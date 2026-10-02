"""Station efficiency index I = 100 · (1 − Σ wᵢ·pᵢ).

Same factors, weights and categories as the frontend contract (``StationIndex``):

* ``delay``       — mean positive departure delay in the window, normalised by 10 min;
* ``on_time``     — share of departures due in the window that did not leave on time;
* ``utilization`` — overload of open train tracks (penalty grows from 75 % to 100 % occupancy);
* ``conflicts``   — number of current conflicts, normalised by 5;
* ``idle``        — share of train-time in the station spent blocked (waiting for a resource/track).

A factor without data in the window is excluded and the remaining weights are renormalised.

The *fact* index looks back over the last 15 model minutes. Occupancy and blocking are integrated
by the coordinator between engine events (``record``) into 30-second buckets kept in the engine
state, so they survive checkpoints and restarts. The *forecast* index looks 15 minutes ahead along
a plan (``forecast``) and is attached to every plan variant and to ``replan_finished`` as the
baseline of the currently accepted plan.
"""
from __future__ import annotations

WINDOW_S = 900
BUCKET_S = 30
FORECAST_STEP_S = 30
WEIGHTS = {"delay": 0.35, "on_time": 0.25, "utilization": 0.15, "conflicts": 0.15, "idle": 0.10}
LABELS = {
    "delay": "Задержка отправления",
    "on_time": "Выполнение отправлений",
    "utilization": "Перегрузка путей",
    "conflicts": "Конфликты плана",
    "idle": "Простой из-за ожидания",
}
ACTIVE = ("waiting_entry", "moving", "on_track")
MOVES = ("arrival", "shunt_to_cargo", "shunt_to_storage", "shunt_to_departure", "departure")
# Tracks that hold trains; locomotive depots hold locomotives and do not count as station capacity.
TRAIN_TRACK_KINDS = ("passenger", "freight", "storage", "cargo")
INF = float("inf")


def _clamp(x: float) -> float:
    return min(max(x, 0.0), 1.0)


def from_penalties(penalties: dict, window_s: int, kind: str) -> dict | None:
    have = {k: v for k, v in penalties.items() if v is not None}
    if not have:
        return None
    wsum = sum(WEIGHTS[k] for k in have)
    factors = []
    for key, weight in WEIGHTS.items():
        if key in have:
            factors.append(dict(id=key, label=LABELS[key], penalty=round(have[key], 3), weight=weight,
                                contribution=round(100 * weight / wsum * have[key], 1)))
        else:
            factors.append(dict(id=key, label=LABELS[key], penalty=None, weight=weight,
                                contribution=None, no_data=True))
    value = max(0, min(100, round(100 - sum(f["contribution"] or 0 for f in factors))))
    category = "norm" if value >= 80 else "attention" if value >= 50 else "critical"
    return dict(value=value, category=category, window_s=int(window_s), factors=factors, kind=kind)


def _train_tracks(state) -> list[dict]:
    return [t for t in state.tracks.values() if t.get("kind") in TRAIN_TRACK_KINDS]


def _open_at(track: dict, ts: int) -> bool:
    if track.get("availability", "open") == "open":
        return True
    until = track.get("closed_until_s")
    return until is not None and ts >= until


def _departure_ops(state) -> dict[str, dict]:
    return {o["train_id"]: o for o in state.operations.values() if o["kind"] == "departure"}


def sample(state) -> tuple[int, int, int, int]:
    """(occupied open tracks, open tracks, blocked trains, active trains) of a constant state."""
    tracks = [t for t in _train_tracks(state) if t.get("availability", "open") == "open"]
    occupied = sum(1 for t in tracks if t.get("occupant_train_id"))
    active = [t["id"] for t in state.trains.values() if t["status"] in ACTIVE]
    waiting = {o["train_id"] for o in state.operations.values()
               if o["status"] == "pending" and o.get("wait_reason")}
    blocked = sum(1 for tid in active if tid in waiting)
    return occupied, len(tracks), blocked, len(active)


def record(buckets: list, state, start_s: int, end_s: int) -> list:
    """Integrate the (constant) ``state`` over model time [start_s, end_s) into 30 s buckets."""
    if end_s <= start_s:
        return buckets
    occ, open_, blocked, active = sample(state)
    out = [list(b) for b in buckets]
    t = max(start_s, end_s - WINDOW_S - BUCKET_S)  # older time would be pruned anyway
    while t < end_s:
        bucket = t - t % BUCKET_S
        upto = min(end_s, bucket + BUCKET_S)
        dt = upto - t
        if not out or out[-1][0] != bucket:
            out.append([bucket, 0, 0, 0, 0])
        cell = out[-1]
        cell[1] += occ * dt
        cell[2] += open_ * dt
        cell[3] += blocked * dt
        cell[4] += active * dt
        t = upto
    return [b for b in out if b[0] + BUCKET_S > end_s - WINDOW_S]


