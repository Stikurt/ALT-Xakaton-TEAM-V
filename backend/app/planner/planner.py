from __future__ import annotations

import itertools
import time
import uuid

from app.constraints import get_value, validate_plan
from .calendars import ResourceCalendar
from .strategies import sort_trains


DEFAULT_HORIZON_S = 7200
MOVING = {"arrival", "departure", "shunt_to_cargo", "shunt_to_storage", "shunt_to_departure"}


def _routes(config, snapshot):
    return get_value(config, "routes", get_value(snapshot, "routes", [])) or []


def _route_map(config, snapshot):
    return {get_value(r, "id"): r for r in _routes(config, snapshot)}


def _track_map(snapshot):
    return {get_value(t, "id"): t for t in get_value(snapshot, "tracks", [])}


def _resource_map(snapshot):
    return {get_value(r, "id"): r for r in get_value(snapshot, "resources", [])}


def _operation_map(snapshot):
    return {get_value(o, "id"): o for o in get_value(snapshot, "operations", [])}


def build_calendars(snapshot, config):
    now = get_value(snapshot, "sim_time_s", 0)
    horizon = now + get_value(config, "horizon_s", get_value(config, "planning_horizon_s", DEFAULT_HORIZON_S))

    calendars = {
        "tracks": {},
        "resources": {},
        "zones": {},
    }

    for track in get_value(snapshot, "tracks", []):
        tid = get_value(track, "id")
        cal = ResourceCalendar()
        closed_until = get_value(track, "closed_until_s")
        if get_value(track, "availability") == "closed" and closed_until and closed_until > now:
            cal.reserve(now, closed_until, f"closed:{tid}")
        occupant = get_value(track, "occupant_train_id")
        if occupant:
            cal.reserve(now, horizon, f"train:{occupant}")
        calendars["tracks"][tid] = cal

    for resource in get_value(snapshot, "resources", []):
        rid = get_value(resource, "id")
        cal = ResourceCalendar()
        unavailable_until = get_value(resource, "unavailable_until_s")
        if get_value(resource, "availability") != "available" and unavailable_until and unavailable_until > now:
            cal.reserve(now, unavailable_until, f"unavailable:{rid}")
        calendars["resources"][rid] = cal

    zones = set()
    for route in _routes(config, snapshot):
        zones.update(get_value(route, "conflict_zone_ids", []) or [])
    for zone in zones:
        calendars["zones"][zone] = ResourceCalendar()

    # Running operation reservations known in snapshot/context-compatible fields.
    running_assignments = get_value(snapshot, "running_assignments", {}) or {}
    resources = _resource_map(snapshot)
    routes = _route_map(config, snapshot)
    operations = _operation_map(snapshot)

    for oid, assignment in running_assignments.items():
        start_s = get_value(assignment, "start_s", now)
        end_s = get_value(assignment, "end_s")
        if end_s is None or end_s <= now:
            continue
        for rid in get_value(assignment, "resource_ids", []) or []:
            if rid in calendars["resources"]:
                calendars["resources"][rid].reserve(now, end_s, oid, allow_same_owner=True)
        op = operations.get(oid)
        if op and get_value(op, "kind") in MOVING:
            route = routes.get(get_value(assignment, "route_id"))
            if route:
                for zone in get_value(route, "conflict_zone_ids", []) or []:
                    calendars["zones"].setdefault(zone, ResourceCalendar()).reserve(
                        now, end_s, oid, allow_same_owner=True
                    )

    return calendars


def get_train_operations(snapshot, train_id):
    operations = [
        o for o in get_value(snapshot, "operations", [])
        if get_value(o, "train_id") == train_id
    ]
    return topological_operations(operations)


def topological_operations(operations):
    by_id = {get_value(o, "id"): o for o in operations}
    done = set()
    result = []

    while len(result) < len(operations):
        ready = [
            o for o in operations
            if get_value(o, "id") not in done
            and all(
                pred not in by_id or pred in done
                for pred in (get_value(o, "predecessor_ids", []) or [])
            )
        ]
        if not ready:
            raise ValueError("Обнаружен цикл зависимостей операций.")
        ready.sort(key=lambda o: get_value(o, "id", ""))
        selected = ready[0]
        done.add(get_value(selected, "id"))
        result.append(selected)

    return result


