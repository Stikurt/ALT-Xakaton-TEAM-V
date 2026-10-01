"""Мок-симулятор станции: пошаговое исполнение принятого плана с проверкой ресурсов (ТЗ разд. 4).

Шаг = 1 модельная секунда. Порядок на одной отметке: завершения → истечение сбоев → прибытия → попытки начала.
Настоящий движок делает В; этот модуль нужен, чтобы фронтенд жил на реалистичных данных.
"""
from __future__ import annotations

import copy
import uuid
from collections import deque

from .model import CONFIG, ROUTE_BY_ID, build_operations
from .planner import index_from_penalties

WAIT_TEXT = {
    "TRACK_CLOSED": "путь {x} закрыт",
    "TRACK_OCCUPIED": "путь {x} занят",
    "ROUTE_BUSY": "горловина {x} занята",
    "RESOURCE_UNAVAILABLE": "ресурс {x} недоступен",
    "RESOURCE_BUSY": "ресурс {x} занят другой операцией",
    "NO_FEASIBLE_SLOT": "нет назначения в принятом плане",
}
INDEX_W = 900
WEIGHTS = {"delay": 0.35, "on_time": 0.25, "utilization": 0.15, "conflicts": 0.15, "idle": 0.10}
FACTOR_LABEL = {"delay": "Задержка отправления", "on_time": "Выполнение отправлений",
                "utilization": "Перегрузка путей", "conflicts": "Конфликты плана", "idle": "Простой из-за ожидания"}