def fact(state) -> dict:
    """Index over the last 15 model minutes of the run."""
    now = state.sim_time_s
    w0 = max(0, now - WINDOW_S)
    departures = _departure_ops(state)
    delays, due, on_time = [], 0, 0
    for train in state.trains.values():
        sched = train["scheduled_departure_s"]
        op = departures.get(train["id"])
        actual = op.get("actual_end_s") if op and train["status"] == "departed" else None
        if actual is not None:
            if actual >= w0:
                delays.append(max(0, actual - sched))
        elif sched < now:
            delays.append(now - sched)
        if w0 <= sched <= now:
            due += 1
            if actual is not None and actual <= sched:
                on_time += 1
    p = {
        "delay": min(sum(delays) / len(delays) / 600, 1.0) if delays else None,
        "on_time": 1 - on_time / due if due else None,
    }
    window = [b for b in getattr(state, "index_samples", []) if b[0] + BUCKET_S > w0]
    occ = sum(b[1] for b in window)
    open_ = sum(b[2] for b in window)
    blocked = sum(b[3] for b in window)
    active = sum(b[4] for b in window)
    p["utilization"] = _clamp((occ / open_ - 0.75) / 0.25) if open_ else None
    p["conflicts"] = min(len(state.conflicts) / 5, 1.0)
    p["idle"] = min(blocked / active, 1.0) if active else None
    return from_penalties(p, min(WINDOW_S, now), "fact")


def forecast(state, assignments: list[dict], conflict_count: int) -> dict | None:
    """Index for the next 15 model minutes if ``assignments`` (pending operations) are executed."""
    now = state.sim_time_s
    w1 = now + WINDOW_S
    ops = state.operations
    per_train: dict[str, list[dict]] = {}
    for a in list(state.running.values()) + list(assignments):
        op = ops.get(a["operation_id"])
        if op is not None:
            per_train.setdefault(op["train_id"], []).append(dict(a, kind=op["kind"]))
    dep, arr = {}, {}
    holds: list[tuple[str, float, float]] = []
    for tid, items in per_train.items():
        items.sort(key=lambda a: a["start_s"])
        train = state.trains[tid]
        cur = None
        if train["status"] == "on_track" and train.get("track_id"):
            cur = (train["track_id"], now)
        for a in items:
            if a["kind"] == "arrival":
                arr.setdefault(tid, a["start_s"])
            if a["kind"] == "departure":
                dep[tid] = a["end_s"]
            if a["kind"] in MOVES:
                if cur:
                    holds.append((cur[0], cur[1], a["end_s"]))
                cur = (a["track_id"], a["start_s"]) if a["kind"] != "departure" and a.get("track_id") else None
        if cur:
            holds.append((cur[0], cur[1], INF))
    # A train standing on a track with no planned moves keeps holding it.
    for train in state.trains.values():
        if train["id"] not in per_train and train["status"] == "on_track" and train.get("track_id"):
            holds.append((train["track_id"], now, INF))

    delays, due, ok = [], 0, 0
    for train in state.trains.values():
        if train["status"] == "departed":
            continue
        sched, d = train["scheduled_departure_s"], dep.get(train["id"])
        if d is None:
            if sched < w1:
                delays.append(w1 - sched)
                due += 1
            continue
        if now <= d < w1 or sched < w1:
            delays.append(max(0, min(d, w1) - sched))
        if now <= sched < w1:
            due += 1
            if d <= sched:
                ok += 1
    p = {
        "delay": min(sum(delays) / len(delays) / 600, 1.0) if delays else None,
        "on_time": 1 - ok / due if due else None,
    }
    tracks = _train_tracks(state)
    ratios, blocked, active = [], 0, 0
    for ts in range(now, w1, FORECAST_STEP_S):
        open_ids = {t["id"] for t in tracks if _open_at(t, ts)}
        busy = {track for track, s, e in holds if s <= ts < e and track in open_ids}
        ratios.append(len(busy) / len(open_ids) if open_ids else 1.0)
        for train in state.trains.values():
            if train["status"] == "departed":
                continue
            d = dep.get(train["id"])
            if train["expected_arrival_s"] <= ts and (d is None or ts < d):
                active += 1
                a = arr.get(train["id"])
                if train["status"] in ("scheduled", "waiting_entry") and (a is None or ts < a):
                    blocked += 1
    p["utilization"] = _clamp((sum(ratios) / len(ratios) - 0.75) / 0.25) if ratios else None
    p["conflicts"] = min(conflict_count / 5, 1.0)
    p["idle"] = min(blocked / active, 1.0) if active else None
    return from_penalties(p, WINDOW_S, "forecast")


def baseline(state) -> dict | None:
    """Forecast of the currently accepted plan (pending assignments of the engine)."""
    if not state.active_plan:
        return None
    pending = [a for oid, a in state.assignments.items() if state.operations[oid]["status"] == "pending"]
    return forecast(state, pending, len(state.conflicts))