def _candidate_tracks(snapshot, train, operation):
    """Deterministic station-role heuristic.

    The normal layout keeps transit traffic on P05/P06 and the local-freight
    process on P03 -> cargo -> storage -> P04. This avoids creating avoidable
    conflicts while staying inside the station topology from station.json.
    """
    kind = get_value(operation, "kind", "")
    train_kind = get_value(train, "kind", "")
    tracks = _track_map(snapshot)

    if kind == "arrival":
        if train_kind == "passenger":
            preferred = ["P01", "P02"]
        elif train_kind == "transit":
            preferred = ["P05", "P06"]
        elif train_kind == "local":
            preferred = ["P03"]
        else:
            preferred = []
    elif kind == "shunt_to_cargo":
        preferred = ["P10", "P11"]
    elif kind == "shunt_to_storage":
        preferred = ["P07", "P08", "P09"]
    elif kind == "shunt_to_departure":
        preferred = ["P04"]
    else:
        return []

    result = []
    for track_id in preferred:
        track = tracks.get(track_id)
        if track is None:
            continue
        if get_value(track, "usable_length_m", 0) < get_value(train, "length_m", 0):
            continue
        result.append(track_id)

    return result


def _route_id(config, snapshot, from_id, to_id):
    for route in _routes(config, snapshot):
        if get_value(route, "from_id") == from_id and get_value(route, "to_id") == to_id:
            return get_value(route, "id")
    return None


def _matching_resources(snapshot, kind, capability, *, track_id=None):
    result = []
    for resource in get_value(snapshot, "resources", []):
        if get_value(resource, "kind") != kind:
            continue
        if capability not in (get_value(resource, "capabilities", []) or []):
            continue
        rid = get_value(resource, "id")
        if kind == "cargo_front" and track_id is not None and rid != f"F{track_id[1:]}":
            continue
        result.append(rid)
    return sorted(result)


def _resource_options(snapshot, operation_kind, track_id):
    specs = []
    if operation_kind.startswith("shunt_"):
        specs = [("locomotive", "shunt"), ("crew", "shunt")]
    elif operation_kind == "inspection":
        specs = [("crew", "inspection")]
    elif operation_kind == "preparation":
        specs = [("crew", "preparation")]
    elif operation_kind == "formation":
        specs = [("crew", "formation")]
    elif operation_kind == "cargo":
        specs = [("cargo_front", "cargo")]

    if not specs:
        return [()]

    groups = []
    for resource_kind, capability in specs:
        matches = _matching_resources(
            snapshot,
            resource_kind,
            capability,
            track_id=track_id,
        )
        if not matches:
            return []
        groups.append(matches)

    return list(itertools.product(*groups))


def _common_slot(calendars, requirements, earliest_s, duration_s, horizon_s):
    candidate = earliest_s
    while candidate + duration_s <= horizon_s:
        next_candidate = candidate
        all_free = True

        for requirement in requirements:
            group, ident = requirement[0], requirement[1]
            span_s = requirement[2] if len(requirement) > 2 else duration_s
            if candidate + span_s > horizon_s:
                return None
            calendar = calendars[group].get(ident)
            if calendar is None:
                return None
            free_at = calendar.next_free_time(candidate, span_s)
            if free_at != candidate:
                all_free = False
                next_candidate = max(next_candidate, free_at)

        if all_free:
            return candidate

        candidate = next_candidate

    return None


def _route_requirements(config, snapshot, route_id):
    routes = _route_map(config, snapshot)
    route = routes.get(route_id)
    if not route:
        return []
    return [("zones", zone) for zone in (get_value(route, "conflict_zone_ids", []) or [])]


def _reserve(calendars, requirements, start_s, end_s, owner):
    operation_duration = end_s - start_s
    for requirement in requirements:
        group, ident = requirement[0], requirement[1]
        span_s = requirement[2] if len(requirement) > 2 else operation_duration
        calendars[group][ident].reserve(start_s, start_s + span_s, owner)


def _target_hold_span(operations, operation_index):
    """Minimum time a target track must stay reserved after entering it.

    The source track is held until the next movement completes, so a newly
    entered track must remain unavailable through all stationary work and the
    following outbound movement.
    """
    total = 0
    current = operations[operation_index]
    total += get_value(current, "duration_s", 0)

    if get_value(current, "kind") == "departure":
        return total

    for following in operations[operation_index + 1:]:
        total += get_value(following, "duration_s", 0)
        if get_value(following, "kind") in MOVING:
            break

    return total


def _occupancy_intervals(snapshot, config, train, assignments):
    routes = _route_map(config, snapshot)
    current_track = get_value(train, "track_id")
    now = get_value(snapshot, "sim_time_s", 0)
    hold_start = now if current_track else None
    result = []

    for a in assignments:
        kind = a["_kind"]
        if kind == "arrival":
            current_track = a["track_id"]
            hold_start = a["start_s"]
        elif kind == "departure":
            if current_track:
                result.append((current_track, hold_start if hold_start is not None else now, a["end_s"]))
            current_track = None
            hold_start = None
        elif kind.startswith("shunt_"):
            if current_track:
                result.append((current_track, hold_start if hold_start is not None else now, a["end_s"]))
            current_track = a["track_id"]
            hold_start = a["start_s"]

    return result


