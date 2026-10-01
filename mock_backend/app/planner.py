"""Мок-планировщик: детерминированная жадная эвристика с ресурсными календарями (ТЗ разд. 14).

Чистая функция plan(snapshot, strategy, budget_s) -> Plan dict. Работает в отдельном процессе.
Это заглушка для фронтенда; настоящий модуль делает Н.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict

from .model import GROUPS, ROUTE_BY_ID, stages_for

INF = 10**9
HORIZON_EXTRA = 4 * 7200


class Calendar:
    def __init__(self):
        self.busy: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        self.closures: dict[str, list[tuple[int, int]]] = defaultdict(list)

    def add(self, res, s, e, owner):
        if e > s:
            self.busy[res].append((s, e, owner))

    def remove_owner(self, res, owner):
        self.busy[res] = [b for b in self.busy[res] if b[2] != owner]

    def overlaps(self, res, s, e, owner):
        return [b for b in self.busy[res] if b[2] != owner and b[0] < e and s < b[1]]

    def free(self, res, s, e, owner):
        return not self.overlaps(res, s, e, owner)

    def conflict_end(self, res, s, e, owner):
        ov = self.overlaps(res, s, e, owner)
        return max(b[1] for b in ov) if ov else s

    def closure_end(self, track, s):
        for cs, ce in self.closures[track]:
            if cs <= s < ce:
                return ce
        return None


def _find_slot(cal, t, dur, singles, groups, owner, track_in=None, limit=None):
    s = t
    for _ in range(400):
        if limit is not None and s > limit:
            return None
        nxt, ok, chosen = s, True, []
        for r in singles:
            if not cal.free(r, s, s + dur, owner):
                ok, nxt = False, max(nxt, cal.conflict_end(r, s, s + dur, owner))
        if track_in:
            ce = cal.closure_end(track_in, s)
            if ce is not None:
                ok, nxt = False, max(nxt, ce)
            if not cal.free(track_in, s, s + dur, owner):
                ok, nxt = False, max(nxt, cal.conflict_end(track_in, s, s + dur, owner))
        for g in groups:
            members = GROUPS[g]
            cand = [r for r in members if cal.free(r, s, s + dur, owner)]
            if cand:
                chosen.append(cand[0])
            else:
                ok = False
                nxt = max(nxt, min(cal.conflict_end(r, s, s + dur, owner) for r in members))
        if ok:
            return s, chosen
        s = nxt if nxt > s else s + 1
    return None


def _asg(op, s, track, route, res, fixed=False):
    return {"operation_id": op["id"], "train_id": op["train_id"], "kind": op["kind"],
            "start_s": s, "end_s": s + op["duration_s"], "track_id": track, "route_id": route,
            "resource_ids": list(res), "fixed": fixed}


class _Budget(Exception):
    pass


def _plan_train(cal, train, ops, start_stage, t_ready, prev_track, prev_hs, limit, counter):
    """Строит оставшуюся цепочку с start_stage. Возвращает (assignments, dep_end) или None."""
    stages = stages_for(train["kind"])
    owner = train["id"]
    by_stage = defaultdict(list)
    for op in ops:
        by_stage[op["stage"]].append(op)
    n = len(stages)

    def solve(i, t, prev, prev_hs):
        counter[0] += 1
        if counter[0] > 6000:
            raise _Budget()
        if i == n:
            dep = by_stage[n][0]
            t0 = max(t, train["scheduled_departure_s"] - dep["duration_s"])
            r = _find_slot(cal, t0, dep["duration_s"], ["GE"], [], owner, limit=limit)
            if not r:
                return None
            s, _ = r
            if prev and not cal.free(prev, prev_hs, s + dep["duration_s"], owner):
                return None
            return [_asg(dep, s, prev, f"R_{prev}_E", [])], s + dep["duration_s"]
        mv, inner = by_stage[i][0], by_stage[i][1:]
        best = None
        for X in stages[i]["tracks"]:
            if X == prev or f"R_{prev or 'W'}_{X}" not in ROUTE_BY_ID:
                continue
            tt = t
            for _ in range(25):
                r = _find_slot(cal, tt, mv["duration_s"], ["GW"], mv["groups"], owner, track_in=X, limit=limit)
                if not r:
                    break
                s, ch = r
                if prev and not cal.free(prev, prev_hs, s + mv["duration_s"], owner):
                    break
                route = f"R_{prev or 'W'}_{X}"
                asgs = [_asg(mv, s, X, route, ch)]
                cur, fail = s + mv["duration_s"], False
                for op in inner:
                    r2 = _find_slot(cal, cur, op["duration_s"], [], op["groups"], owner, limit=limit)
                    if not r2:
                        fail = True
                        break
                    s2, ch2 = r2
                    asgs.append(_asg(op, s2, X, None, ch2))
                    cur = s2 + op["duration_s"]
                if fail:
                    break
                sub = solve(i + 1, cur, X, s)
                if sub is None:
                    later = [b for b in cal.busy[X] if b[2] != owner and b[0] >= s]
                    if not later:
                        break
                    tt = min(later, key=lambda b: b[0])[1]
                    continue
                sub_asgs, dep_end = sub
                e_out = sub_asgs[0]["start_s"] + by_stage[i + 1][0]["duration_s"]
                if not cal.free(X, s, e_out, owner):
                    tt = cal.conflict_end(X, s, e_out, owner)
                    continue
                cand = (dep_end, X, asgs + sub_asgs)
                if best is None or cand[:2] < best[:2]:
                    best = cand
                break
        return (best[2], best[0]) if best else None

    if start_stage == "internal":
        return solve  # используется вызывающим кодом
    return solve(start_stage, t_ready, prev_track, prev_hs)


def _holds(asgs):
    """Удержание путей: от начала входящего перемещения до конца исходящего."""
    holds, cur = [], None
    for a in asgs:
        if a["kind"] in ("arrival", "shunt", "departure"):
            if cur is not None:
                holds.append((cur[0], cur[1], a["end_s"]))
            cur = (a["track_id"], a["start_s"]) if a["kind"] != "departure" else None
    if cur is not None:
        holds.append((cur[0], cur[1], INF))
    return holds


def _book(cal, train_id, asgs):
    for a in asgs:
        for r in a["resource_ids"]:
            cal.add(r, a["start_s"], a["end_s"], train_id)
        route = ROUTE_BY_ID.get(a["route_id"]) if a["route_id"] else None
        if route:
            for z in route["conflict_zone_ids"]:
                cal.add(z, a["start_s"], a["end_s"], train_id)
    for tr, s, e in _holds(asgs):
        cal.add(tr, s, e, train_id)


def _occupied(tr):
    out = [tr["track_id"]] if tr["track_id"] else []
    mv = tr.get("movement")
    if mv:
        to = ROUTE_BY_ID[mv["route_id"]]["to_id"]
        if to.startswith("P"):
            out.append(to)
    return out


def _order_key(strategy, tr):
    if strategy == "passenger_first":
        return (-tr["priority"], tr["scheduled_departure_s"], tr["id"])
    return (tr["scheduled_departure_s"], -tr["priority"], tr["id"])


def plan(snapshot: dict, strategy: str = "passenger_first", budget_s: float = 2.0) -> dict:
    t_start = time.monotonic()
    now = snapshot["sim_time_s"]
    limit = now + HORIZON_EXTRA
    cal = Calendar()
    for tr in snapshot["tracks"]:
        if tr["closed_until_s"] and tr["closed_until_s"] > now:
            cal.closures[tr["id"]].append((now, tr["closed_until_s"]))
    for r in snapshot["resources"]:
        if r["unavailable_until_s"] and r["unavailable_until_s"] > now:
            cal.add(r["id"], now, r["unavailable_until_s"], "INCIDENT")

    active = {a["operation_id"]: a for a in (snapshot.get("active_plan") or {}).get("assignments", [])}
    ops_by_train = defaultdict(list)
    for op in snapshot["operations"]:
        ops_by_train[op["train_id"]].append(op)

    fixed_asgs: dict[str, list] = {}
    for tr in snapshot["trains"]:
        fx = []
        for op in ops_by_train[tr["id"]]:
            if op["status"] in ("running", "completed"):
                a = dict(active.get(op["id"]) or _asg(op, op["actual_start_s"], tr["track_id"], None, []))
                a["start_s"] = op["actual_start_s"]
                a["end_s"] = op["actual_start_s"] + op["duration_s"]
                a["fixed"] = True
                fx.append(a)
        fixed_asgs[tr["id"]] = fx
        for a in fx:
            for r in a["resource_ids"]:
                cal.add(r, a["start_s"], a["end_s"], tr["id"])
            route = ROUTE_BY_ID.get(a["route_id"]) if a["route_id"] else None
            if route:
                for z in route["conflict_zone_ids"]:
                    cal.add(z, a["start_s"], a["end_s"], tr["id"])
        if tr["status"] != "departed":
            for tid_ in _occupied(tr):
                cal.add(tid_, now, INF, tr["id"])  # временный блок до планирования поезда

    on_station = [t for t in snapshot["trains"] if t["status"] in ("on_track", "moving")]
    outside = [t for t in snapshot["trains"] if t["status"] in ("scheduled", "waiting_entry")]
    order = sorted(on_station, key=lambda t: _order_key(strategy, t)) + \
        sorted(outside, key=lambda t: _order_key(strategy, t))

    assignments, unassigned, timed_out = [], [], False
    for tr in snapshot["trains"]:
        if tr["status"] == "departed" or all(o["status"] != "pending" for o in ops_by_train[tr["id"]]):
            assignments += fixed_asgs[tr["id"]]
    for tr in order:
        ops = ops_by_train[tr["id"]]
        pending = [o for o in ops if o["status"] == "pending"]
        if not pending:
            continue
        if time.monotonic() - t_start > budget_s:
            timed_out = True
            unassigned.append({"train_id": tr["id"], "code": "NO_FEASIBLE_SLOT",
                               "message": f"{tr['id']}: не хватило бюджета расчёта"})
            continue
        counter = [0]
        res = None
        try:
            if tr["status"] in ("scheduled", "waiting_entry"):
                t0 = max(now, tr["expected_arrival_s"])
                res = _plan_train(cal, tr, ops, 0, t0, None, None, limit, counter)
            else:
                started = [o for o in ops if o["status"] != "pending"]
                last = started[-1]
                X = tr["track_id"] if tr["status"] == "on_track" else \
                    ROUTE_BY_ID[active[last["id"]]["route_id"]]["to_id"]
                cs = last["stage"]
                move_in = [o for o in ops if o["stage"] == cs and o["is_move"]][0]
                hs = move_in["actual_start_s"]
                cur = max(now, last["actual_start_s"] + last["duration_s"])
                inner = [o for o in pending if o["stage"] == cs]
                asgs, ok = [], True
                for op in inner:
                    r2 = _find_slot(cal, cur, op["duration_s"], [], op["groups"], tr["id"], limit=limit)
                    if not r2:
                        ok = False
                        break
                    asgs.append(_asg(op, r2[0], X, None, r2[1]))
                    cur = r2[0] + op["duration_s"]
                if ok:
                    solve = _plan_train(cal, tr, ops, "internal", 0, None, None, limit, counter)
                    sub = solve(cs + 1, cur, X, hs)
                    if sub:
                        res = (asgs + sub[0], sub[1])
        except _Budget:
            res = None
        if res is None:
            unassigned.append({"train_id": tr["id"], "code": "NO_FEASIBLE_SLOT",
                               "message": f"{tr['id']}: нет допустимой цепочки путей и ресурсов в горизонте"})
            # оставить прежние будущие назначения, чтобы поезд не «исчез»
            assignments += fixed_asgs[tr["id"]] + [active[o["id"]] for o in pending if o["id"] in active]
            continue
        full = fixed_asgs[tr["id"]] + res[0]
        for tid_ in _occupied(tr):
            cal.remove_owner(tid_, tr["id"])
        _book(cal, tr["id"], full)
        assignments += full

    plan_obj = {
        "id": "PL-" + uuid.uuid4().hex[:8], "run_id": snapshot["run_id"],
        "based_on_version": snapshot["state_version"], "based_on_epoch": snapshot["epoch"],
        "based_on_time_s": now, "strategy": strategy, "timed_out": timed_out,
        "assignments": sorted(assignments, key=lambda a: (a["start_s"], a["operation_id"])),
        "unassigned": unassigned, "calc_ms": 0,
    }
    violations = validate_plan(snapshot, plan_obj)
    plan_obj["violations"] = violations
    plan_obj["status"] = "infeasible" if violations else ("partial" if unassigned else "feasible")
    plan_obj["metrics"], plan_obj["explanations"] = _metrics_and_explanations(snapshot, plan_obj, active)
    plan_obj["calc_ms"] = round((time.monotonic() - t_start) * 1000)
    return plan_obj


def validate_plan(snapshot, plan_obj) -> list[dict]:
    """Независимая проверка: пересечения ресурсов, зон, путей; закрытия; начало не раньше now."""
    now = snapshot["sim_time_s"]
    asgs = plan_obj["assignments"]
    v = []
    uses = defaultdict(list)
    by_train = defaultdict(list)
    for a in asgs:
        by_train[a["train_id"]].append(a)
        if not a["fixed"] and a["start_s"] < now:
            v.append({"code": "PAST_START", "operation_ids": [a["operation_id"]], "message": "Начало в прошлом"})
        for r in a["resource_ids"]:
            uses[r].append((a["start_s"], a["end_s"], a["train_id"], a["operation_id"]))
        route = ROUTE_BY_ID.get(a["route_id"]) if a["route_id"] else None
        if route:
            for z in route["conflict_zone_ids"]:
                uses[z].append((a["start_s"], a["end_s"], a["train_id"], a["operation_id"]))
    for tid, lst in by_train.items():
        lst.sort(key=lambda a: a["start_s"])
        for a, b in zip(lst, lst[1:]):
            if b["start_s"] < a["end_s"]:
                v.append({"code": "PREDECESSOR_INCOMPLETE", "operation_ids": [b["operation_id"]],
                          "message": f"{b['operation_id']} начинается до завершения предшественника"})
        for tr, s, e in _holds(lst):
            uses["track:" + tr].append((s, e, tid, tid))
    closed = {t["id"]: t["closed_until_s"] for t in snapshot["tracks"] if t["closed_until_s"] and t["closed_until_s"] > now}
    for a in asgs:
        if a["kind"] in ("arrival", "shunt") and not a["fixed"] and a["track_id"] in closed:
            if a["start_s"] < closed[a["track_id"]]:
                v.append({"code": "TRACK_CLOSED", "operation_ids": [a["operation_id"]],
                          "message": f"Вход на закрытый {a['track_id']}"})
    unavail = {r["id"]: r["unavailable_until_s"] for r in snapshot["resources"]
               if r["unavailable_until_s"] and r["unavailable_until_s"] > now}
    for a in asgs:
        for r in a["resource_ids"]:
            if not a["fixed"] and r in unavail and a["start_s"] < unavail[r]:
                v.append({"code": "RESOURCE_UNAVAILABLE", "operation_ids": [a["operation_id"]],
                          "message": f"{r} недоступен до {unavail[r]} с"})
    for res, lst in uses.items():
        lst.sort()
        for x, y in zip(lst, lst[1:]):
            if y[0] < x[1] and x[2] != y[2]:
                code = "TRACK_OCCUPIED" if res.startswith("track:") else ("ROUTE_BUSY" if res in ("GW", "GE") else "RESOURCE_UNAVAILABLE")
                v.append({"code": code, "operation_ids": [x[3], y[3]],
                          "message": f"Пересечение на {res.replace('track:', '')}: {x[2]} и {y[2]}"})
    return v


def _dep_forecast(asgs):
    out = {}
    for a in asgs:
        if a["kind"] == "departure":
            out[a["train_id"]] = a["end_s"]
    return out


def _metrics_and_explanations(snapshot, plan_obj, active):
    trains = {t["id"]: t for t in snapshot["trains"]}
    dep = _dep_forecast(plan_obj["assignments"])
    delays = {tid: max(0, end - trains[tid]["scheduled_departure_s"]) for tid, end in dep.items()}
    old_dep = _dep_forecast(active.values())
    changed, expl = 0, []
    new_by_op = {a["operation_id"]: a for a in plan_obj["assignments"]}
    closed = {t["id"]: t["closed_until_s"] for t in snapshot["tracks"]
              if t["closed_until_s"] and t["closed_until_s"] > snapshot["sim_time_s"]}
    unavail = {r["id"]: r["unavailable_until_s"] for r in snapshot["resources"]
               if r["unavailable_until_s"] and r["unavailable_until_s"] > snapshot["sim_time_s"]}
    per_train = defaultdict(list)
    for op_id, a in new_by_op.items():
        o = active.get(op_id)
        if a["fixed"] or not o:
            continue
        if (o["start_s"], o["track_id"], tuple(o["resource_ids"])) != (a["start_s"], a["track_id"], tuple(a["resource_ids"])):
            changed += 1
            per_train[a["train_id"]].append((o, a))
    for tid in sorted(per_train):
        pairs = per_train[tid]
        parts, code, reason = [], "RESCHEDULED", ""
        moved = [(o, a) for o, a in pairs if o["track_id"] != a["track_id"] and a["kind"] in ("arrival", "shunt")]
        for o, a in moved[:1]:
            parts.append(f"{tid} перенесён с {o['track_id']} на {a['track_id']}")
            if o["track_id"] in closed:
                code, reason = "TRACK_CLOSED", f", потому что {o['track_id']} закрыт до {closed[o['track_id']]} с"
        res_ch = [(o, a) for o, a in pairs if set(o["resource_ids"]) != set(a["resource_ids"])]
        for o, a in res_ch[:1]:
            old_r = ",".join(o["resource_ids"]) or "—"
            new_r = ",".join(a["resource_ids"]) or "—"
            parts.append(f"{a['kind']} {tid}: {old_r} → {new_r}")
            bad = [r for r in o["resource_ids"] if r in unavail]
            if bad and not reason:
                code, reason = "RESOURCE_UNAVAILABLE", f", потому что {bad[0]} недоступен до {unavail[bad[0]]} с"
        if not parts:
            parts.append(f"{tid}: сдвиг операций по времени")
        d_old, d_new = old_dep.get(tid), dep.get(tid)
        tail = ""
        if d_old is not None and d_new is not None and d_new != d_old:
            tail = f"; прогноз отправления {'позже' if d_new > d_old else 'раньше'} на {abs(d_new - d_old)} с"
        expl.append({"train_id": tid, "code": code, "operation_ids": [a["operation_id"] for _, a in pairs],
                     "message": "; ".join(parts) + reason + tail})
    vals = list(delays.values())
    metrics = {"total_delay_s": sum(vals), "max_delay_s": max(vals) if vals else 0,
               "unassigned_count": len(plan_obj["unassigned"]), "changed_count": changed,
               "delayed_trains": sum(1 for x in vals if x > 0),
               "forecast_departures": dep, "delays": delays}
    return metrics, expl
