"""Мок-backend «Узел 12»: HTTP + WebSocket по контрактам ТЗ v1.0 (разд. 7).

Только для разработки фронтенда. Без PostgreSQL; вход упрощён, но контракт тот же, что у backend:
сессия в cookie, csrf_token в /api/login и /api/me, заголовок X-CSRF-Token на изменяющих запросах,
пакет сбоев — POST /api/incidents/batch {incidents}, план — GET /api/plans/{id} → {plan, stale, applicable}.
Запуск: uvicorn app.main:app --port 8000   (из каталога mock_backend)
     или uvicorn mock_backend.app.main:app --port 8000   (из корня репозитория)
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import os
import secrets
import time
import uuid
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from .model import topology
from .planner import baseline_forecast, plan as run_planner
from .sim import Station

STRATEGIES = ["passenger_first", "earliest_departure"]
TICK = 0.05
HISTORY_STEP_S = 5
HISTORY_KEEP_S = 15 * 60


def _hash(pw: str) -> str:
    return hashlib.sha256(("uzel12:" + pw).encode()).hexdigest()


# Демо-учётки. В настоящем backend хеши берутся из ADMIN_PASSWORD_HASH / DISPATCHER_PASSWORD_HASH.
USERS = {
    "dispatcher": {"role": "dispatcher", "hash": os.environ.get("DISPATCHER_PASSWORD_HASH", _hash("dispatcher"))},
    "admin": {"role": "admin", "hash": os.environ.get("ADMIN_PASSWORD_HASH", _hash("admin"))},
    "viewer": {"role": "viewer", "hash": _hash("viewer")},
}
ROLE_RANK = {"viewer": 0, "dispatcher": 1, "admin": 2}
SESSIONS: dict[str, dict] = {}


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
        self.history: deque = deque()
        self._initial_plan_sync()
        self.remember_history()

    def remember_history(self):
        snap = self.st.snapshot()
        self.history.append(snap)
        while self.history and (self.history[0]["run_id"] != snap["run_id"] or
                                self.history[0]["sim_time_s"] < snap["sim_time_s"] - HISTORY_KEEP_S - 60):
            self.history.popleft()

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
                    if self.st.sim_time_s % HISTORY_STEP_S == 0:
                        self.remember_history()
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
        baseline = baseline_forecast(snap)
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
                    "calc_ms": round((time.monotonic() - t0) * 1000), "plans": plans,
                    "baseline_index": baseline, "based_on_time_s": snap["sim_time_s"]})
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


CSRF_HEADER = "X-CSRF-Token"


def session_of(conn) -> dict | None:
    tok = conn.cookies.get("session")
    return SESSIONS.get(tok) if tok else None


def need(request: Request, role: str):
    u = session_of(request)
    if not u:
        return err(401, "UNAUTHORIZED", "Нужен вход в систему")
    if ROLE_RANK[u["role"]] < ROLE_RANK[role]:
        return err(403, "FORBIDDEN", f"Недостаточно прав: нужна роль «{role}»")
    if request.method not in ("GET", "HEAD", "OPTIONS") and not secrets.compare_digest(
            request.headers.get(CSRF_HEADER, ""), u["csrf_token"]):
        return err(403, "CSRF_FAILED", "Отсутствует или неверен заголовок X-CSRF-Token.")
    return None


def session_info(u: dict) -> dict:
    return {"username": u["username"], "role": u["role"], "csrf_token": u["csrf_token"]}


def check_run(body):
    if body.get("run_id") != hub.st.run_id:
        return err(409, "STALE_RUN", "Команда относится к другому запуску", {"current_run_id": hub.st.run_id})
    return None


@app.get("/health")
async def health():
    return {"status": "ok", "db": "mock (in-memory)", "run_id": hub.st.run_id}


@app.post("/api/login")
async def login(body: dict):
    u = USERS.get(str(body.get("username", "")))
    if not u or not secrets.compare_digest(u["hash"], _hash(str(body.get("password", "")))):
        return err(401, "BAD_CREDENTIALS", "Неверное имя пользователя или пароль")
    tok = secrets.token_urlsafe(24)
    SESSIONS[tok] = {"username": body["username"], "role": u["role"], "csrf_token": secrets.token_urlsafe(24)}
    r = JSONResponse(session_info(SESSIONS[tok]))
    r.set_cookie("session", tok, httponly=True, samesite="lax", max_age=12 * 3600)
    return r


@app.post("/api/logout")
async def logout(request: Request):
    u = session_of(request)
    if u and not secrets.compare_digest(request.headers.get(CSRF_HEADER, ""), u["csrf_token"]):
        return err(403, "CSRF_FAILED", "Отсутствует или неверен заголовок X-CSRF-Token.")
    SESSIONS.pop(request.cookies.get("session", ""), None)
    r = Response(status_code=204)
    r.delete_cookie("session")
    return r


@app.get("/api/me")
async def me(request: Request):
    u = session_of(request)
    return session_info(u) if u else err(401, "UNAUTHORIZED", "Нужен вход в систему")


@app.get("/api/history")
async def history(request: Request, run_id: str, at_s: int):
    if (e := need(request, "viewer")):
        return e
    if run_id != hub.st.run_id:
        return err(409, "STALE_RUN", "История доступна только для текущего запуска")
    cands = [h for h in hub.history if h["sim_time_s"] <= at_s]
    if not cands:
        return err(404, "NO_HISTORY", "Нет сохранённого состояния на этот момент")
    snap = cands[-1]
    return {"schema_version": 1, "snapshot": snap, "requested_at_s": at_s,
            "available_from_s": hub.history[0]["sim_time_s"], "available_to_s": hub.st.sim_time_s}


def _csv_safe(v):
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@app.get("/api/export.csv")
async def export_csv(request: Request, run_id: str):
    if (e := need(request, "viewer")):
        return e
    if run_id != hub.st.run_id:
        return err(409, "STALE_RUN", "Отчёт доступен только для текущего запуска")
    st = hub.st
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["run_id", "sim_time_s", "train_id", "kind", "status", "scheduled_departure_s",
                "actual_departure_s", "forecast_departure_s", "delay_s", "completed_operations", "track_id"])
    for t in st.trains:
        done = [o["kind"] for o in st.ops_of(t["id"]) if o["status"] == "completed"]
        w.writerow([_csv_safe(x) for x in (st.run_id, st.sim_time_s, t["id"], t["kind"], t["status"],
                    t["scheduled_departure_s"], t["actual_departure_s"], t["forecast_departure_s"],
                    t["delay_s"], " ".join(done), t["track_id"])])
    w.writerow([])
    idx = st.index()
    w.writerow(["departed", sum(1 for t in st.trains if t["status"] == "departed")])
    delays = [t["delay_s"] for t in st.trains if t["status"] == "departed"]
    w.writerow(["avg_delay_s", round(sum(delays) / len(delays)) if delays else 0])
    w.writerow(["max_delay_s", max(delays) if delays else 0])
    w.writerow(["conflicts_now", len(st.exec_conflicts) + len(st.plan_conflicts)])
    w.writerow(["index", idx["value"] if idx else ""])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="uzel12_{st.run_id}.csv"'})


@app.get("/api/state")
async def get_state(request: Request):
    if (e := need(request, "viewer")):
        return e
    return {"schema_version": 1, "snapshot": hub.st.snapshot(), "topology": topology(), "clock": hub.clock()}


@app.post("/api/simulation/control")
async def control(body: dict, request: Request):
    if (e := need(request, "dispatcher")):
        return e
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
        hub.history.clear()
        hub.remember_history()
        await hub.broadcast("snapshot", {"snapshot": st.snapshot(), "topology": topology()})
    else:
        return err(422, "BAD_ACTION", "Неизвестное действие")
    await hub.broadcast("clock_sync", hub.clock())
    await hub.publish_state()
    return remember(body, 200, {"ok": True, "run_id": st.run_id, "state_version": st.state_version})


@app.post("/api/incidents")
async def incidents(body: dict, request: Request):
    return await _incidents(body, request, [body])


@app.post("/api/incidents/batch")
async def incidents_batch(body: dict, request: Request):
    items = body.get("incidents")
    if not isinstance(items, list) or not 1 <= len(items) <= 50:
        return err(422, "VALIDATION_ERROR", "Пакет: поле incidents, от 1 до 50 сбоев")
    return await _incidents(body, request, items)


async def _incidents(body: dict, request: Request, items: list):
    if (e := need(request, "dispatcher")):
        return e
    if (c := idem(body)):
        return JSONResponse(status_code=c[0], content=c[1])
    if (e := check_run(body)):
        return e
    results = []
    for it in items:
        status, code, msg = hub.st.incident(it.get("kind"), it.get("target_id"),
                                            int(it.get("duration_s") or 600), int(it.get("delay_s") or 300))
        results.append({"status": status, "code": code, "message": msg, "target_id": it.get("target_id")})
    ok = [r for r in results if r["status"] == 200]
    if not ok:
        r = results[0]
        return remember(body, r["status"], {"code": r["code"], "message": r["message"], "details": {"results": results}})
    hub.remember_history()
    job = await hub.request_replan()  # один пересчёт на пакет
    await hub.publish_state()
    return remember(body, 200, {"command_id": body.get("command_id"), "run_id": hub.st.run_id,
                                "state_version": hub.st.state_version, "replan_required": True,
                                "results": results, "job_id": job})


@app.post("/api/replans", status_code=202)
async def replans(body: dict, request: Request):
    if (e := need(request, "dispatcher")):
        return e
    if (e := check_run(body)):
        return e
    return {"job_id": await hub.request_replan()}


@app.get("/api/plans/{pid}")
async def get_plan(pid: str):
    p = hub.st.plans.get(pid)
    if not p:
        return err(404, "NOT_FOUND", "План не найден")
    stale = p["run_id"] != hub.st.run_id or p["based_on_epoch"] != hub.st.epoch
    return {"plan": p, "stale": stale, "applicable": not stale and p["status"] == "feasible"}  # как PlanResponse


@app.post("/api/plans/{pid}/apply")
async def apply_plan(pid: str, body: dict, request: Request):
    if (e := need(request, "dispatcher")):
        return e
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
    hub.remember_history()
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
    if not session_of(ws):
        await ws.close(code=4401, reason="unauthorized")
        return
    hub.clients[ws] = 0
    await hub.send(ws, "snapshot", {"snapshot": hub.st.snapshot(), "topology": topology()})
    await hub.send(ws, "clock_sync", hub.clock())
    try:
        while True:
            await ws.receive_text()  # клиент ничего не шлёт; держим соединение
    except WebSocketDisconnect:
        hub.clients.pop(ws, None)