def _remove_train_placeholder(calendars, train_id):
    owner = f"train:{train_id}"
    for calendar in calendars["tracks"].values():
        calendar.remove_owner(owner)


def _restore_train_placeholder(calendars, snapshot, config, train):
    track_id = get_value(train, "track_id")
    if not track_id or track_id not in calendars["tracks"]:
        return
    now = get_value(snapshot, "sim_time_s", 0)
    horizon = now + get_value(config, "horizon_s", get_value(config, "planning_horizon_s", DEFAULT_HORIZON_S))
    calendars["tracks"][track_id].reserve(now, horizon, f"train:{get_value(train, 'id')}")


def schedule_train(snapshot, config, train, calendars, horizon_s):
    train_id = get_value(train, "id")
    _remove_train_placeholder(calendars, train_id)

    operations = get_train_operations(snapshot, train_id)
    operations = [o for o in operations if get_value(o, "status") == "pending"]

    now = get_value(snapshot, "sim_time_s", 0)
    current_s = max(
        now,
        get_value(train, "expected_arrival_s", get_value(train, "scheduled_arrival_s", now)) or now,
    )

    current_track = get_value(train, "track_id")
    raw_assignments = []
    trial = {
        group: {ident: cal.clone() for ident, cal in values.items()}
        for group, values in calendars.items()
    }

    for operation_index, operation in enumerate(operations):
        oid = get_value(operation, "id")
        kind = get_value(operation, "kind")
        duration = get_value(operation, "duration_s", 0)

        if duration <= 0:
            _restore_train_placeholder(calendars, snapshot, config, train)
            return None, {"train_id": train_id, "operation_id": oid, "reason": "INVALID_DURATION"}

        if kind == "arrival":
            track_candidates = _candidate_tracks(snapshot, train, operation)
        elif kind.startswith("shunt_"):
            track_candidates = _candidate_tracks(snapshot, train, operation)
        elif kind == "departure":
            track_candidates = [current_track] if current_track else []
        else:
            track_candidates = [current_track] if current_track else []

        scheduled = None
        best_choice = None

        # Evaluate all deterministic alternatives and choose the earliest
        # feasible start. Do not pick the first path merely because it has
        # some slot later in the horizon.
        for track_id in track_candidates:
            if not track_id:
                continue

            if kind == "arrival":
                route_id = _route_id(config, snapshot, "W", track_id)
            elif kind.startswith("shunt_"):
                route_id = _route_id(config, snapshot, current_track, track_id)
            elif kind == "departure":
                route_id = _route_id(config, snapshot, current_track, "E")
            else:
                route_id = None

            if kind in MOVING and route_id is None:
                continue

            for resources in _resource_options(snapshot, kind, track_id):
                requirements = [("resources", rid) for rid in resources]

                if kind in MOVING:
                    requirements.extend(
                        _route_requirements(config, snapshot, route_id)
                    )

                if kind in MOVING and kind != "departure":
                    requirements.append(
                        (
                            "tracks",
                            track_id,
                            _target_hold_span(operations, operation_index),
                        )
                    )

                earliest = current_s

                # scheduled_departure_s is the moment the train reaches E.
                if kind == "departure" and get_value(train, "kind") in (
                    "passenger",
                    "transit",
                ):
                    earliest = max(
                        earliest,
                        get_value(train, "scheduled_departure_s", 0) - duration,
                    )

                start_s = _common_slot(
                    trial,
                    requirements,
                    earliest,
                    duration,
                    horizon_s,
                )
                if start_s is None:
                    continue

                choice_key = (
                    start_s,
                    track_id,
                    tuple(resources),
                    route_id or "",
                )

                if best_choice is None or choice_key < best_choice[0]:
                    best_choice = (
                        choice_key,
                        {
                            "operation_id": oid,
                            "start_s": start_s,
                            "end_s": start_s + duration,
                            "track_id": track_id,
                            "route_id": route_id,
                            "resource_ids": list(resources),
                            "_kind": kind,
                        },
                        list(requirements),
                    )

        if best_choice is not None:
            _, scheduled, requirements = best_choice
            _reserve(
                trial,
                requirements,
                scheduled["start_s"],
                scheduled["end_s"],
                oid,
            )

        if scheduled is None:
            _restore_train_placeholder(calendars, snapshot, config, train)
            return None, {
                "train_id": train_id,
                "operation_id": oid,
                "reason": "NO_FEASIBLE_SLOT",
            }

        raw_assignments.append(scheduled)
        current_s = scheduled["end_s"]

        if kind == "arrival" or kind.startswith("shunt_"):
            current_track = scheduled["track_id"]
        elif kind == "departure":
            current_track = None

    # Temporary target-track reservations were used to find conflict-free
    # movement slots. Replace them with the train's continuous hold interval.
    for assignment in raw_assignments:
        if assignment["_kind"] != "departure":
            trial["tracks"][assignment["track_id"]].remove_owner(
                assignment["operation_id"]
            )

    # Reserve the whole path-holding intervals required by the model.
    occupancy = _occupancy_intervals(snapshot, config, train, raw_assignments)
    for track_id, start_s, end_s in occupancy:
        cal = trial["tracks"][track_id]
        if not cal.is_free(start_s, end_s):
            _restore_train_placeholder(calendars, snapshot, config, train)
            return None, {
                "train_id": train_id,
                "reason": "TRACK_OCCUPIED",
                "track_id": track_id,
            }
        cal.reserve(start_s, end_s, f"train:{train_id}")

    assignments = [
        {k: v for k, v in a.items() if k != "_kind"}
        for a in raw_assignments
    ]

    return (assignments, trial), None


