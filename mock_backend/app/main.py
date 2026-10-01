"""Мок-backend «Узел 12»: HTTP + WebSocket по контрактам ТЗ v1.0 (разд. 7).

Только для разработки фронтенда. Без PostgreSQL и аутентификации — их делает И.
Запуск: uvicorn app.main:app --port 8000   (из каталога mock_backend)
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .model import topology
from .planner import plan as run_planner
from .sim import Station

STRATEGIES = ["passenger_first", "earliest_departure"]
TICK = 0.05


class Hub:
    def __init__(self):
        self.st = Station()
        self.clients: dict[WebSocket, int] = {}
        self.cmd_cache: dict[str, tuple[int, dict]] = {}
        self.acc = 0.0
        self.pool = ProcessPoolExecutor(max_workers=1)
        self.job_running: str | None = None
        self.job_pending: str | None = None
        self.last_pub_version = -1
        self.jobs: dict[str, dict] = {}
        self._initial_plan_sync()

    def _initial_plan_sync(self):
        snap = self.st.snapshot()
        best = None
        for s in STRATEGIES:
            p = run_planner(snap, s)
            key = (p["status"] != "feasible", p["metrics"]["unassigned_count"], p["metrics"]["total_delay_s"])
            if best is None or key < best[0]:
                best = (key, p)
        self.st.plans[best[1]["id"]] = best[1]
        self.st.apply_plan(best[1])

    # ---------- рассылка ----------
    async def send(self, ws, type_, payload):
        self.clients[ws] += 1
        env = {"schema_version": 1, "run_id": self.st.run_id, "ws_seq": self.clients[ws],
               "state_version": self.st.state_version, "type": type_, "payload": payload}
        try:
            await ws.send_text(json.dumps(env, ensure_ascii=False))
        except Exception:
            self.clients.pop(ws, None)

    async def broadcast(self, type_, payload):
        for ws in list(self.clients):
            await self.send(ws, type_, payload)

    async def publish_state(self):
        self.last_pub_version = self.st.state_version
        await self.broadcast("state_updated", {"snapshot": self.st.snapshot()})

    def clock(self):
        return {"sim_time_s": self.st.sim_time_s, "sim_time_exact": self.st.sim_time_s + self.acc,
                "speed": self.st.speed, "paused": self.st.paused, "server_mono_ms": round(time.monotonic() * 1000)}

    # ---------- циклы ----------
    async def sim_loop(self):
        while True:
            await asyncio.sleep(TICK)
            if not self.st.paused:
                self.acc += TICK * self.st.speed
                while self.acc >= 1:
                    self.acc -= 1
                    self.st.step()
            if self.st.state_version != self.last_pub_version:
                await self.publish_state()

    async def clock_loop(self):
        while True:
            await asyncio.sleep(1)
            await self.broadcast("clock_sync", self.clock())

    # ---------- пересчёт ----------
    async def request_replan(self) -> str:
        job_id = "JOB-" + uuid.uuid4().hex[:6]
        self.jobs[job_id] = {"id": job_id, "status": "queued"}
        if self.job_running:
            if self.job_pending:  # объединяем устаревшие запросы
                self.jobs[self.job_pending]["status"] = "merged"
            self.job_pending = job_id
        else:
            asyncio.create_task(self._run_job(job_id))
        return job_id

    async def _run_job(self, job_id):
        self.job_running = job_id
        self.jobs[job_id]["status"] = "running"
        await self.broadcast("replan_started", {"job_id": job_id})
        snap = self.st.snapshot()
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        try:
            plans = []
            for s in STRATEGIES:
                p = await loop.run_in_executor(self.pool, run_planner, snap, s, 2.0)
                plans.append(p)
            if snap["run_id"] == self.st.run_id:
                same = _same(plans[0], plans[1])
                for p in plans:
                    p["identical_to_other"] = same
                    self.st.plans[p["id"]] = p
                self.jobs[job_id] |= {"status": "done", "plan_ids": [p["id"] for p in plans]}
                await self.broadcast("replan_finished", {
                    "job_id": job_id, "plan_ids": [p["id"] for p in plans], "identical": same,
                    "calc_ms": round((time.monotonic() - t0) * 1000), "plans": plans})
        except Exception as e:  # noqa
            self.jobs[job_id]["status"] = "failed"
            await self.broadcast("replan_failed", {"job_id": job_id, "message": str(e)})
        finally:
            self.job_running = None
            if self.job_pending:
                nxt, self.job_pending = self.job_pending, None
                asyncio.create_task(self._run_job(nxt))


def _same(a, b):
    key = lambda p: sorted((x["operation_id"], x["start_s"], x["track_id"], tuple(x["resource_ids"])) for x in p["assignments"])
    return key(a) == key(b)


hub: Hub


@asynccontextmanager
async def lifespan(_app):
    global hub
    hub = Hub()
    tasks = [asyncio.create_task(hub.sim_loop()), asyncio.create_task(hub.clock_loop())]
    yield
    for t in tasks:
        t.cancel()
    hub.pool.shutdown(cancel_futures=True)


app = FastAPI(title="Узел 12 — мок API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def err(status, code, message, details=None):
    return JSONResponse(status_code=status, content={"code": code, "message": message, "details": details or {}})


def idem(body):
    cid = body.get("command_id")
    return hub.cmd_cache.get(cid) if cid else None


def remember(body, status, content):
    if body.get("command_id"):
        hub.cmd_cache[body["command_id"]] = (status, content)
    return JSONResponse(status_code=status, content=content)


def check_run(body):
    if body.get("run_id") != hub.st.run_id:
        return err(409, "STALE_RUN", "Команда относится к другому запуску", {"current_run_id": hub.st.run_id})
    return None


@app.get("/health")
async def health():
    return {"status": "ok", "db": "mock (in-memory)", "run_id": hub.st.run_id}


@app.post("/api/login")
async def login(body: dict):
    role = {"dispatcher": "dispatcher", "admin": "admin"}.get(body.get("username"), "viewer")
    return {"username": body.get("username"), "role": role, "mock": True}


@app.get("/api/state")
async def get_state():
    return {"schema_version": 1, "snapshot": hub.st.snapshot(), "topology": topology(), "clock": hub.clock()}


@app.post("/api/simulation/control")
async def control(body: dict):
    if (c := idem(body)):
        return JSONResponse(status_code=c[0], content=c[1])
    if (e := check_run(body)) and body.get("action") != "reset":
        return e
    st, a = hub.st, body.get("action")
    if a == "start":
        if not st.active_plan or st.active_plan["status"] not in ("feasible", "partial"):
            return err(409, "NO_PLAN", "Без допустимого плана запуск запрещён")
        if st.sim_time_s == 0 and st.paused:
            st.paused = False
            st.process()
        st.paused = False
    elif a == "pause":
        st.paused = True
    elif a == "speed":
        if body.get("speed") not in (1, 5, 10):
            return err(422, "BAD_SPEED", "Скорость: 1, 5 или 10")
        st.speed = body["speed"]
    elif a == "reset":
        st.reset()
        hub.acc = 0
        hub._initial_plan_sync()
        await hub.broadcast("snapshot", {"snapshot": st.snapshot(), "topology": topology()})
    else:
        return err(422, "BAD_ACTION", "Неизвестное действие")
    await hub.broadcast("clock_sync", hub.clock())
    await hub.publish_state()
    return remember(body, 200, {"ok": True, "run_id": st.run_id, "state_version": st.state_version})


@app.post("/api/incidents")
async def incidents(body: dict):
    if (c := idem(body)):
        return JSONResponse(status_code=c[0], content=c[1])
    if (e := check_run(body)):
        return e
    items = body.get("batch") or [body]
    results = []
    for it in items:
        status, code, msg = hub.st.incident(it.get("kind"), it.get("target_id"),
                                            int(it.get("duration_s") or 600), int(it.get("delay_s") or 300))
        results.append({"status": status, "code": code, "message": msg, "target_id": it.get("target_id")})
    ok = [r for r in results if r["status"] == 200]
    if not ok:
        r = results[0]
        return remember(body, r["status"], {"code": r["code"], "message": r["message"], "details": {"results": results}})
    job = await hub.request_replan()  # один пересчёт на пакет
    await hub.publish_state()
    return remember(body, 200, {"ok": True, "results": results, "job_id": job, "state_version": hub.st.state_version})


@app.post("/api/replans", status_code=202)
async def replans(body: dict):
    if (e := check_run(body)):
        return e
    return {"job_id": await hub.request_replan()}


@app.get("/api/plans/{pid}")
async def get_plan(pid: str):
    p = hub.st.plans.get(pid)
    return p if p else err(404, "NOT_FOUND", "План не найден")


@app.post("/api/plans/{pid}/apply")
async def apply_plan(pid: str, body: dict):
    if (c := idem(body)):
        return JSONResponse(status_code=c[0], content=c[1])
    if (e := check_run(body)):
        return e
    p = hub.st.plans.get(pid)
    if not p:
        return err(404, "NOT_FOUND", "План не найден")
    if p["run_id"] != hub.st.run_id:
        return err(409, "STALE_PLAN", "План относится к старому запуску")
    if p["based_on_epoch"] != hub.st.epoch:
        return err(409, "STALE_PLAN", "После расчёта изменилась обстановка (сбой или другой план). Нужен пересчёт.",
                   {"plan_epoch": p["based_on_epoch"], "current_epoch": hub.st.epoch})
    if p["status"] != "feasible":
        return err(409, "PLAN_NOT_FEASIBLE", "Неполный или недопустимый план нельзя принять обычной кнопкой")
    if (bad := hub.st.plan_conflicts_with_started(p)):
        return err(409, "STALE_PLAN", "Пока план рассматривался, операции начались иначе. Нужен пересчёт.",
                   {"operation_ids": bad})
    hub.st.apply_plan(p)
    await hub.publish_state()
    return remember(body, 200, {"ok": True, "active_plan_id": pid, "state_version": hub.st.state_version})


@app.get("/api/config")
async def get_config():
    from .sim import WEIGHTS
    return {"index_weights": WEIGHTS, "window_s": 900, "thresholds": {"norm": 80, "attention": 50},
            "strategies": STRATEGIES}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    hub.clients[ws] = 0
    await hub.send(ws, "snapshot", {"snapshot": hub.st.snapshot(), "topology": topology()})
    await hub.send(ws, "clock_sync", hub.clock())
    try:
        while True:
            await ws.receive_text()  # клиент ничего не шлёт; держим соединение
    except WebSocketDisconnect:
        hub.clients.pop(ws, None)