class Station:
    def __init__(self):
        self.reset()

    # ---------- жизненный цикл ----------
    def reset(self):
        self.run_id = "RUN-" + uuid.uuid4().hex[:6]
        self.sim_time_s = 0
        self.speed = 1
        self.paused = True
        self.state_version = 1
        self.epoch = 1
        self.seq = 0
        self.events: list[dict] = []
        self.trains = []
        for t in CONFIG["trains"]:
            self.trains.append({**t, "expected_arrival_s": t["scheduled_arrival_s"], "status": "scheduled",
                                "track_id": None, "movement": None, "current_operation_id": None,
                                "wait_reason": None, "forecast_departure_s": None, "delay_s": 0,
                                "actual_departure_s": None})
        self.tracks = [{"id": t["id"], "kind": t["kind"], "usable_length_m": t["usable_length_m"],
                        "availability": "open", "closed_until_s": None, "occupant_train_id": None}
                       for t in CONFIG["tracks"]]
        self.resources = [{"id": r["id"], "kind": r["kind"], "capabilities": r["capabilities"],
                           "availability": "available", "unavailable_until_s": None, "active_operation_id": None}
                          for r in CONFIG["resources"]]
        self.zones = {"GW": None, "GE": None}
        self.operations = []
        for t in self.trains:
            self.operations += build_operations(t)
        self.active_plan = None
        self.plans: dict[str, dict] = {}
        self.exec_conflicts: dict[str, dict] = {}
        self.plan_conflicts: dict[str, dict] = {}
        self.samples = deque(maxlen=INDEX_W)
        self.incidents: list[dict] = []
        self.emit("run_reset", self.run_id, {})

    # ---------- утилиты ----------
    def T(self, tid):
        return next(t for t in self.trains if t["id"] == tid)

    def TR(self, tid):
        return next(t for t in self.tracks if t["id"] == tid)

    def R(self, rid):
        return next(r for r in self.resources if r["id"] == rid)

    def ops_of(self, tid):
        return [o for o in self.operations if o["train_id"] == tid]

    def emit(self, type_, entity_id, payload):
        self.seq += 1
        ev = {"event_id": f"{self.run_id}-{self.seq}", "run_id": self.run_id, "seq": self.seq,
              "sim_time_s": self.sim_time_s, "type": type_, "entity_id": entity_id, "payload": payload}
        self.events.append(ev)
        return ev

    def asg(self, op_id):
        if not self.active_plan:
            return None
        return self.active_plan["_by_op"].get(op_id)

    # ---------- план ----------
    def plan_conflicts_with_started(self, plan):
        """Операции, начавшиеся после расчёта плана иначе, чем в нём записано."""
        if not self.active_plan:
            return []
        new = {a["operation_id"]: a for a in plan["assignments"]}
        bad = []
        for op in self.operations:
            if op["status"] == "pending":
                continue
            o, n = self.asg(op["id"]), new.get(op["id"])
            if o and n and (o["track_id"], o["route_id"], sorted(o["resource_ids"])) != \
                    (n["track_id"], n["route_id"], sorted(n["resource_ids"])):
                bad.append(op["id"])
        return bad

    def apply_plan(self, plan):
        p = copy.deepcopy(plan)
        if self.active_plan:  # начатые операции сохраняют фактические назначения
            for i, a in enumerate(p["assignments"]):
                op = next(o for o in self.operations if o["id"] == a["operation_id"])
                if op["status"] != "pending" and self.asg(op["id"]):
                    p["assignments"][i] = copy.deepcopy(self.asg(op["id"]))
        p["_by_op"] = {a["operation_id"]: a for a in p["assignments"]}
        p["status_applied"] = "active"
        self.active_plan = p
        self.epoch += 1
        self.state_version += 1
        self.emit("plan_applied", plan["id"], {"strategy": plan["strategy"]})
        self.recompute()

    # ---------- шаг ----------
    def step(self) -> bool:
        self.sim_time_s += 1
        return self.process()

    def process(self) -> bool:
        """Обработать текущую отметку sim_time_s (вызывается и на t=0 при старте)."""
        t = self.sim_time_s
        changed = False
        # 1. завершения
        for op in self.operations:
            if op["status"] == "running" and op["actual_start_s"] + op["duration_s"] <= t:
                self._complete(op)
                changed = True
        # 2. истечение сбоев
        for tr in self.tracks:
            if tr["closed_until_s"] and tr["closed_until_s"] <= t:
                tr["availability"], tr["closed_until_s"] = "open", None
                self.emit("track_reopened", tr["id"], {})
                changed = True
        for r in self.resources:
            if r["unavailable_until_s"] and r["unavailable_until_s"] <= t:
                r["availability"], r["unavailable_until_s"] = "available", None
                self.emit("resource_restored", r["id"], {})
                changed = True
        # 3. прибытия к W
        for tr in self.trains:
            if tr["status"] == "scheduled" and tr["expected_arrival_s"] <= t:
                tr["status"] = "waiting_entry"
                self.emit("train_at_border", tr["id"], {})
                changed = True
        # 4. попытки начала
        cands = []
        for tr in self.trains:
            if tr["status"] in ("scheduled", "departed"):
                continue
            ops = self.ops_of(tr["id"])
            if any(o["status"] == "running" for o in ops):
                continue
            nxt = next((o for o in ops if o["status"] == "pending"), None)
            if not nxt:
                continue
            a = self.asg(nxt["id"])
            start_at = a["start_s"] if a else 0
            if nxt["kind"] == "departure":
                start_at = max(start_at, tr["scheduled_departure_s"] - nxt["duration_s"])
            if t >= start_at:
                cands.append((start_at, tr["id"], nxt, a))
        cands.sort(key=lambda c: (c[0], c[1]))
        new_conf = {}
        for _, tid, op, a in cands:
            ok, code, x = self._can_start(self.T(tid), op, a)
            if ok:
                self._start(self.T(tid), op, a)
                changed = True
            else:
                txt = WAIT_TEXT[code].format(x=x)
                if op["wait_reason"] != txt:
                    op["wait_reason"] = txt
                    changed = True
                cid = f"{code}:{op['id']}"
                new_conf[cid] = {"id": cid, "code": code, "severity": "high", "kind": "execution",
                                 "entity_ids": [tid] + ([x] if x else []), "operation_ids": [op["id"]],
                                 "start_s": self.exec_conflicts.get(cid, {}).get("start_s", t), "end_s": None,
                                 "message": f"{tid}: {self._op_label(op)} ждёт — {txt}"}
        if set(new_conf) != set(self.exec_conflicts):
            changed = True
        self.exec_conflicts = new_conf
        self._sample()
        if changed:
            self.state_version += 1
            self.recompute()
        return changed

    def _op_label(self, op):
        return {"arrival": "приём", "departure": "отправление", "shunt": "маневр", "stop": "стоянка",
                "inspection": "осмотр", "preparation": "подготовка", "cargo": "грузовая обработка",
                "formation": "формирование"}[op["kind"]]

    def _can_start(self, tr, op, a):
        if a is None:
            return False, "NO_FEASIBLE_SLOT", None
        for rid in a["resource_ids"]:
            r = self.R(rid)
            if r["availability"] != "available":
                return False, "RESOURCE_UNAVAILABLE", rid
            if r["active_operation_id"]:
                return False, "RESOURCE_BUSY", rid
        if op["is_move"]:
            route = ROUTE_BY_ID[a["route_id"]]
            for z in route["conflict_zone_ids"]:
                if self.zones[z]:
                    return False, "ROUTE_BUSY", z
            if op["is_move"] != "departure":
                dst = self.TR(route["to_id"])
                if dst["availability"] == "closed":
                    return False, "TRACK_CLOSED", dst["id"]
                if dst["occupant_train_id"] not in (None, tr["id"]):
                    return False, "TRACK_OCCUPIED", dst["id"]
        return True, None, None

    def _start(self, tr, op, a):
        t = self.sim_time_s
        op["status"], op["actual_start_s"], op["wait_reason"] = "running", t, None
        for rid in a["resource_ids"]:
            self.R(rid)["active_operation_id"] = op["id"]
        tr["current_operation_id"] = op["id"]
        if op["is_move"]:
            route = ROUTE_BY_ID[a["route_id"]]
            for z in route["conflict_zone_ids"]:
                self.zones[z] = op["id"]
            if op["is_move"] != "departure":
                self.TR(route["to_id"])["occupant_train_id"] = tr["id"]
            tr["movement"] = {"route_id": route["id"], "started_at_s": t, "expected_end_at_s": t + op["duration_s"]}
            tr["status"] = "moving"
        self.emit("operation_started", op["id"], {"train_id": tr["id"], "kind": op["kind"]})

    def _complete(self, op):
        t = op["actual_start_s"] + op["duration_s"]
        op["status"], op["actual_end_s"] = "completed", t
        tr = self.T(op["train_id"])
        a = self.asg(op["id"])
        for rid in (a["resource_ids"] if a else []):
            if self.R(rid)["active_operation_id"] == op["id"]:
                self.R(rid)["active_operation_id"] = None
        tr["current_operation_id"] = None
        if op["is_move"]:
            route = ROUTE_BY_ID[a["route_id"]]
            for z in route["conflict_zone_ids"]:
                if self.zones[z] == op["id"]:
                    self.zones[z] = None
            if tr["track_id"]:
                src = self.TR(tr["track_id"])
                if src["occupant_train_id"] == tr["id"]:
                    src["occupant_train_id"] = None
            tr["movement"] = None
            if op["is_move"] == "departure":
                tr["status"], tr["track_id"], tr["actual_departure_s"] = "departed", None, t
            else:
                tr["status"], tr["track_id"] = "on_track", route["to_id"]
        self.emit("operation_completed", op["id"], {"train_id": tr["id"], "kind": op["kind"]})

    # ---------- сбои ----------
    def incident(self, kind, target_id, duration_s=600, delay_s=300):
        t = self.sim_time_s
        if kind == "delay":
            tr = next((x for x in self.trains if x["id"] == target_id), None)
            if not tr:
                return 422, "NOT_FOUND", "Поезд не найден"
            if tr["status"] != "scheduled":
                return 409, "INCOMPATIBLE", f"{target_id} уже прибыл к станции — опоздание вносится только для scheduled"
            tr["expected_arrival_s"] += delay_s
            msg = f"Опоздание {target_id} на {delay_s} с"
        elif kind == "close_track":
            tr = next((x for x in self.tracks if x["id"] == target_id), None)
            if not tr:
                return 422, "NOT_FOUND", "Путь не найден"
            for o in self.operations:
                a = self.asg(o["id"])
                if o["status"] == "running" and o["is_move"] and a and ROUTE_BY_ID[a["route_id"]]["to_id"] == target_id:
                    return 409, "INCOMPATIBLE", f"На {target_id} уже идёт движение — закрытие в этот момент отклоняется"
            tr["availability"], tr["closed_until_s"] = "closed", t + duration_s
            msg = f"Закрыт {target_id} до {t + duration_s} с"
        elif kind == "loco_unavailable":
            r = next((x for x in self.resources if x["id"] == target_id), None)
            if not r or r["kind"] != "shunting_loco":
                return 422, "NOT_FOUND", "Локомотив не найден"
            if r["active_operation_id"]:
                return 409, "INCOMPATIBLE", f"{target_id} занят операцией — поломка в движении вне первой версии"
            r["availability"], r["unavailable_until_s"] = "unavailable", t + duration_s
            msg = f"{target_id} недоступен до {t + duration_s} с"
        else:
            return 422, "BAD_KIND", "Неизвестный вид сбоя"
        self.incidents.append({"kind": kind, "target_id": target_id, "at_s": t, "message": msg})
        self.epoch += 1
        self.state_version += 1
        self.emit("incident", target_id, {"kind": kind, "message": msg})
        self.recompute()
        return 200, "OK", msg

    # ---------- производные величины ----------
    def recompute(self):
        """Прогноз отправлений и плановые конфликты (из принятого плана и текущих ограничений)."""
        t = self.sim_time_s
        for tr in self.trains:
            est = t
            fc = None
            for op in self.ops_of(tr["id"]):
                if op["status"] == "completed":
                    est = op["actual_end_s"]
                elif op["status"] == "running":
                    est = op["actual_start_s"] + op["duration_s"]
                else:
                    a = self.asg(op["id"])
                    s = max(est, t, a["start_s"] if a else t)
                    if op["kind"] == "arrival":
                        s = max(s, tr["expected_arrival_s"])
                    if op["kind"] == "departure":
                        s = max(s, tr["scheduled_departure_s"] - op["duration_s"])
                    est = s + op["duration_s"]
                if op["kind"] == "departure":
                    fc = op["actual_end_s"] if op["status"] == "completed" else est
            tr["forecast_departure_s"] = fc
            tr["delay_s"] = max(0, (fc or 0) - tr["scheduled_departure_s"])
            nxt = next((o for o in self.ops_of(tr["id"]) if o["status"] != "completed"), None)
            tr["wait_reason"] = nxt["wait_reason"] if nxt and nxt["status"] == "pending" else None
        self.plan_conflicts = {}
        closed = {x["id"]: x["closed_until_s"] for x in self.tracks if x["closed_until_s"]}
        unav = {x["id"]: x["unavailable_until_s"] for x in self.resources if x["unavailable_until_s"]}
        for op in self.operations:
            if op["status"] != "pending":
                continue
            a = self.asg(op["id"])
            if not a:
                continue
            if op["is_move"] in ("arrival", "shunt") and a["track_id"] in closed and a["start_s"] < closed[a["track_id"]]:
                cid = f"TRACK_CLOSED:{op['id']}"
                if f"TRACK_CLOSED:{op['id']}" not in self.exec_conflicts:
                    self.plan_conflicts[cid] = {"id": cid, "code": "TRACK_CLOSED", "severity": "medium", "kind": "plan",
                                                "entity_ids": [op["train_id"], a["track_id"]], "operation_ids": [op["id"]],
                                                "start_s": a["start_s"], "end_s": closed[a["track_id"]],
                                                "message": f"{op['train_id']}: по плану {self._op_label(op)} на {a['track_id']} в {a['start_s']} с, но путь закрыт до {closed[a['track_id']]} с"}
            for rid in a["resource_ids"]:
                if rid in unav and a["start_s"] < unav[rid]:
                    cid = f"RESOURCE_UNAVAILABLE:{op['id']}"
                    if cid not in self.exec_conflicts:
                        self.plan_conflicts[cid] = {"id": cid, "code": "RESOURCE_UNAVAILABLE", "severity": "medium",
                                                    "kind": "plan", "entity_ids": [op["train_id"], rid],
                                                    "operation_ids": [op["id"]], "start_s": a["start_s"], "end_s": unav[rid],
                                                    "message": f"{op['train_id']}: по плану {self._op_label(op)} с {rid} в {a['start_s']} с, но {rid} недоступен до {unav[rid]} с"}

    def _sample(self):
        main = [x for x in self.tracks if x["id"] != "P12"]
        open_ = [x for x in main if x["availability"] == "open"]
        occ = sum(1 for x in open_ if x["occupant_train_id"])
        active = [x for x in self.trains if x["status"] in ("waiting_entry", "moving", "on_track")]
        blocked = sum(1 for x in active if any(o["wait_reason"] for o in self.ops_of(x["id"]) if o["status"] == "pending"))
        self.samples.append((occ, len(open_), blocked, len(active)))

    def index(self):
        t = self.sim_time_s
        w0 = max(0, t - INDEX_W)
        p = {}
        delays = []
        for tr in self.trains:
            if tr["status"] == "departed" and tr["actual_departure_s"] >= w0:
                delays.append(max(0, tr["actual_departure_s"] - tr["scheduled_departure_s"]))
            elif tr["status"] != "departed" and tr["scheduled_departure_s"] < t:
                delays.append(t - tr["scheduled_departure_s"])
        p["delay"] = min(sum(delays) / len(delays) / 600, 1) if delays else None
        due = [tr for tr in self.trains if w0 <= tr["scheduled_departure_s"] <= t]
        if due:
            ok = sum(1 for tr in due if tr["status"] == "departed" and tr["actual_departure_s"] <= tr["scheduled_departure_s"])
            p["on_time"] = 1 - ok / len(due)
        else:
            p["on_time"] = None
        if self.samples:
            us = [o / n if n else 1 for o, n, _, _ in self.samples]
            U = sum(us) / len(us)
            p["utilization"] = min(max((U - 0.75) / 0.25, 0), 1)
            act = sum(a for *_, a in self.samples)
            p["idle"] = min(sum(b for _, _, b, _ in self.samples) / act, 1) if act else None
        else:
            p["utilization"] = p["idle"] = None
        C = len(self.exec_conflicts) + len(self.plan_conflicts)
        p["conflicts"] = min(C / 5, 1)
        return index_from_penalties(p, min(INDEX_W, t), "fact")

    # ---------- снимок ----------
    def snapshot(self) -> dict:
        ap = None
        if self.active_plan:
            ap = {k: v for k, v in self.active_plan.items() if not k.startswith("_")}
        conflicts = list(self.exec_conflicts.values()) + list(getattr(self, "plan_conflicts", {}).values())
        return {
            "schema_version": 1, "run_id": self.run_id, "state_version": self.state_version, "epoch": self.epoch,
            "last_seq": self.seq, "sim_time_s": self.sim_time_s, "speed": self.speed, "paused": self.paused,
            "trains": copy.deepcopy(self.trains), "tracks": copy.deepcopy(self.tracks),
            "resources": copy.deepcopy(self.resources),
            "zones": [{"id": k, "active_operation_id": v} for k, v in self.zones.items()],
            "operations": [{k: v for k, v in o.items() if k != "groups"} | {"groups": o["groups"]} for o in self.operations],
            "active_plan_id": self.active_plan["id"] if self.active_plan else None,
            "active_plan": ap, "conflicts": conflicts,
            "queue": [t["id"] for t in self.trains if t["status"] == "waiting_entry"],
            "index": self.index(), "incidents": self.incidents[-10:],
        }