def calculate_plan_metrics(snapshot, assignments, unassigned):
    operations = _operation_map(snapshot)
    trains = {get_value(t, "id"): t for t in get_value(snapshot, "trains", [])}
    assignment_by_op = {a["operation_id"]: a for a in assignments}
    delays = []

    for oid, op in operations.items():
        if get_value(op, "kind") != "departure":
            continue
        a = assignment_by_op.get(oid)
        if not a:
            continue
        train = trains[get_value(op, "train_id")]
        delays.append(max(0, a["end_s"] - get_value(train, "scheduled_departure_s", 0)))

    return {
        "total_positive_delay_s": sum(delays),
        "max_delay_s": max(delays) if delays else 0,
        "unassigned_count": len(unassigned),
        "changed_future_assignments": 0,
    }


def plan(snapshot, config, strategy: str, budget_s: float = 2.0):
    started_at = time.monotonic()
    now = get_value(snapshot, "sim_time_s", 0)
    run_id = get_value(snapshot, "run_id", "")
    state_version = get_value(snapshot, "state_version", 0)

    horizon_length = get_value(
        config,
        "horizon_s",
        get_value(config, "planning_horizon_s", DEFAULT_HORIZON_S),
    )
    horizon_s = max(now, horizon_length if horizon_length > now else now + horizon_length)

    calendars = build_calendars(snapshot, config)
    trains = sort_trains(get_value(snapshot, "trains", []), strategy)

    assignments = []
    unassigned = []
    timed_out = False

    for train in trains:
        if time.monotonic() - started_at >= budget_s:
            timed_out = True
            break

        result, error = schedule_train(
            snapshot,
            config,
            train,
            calendars,
            horizon_s,
        )

        if error:
            unassigned.append(error)
            continue

        train_assignments, new_calendars = result
        calendars = new_calendars
        assignments.extend(train_assignments)

    if timed_out:
        assigned_ops = {a["operation_id"] for a in assignments}
        operations = _operation_map(snapshot)
        assigned_trains = {
            get_value(operations[oid], "train_id")
            for oid in assigned_ops
            if oid in operations
        }
        known_unassigned = {x["train_id"] for x in unassigned}
        for train in trains:
            tid = get_value(train, "id")
            if tid not in assigned_trains and tid not in known_unassigned:
                unassigned.append({"train_id": tid, "reason": "TIMEOUT"})

    metrics = calculate_plan_metrics(snapshot, assignments, unassigned)

    result = {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "based_on_version": state_version,
        "strategy": strategy,
        "status": "partial" if unassigned else "feasible",
        "assignments": assignments,
        "unassigned": unassigned,
        "metrics": metrics,
        "explanations": [
            {
                "train_id": item["train_id"],
                "reason_code": item["reason"],
            }
            for item in unassigned
        ],
        "timed_out": timed_out,
    }

    context = {
        "snapshot": snapshot,
        "topology": config,
        "reservations": {},
        "busy_zones": {},
        "running_assignments": get_value(snapshot, "running_assignments", {}) or {},
    }

    violations = validate_plan(context, result)
    result["violations"] = violations

    if violations:
        result["status"] = "infeasible"

    result["calculation_time_ms"] = round(
        (time.monotonic() - started_at) * 1000,
        2,
    )

    return result
