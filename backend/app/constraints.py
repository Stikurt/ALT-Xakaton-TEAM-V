from __future__ import annotations

from typing import Any

from app.topology import MOVING, TopologyError, cargo_front_for, planning_horizon, station_boundary

Json = dict[str, Any]


def get_value(obj: Any, name: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def intervals_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def make_violation(
    code: str,
    message: str,
    *,
    entity_ids=None,
    operation_ids=None,
    start_s=None,
    end_s=None,
):
    return {
        "code": code,
        "message": message,
        "severity": "error",
        "entity_ids": entity_ids or [],
        "operation_ids": operation_ids or [],
        "start_s": start_s,
        "end_s": end_s,
    }


def _context(value: Json) -> tuple[Json, Json, Json, Json, Json]:
    """Return snapshot, topology, reservations, busy_zones, running_assignments."""
    if isinstance(value, dict) and isinstance(value.get("snapshot"), dict):
        return (
            value["snapshot"],
            value.get("topology") or {},
            value.get("reservations") or {},
            value.get("busy_zones") or {},
            value.get("running_assignments") or {},
        )
    return (
        value,
        value.get("topology") or value,
        value.get("reservations") or {},
        value.get("busy_zones") or {},
        value.get("running_assignments") or {},
    )


def _index(items, key="id"):
    return {get_value(x, key): x for x in (items or []) if get_value(x, key) is not None}


def _boundary(tracks: dict, topology: Json, routes: dict) -> tuple[str | None, str | None]:
    """(entry, exit) node ids of the station; (None, None) when the topology is unusable."""
    try:
        return station_boundary({"routes": list(routes.values()), "boundary": get_value(topology, "boundary")},
                                list(tracks))
    except TopologyError:
        return None, None


def _cargo_front_ok(track_id, resource_ids, resources: dict) -> bool:
    front = cargo_front_for(track_id, list(resources.values()))
    return front is not None and front in resource_ids


def _maps(context: Json):
    snapshot, topology, reservations, busy_zones, running = _context(context)
    tracks = _index(get_value(snapshot, "tracks", []))
    trains = _index(get_value(snapshot, "trains", []))
    resources = _index(get_value(snapshot, "resources", []))
    operations = _index(get_value(snapshot, "operations", []))
    routes = _index(get_value(topology, "routes", get_value(snapshot, "routes", [])))
    return snapshot, topology, reservations, busy_zones, running, tracks, trains, resources, operations, routes


def _resource_need(kind: str):
    if kind.startswith("shunt_"):
        return [("locomotive", "shunt"), ("crew", "shunt")]
    if kind == "inspection":
        return [("crew", "inspection")]
    if kind == "preparation":
        return [("crew", "preparation")]
    if kind == "formation":
        return [("crew", "formation")]
    if kind == "cargo":
        return [("cargo_front", "cargo")]
    return []


def _expected_track_kind(train_kind: str, operation_kind: str):
    if operation_kind in ("arrival", "departure"):
        return "passenger" if train_kind == "passenger" else "freight"
    return {
        "shunt_to_cargo": "cargo",
        "cargo": "cargo",
        "shunt_to_storage": "storage",
        "formation": "storage",
        "shunt_to_departure": "freight",
    }.get(operation_kind)


def _topological_for_train(operations: list[Json]) -> list[Json]:
    by_id = {o["id"]: o for o in operations}
    done: set[str] = set()
    result: list[Json] = []
    while len(result) < len(operations):
        ready = [
            o for o in operations
            if o["id"] not in done
            and all(p not in by_id or p in done for p in (o.get("predecessor_ids") or []))
        ]
        if not ready:
            return sorted(operations, key=lambda x: x["id"])
        ready.sort(key=lambda x: x["id"])
        o = ready[0]
        done.add(o["id"])
        result.append(o)
    return result


def _can_start_conflicts(context: Json, operation: Json, assignment: Json) -> list[Json]:
    (
        snapshot, topology, reservations, busy_zones, running,
        tracks, trains, resources, operations, routes,
    ) = _maps(context)

    problems: list[Json] = []
    oid = get_value(operation, "id")
    tid = get_value(operation, "train_id")
    train = trains.get(tid)
    start_s = get_value(assignment, "start_s")
    end_s = get_value(assignment, "end_s")
    track_id = get_value(assignment, "track_id")
    route_id = get_value(assignment, "route_id")
    resource_ids = get_value(assignment, "resource_ids", []) or []
    now = get_value(snapshot, "sim_time_s", 0)

    if train is None:
        return [make_violation("NO_FEASIBLE_SLOT", f"Неизвестный поезд {tid}", operation_ids=[oid])]

    if type(start_s) is not int or type(end_s) is not int or end_s <= start_s:
        problems.append(make_violation("NO_FEASIBLE_SLOT", "Некорректный интервал назначения",
                                       operation_ids=[oid], start_s=start_s, end_s=end_s))
        return problems

    if end_s - start_s != get_value(operation, "duration_s", end_s - start_s):
        problems.append(make_violation("NO_FEASIBLE_SLOT", "Длительность назначения не совпадает с операцией",
                                       operation_ids=[oid], start_s=start_s, end_s=end_s))

    for pred_id in get_value(operation, "predecessor_ids", []) or []:
        pred = operations.get(pred_id)
        if pred is None or get_value(pred, "status") != "completed":
            problems.append(make_violation(
                "PREDECESSOR_INCOMPLETE",
                f"Предшествующая операция {pred_id} ещё не завершена",
                operation_ids=[pred_id, oid],
            ))

    track = tracks.get(track_id)
    if track is None:
        problems.append(make_violation("NO_FEASIBLE_SLOT", f"Неизвестный путь {track_id}",
                                       entity_ids=[track_id], operation_ids=[oid]))
        return problems

    if get_value(track, "usable_length_m", 0) < get_value(train, "length_m", 0):
        problems.append(make_violation("NO_FEASIBLE_SLOT", "Поезд длиннее полезной длины пути",
                                       entity_ids=[tid, track_id], operation_ids=[oid]))

    kind = get_value(operation, "kind", "")
    expected_kind = _expected_track_kind(get_value(train, "kind", ""), kind)
    if expected_kind and get_value(track, "kind") != expected_kind:
        problems.append(make_violation("NO_FEASIBLE_SLOT", "Назначение пути несовместимо с операцией",
                                       entity_ids=[track_id], operation_ids=[oid]))

    if kind == "arrival":
        if get_value(train, "status") != "waiting_entry" or now < get_value(train, "expected_arrival_s", 0):
            problems.append(make_violation("PREDECESSOR_INCOMPLETE", "Поезд ещё не прибыл ко входу станции",
                                           entity_ids=[tid], operation_ids=[oid]))
    elif get_value(train, "status") != "on_track":
        problems.append(make_violation("PREDECESSOR_INCOMPLETE", "Поезд не находится на станционном пути",
                                       entity_ids=[tid], operation_ids=[oid]))

    if kind == "departure" and get_value(train, "kind") in ("passenger", "transit"):
        if now + get_value(operation, "duration_s", 0) < get_value(train, "scheduled_departure_s", 0):
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Раннее отправление запрещено",
                                           entity_ids=[tid], operation_ids=[oid]))

    if kind in MOVING:
        route = routes.get(route_id)
        if route is None:
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Неизвестный маршрут",
                                           entity_ids=[route_id], operation_ids=[oid]))
        else:
            entry_node, exit_node = _boundary(tracks, topology, routes)
            actual_from = entry_node if kind == "arrival" else get_value(train, "track_id")
            if get_value(route, "from_id") != actual_from:
                problems.append(make_violation("NO_FEASIBLE_SLOT", "Маршрут не начинается в фактическом положении поезда",
                                               entity_ids=[route_id], operation_ids=[oid]))
            if kind == "departure":
                if get_value(route, "to_id") != exit_node or get_value(route, "from_id") != track_id:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Неверный маршрут отправления",
                                                   entity_ids=[route_id, track_id], operation_ids=[oid]))
            else:
                if get_value(route, "to_id") != track_id:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Целевой путь не соответствует маршруту",
                                                   entity_ids=[route_id, track_id], operation_ids=[oid]))

            for zone in get_value(route, "conflict_zone_ids", []) or []:
                owner = busy_zones.get(zone)
                if owner not in (None, oid):
                    problems.append(make_violation("ROUTE_BUSY", f"Конфликтная зона {zone} занята",
                                                   entity_ids=[zone], operation_ids=[oid, owner]))

        if kind != "departure":
            if get_value(track, "availability") == "closed":
                problems.append(make_violation("TRACK_CLOSED", "Целевой путь закрыт для новых входов",
                                               entity_ids=[track_id], operation_ids=[oid]))
            occupant = get_value(track, "occupant_train_id")
            reserved_by = reservations.get(track_id)
            if occupant not in (None, tid) or reserved_by not in (None, oid):
                problems.append(make_violation("TRACK_OCCUPIED", "Целевой путь занят или зарезервирован",
                                               entity_ids=[track_id], operation_ids=[oid]))
    else:
        if route_id is not None:
            problems.append(make_violation("NO_FEASIBLE_SLOT", "У неподвижной операции не должно быть маршрута",
                                           operation_ids=[oid]))
        if get_value(train, "track_id") != track_id:
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Нельзя выполнять операцию на другом пути",
                                           entity_ids=[track_id], operation_ids=[oid]))

    if kind != "arrival":
        source = tracks.get(get_value(train, "track_id"))
        if source and get_value(source, "occupant_train_id") != tid:
            problems.append(make_violation("TRACK_OCCUPIED", "Поезд не владеет исходным путём",
                                           entity_ids=[get_value(train, "track_id")], operation_ids=[oid]))

    selected = []
    for rid in resource_ids:
        r = resources.get(rid)
        if r is None:
            problems.append(make_violation("RESOURCE_UNAVAILABLE", f"Неизвестный ресурс {rid}",
                                           entity_ids=[rid], operation_ids=[oid]))
            continue
        selected.append(r)
        if get_value(r, "availability") != "available" or get_value(r, "active_operation_id"):
            problems.append(make_violation("RESOURCE_UNAVAILABLE", f"Ресурс {rid} занят или недоступен",
                                           entity_ids=[rid], operation_ids=[oid]))

    for resource_kind, capability in _resource_need(kind):
        if not any(
            get_value(r, "kind") == resource_kind
            and capability in (get_value(r, "capabilities", []) or [])
            for r in selected
        ):
            problems.append(make_violation(
                "RESOURCE_UNAVAILABLE",
                f"Не назначен ресурс {resource_kind}/{capability}",
                operation_ids=[oid],
            ))

    if kind == "cargo" and not _cargo_front_ok(track_id, resource_ids, resources):
        problems.append(make_violation("RESOURCE_UNAVAILABLE", "Грузовой фронт не соответствует пути",
                                       entity_ids=[track_id], operation_ids=[oid]))

    return problems


def can_start(context: Json, operation_or_assignment: Json, assignment: Json | None = None):
    """Simulation contract: can_start(context, operation, assignment) -> list[conflict].

    Legacy local-test form can_start(snapshot, assignment) is kept temporarily and returns
    {"allowed": bool, "reasons": [...]}.
    """
    if assignment is None:
        snapshot = context
        legacy_assignment = operation_or_assignment
        operations = _index(get_value(snapshot, "operations", []))
        operation = operations.get(get_value(legacy_assignment, "operation_id"))
        if operation is None:
            reasons = [make_violation(
                "NO_FEASIBLE_SLOT",
                f"Операция {get_value(legacy_assignment, 'operation_id')} не существует",
                operation_ids=[get_value(legacy_assignment, "operation_id")],
            )]
        else:
            reasons = _can_start_conflicts(snapshot, operation, legacy_assignment)
        return {"allowed": not reasons, "reasons": reasons}

    return _can_start_conflicts(context, operation_or_assignment, assignment)


def validate_plan(context: Json, plan: Json, *, require_complete: bool = True) -> list[Json]:
    """All plan-level rules. ``require_complete=False`` skips only the "every pending operation is
    assigned" rule, so a planner can tell a partial plan (some trains not placed) from an
    infeasible one; accepting a plan (engine.apply_plan) always uses the complete check."""
    (
        snapshot, topology, reservations, busy_zones, running,
        tracks, trains, resources, operations, routes,
    ) = _maps(context)

    problems: list[Json] = []
    now = get_value(snapshot, "sim_time_s", 0)
    assignments = get_value(plan, "assignments", []) or []
    entry_node, exit_node = _boundary(tracks, topology, routes)
    seen: set = set()
    # Only assignments that passed the per-assignment checks take part in the cross checks below
    # (predecessors, overlaps, track holds); a malformed one is reported once and never crashes them.
    by_operation: dict[str, Json] = {}

    for a in assignments:
        oid = get_value(a, "operation_id")
        if oid in seen:
            problems.append(make_violation("NO_FEASIBLE_SLOT", f"Операция {oid} назначена дважды",
                                           operation_ids=[oid]))
            continue
        seen.add(oid)

        op = operations.get(oid)
        if op is None:
            problems.append(make_violation("NO_FEASIBLE_SLOT", f"Неизвестная операция {oid}",
                                           operation_ids=[oid]))
            continue

        start_s, end_s = get_value(a, "start_s"), get_value(a, "end_s")
        if type(start_s) is not int or type(end_s) is not int or end_s <= start_s:
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Некорректный интервал",
                                           operation_ids=[oid], start_s=start_s, end_s=end_s))
            continue

        if end_s - start_s != get_value(op, "duration_s"):
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Длительность назначения не совпадает с операцией",
                                           operation_ids=[oid], start_s=start_s, end_s=end_s))

        if get_value(op, "status") == "pending" and start_s < now:
            problems.append(make_violation("STALE_PLAN", "Будущая операция начинается в прошлом",
                                           operation_ids=[oid], start_s=start_s, end_s=end_s))

        tid = get_value(op, "train_id")
        train = trains.get(tid)
        track_id = get_value(a, "track_id")
        track = tracks.get(track_id)
        if train is None or track is None:
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Неизвестный поезд или путь",
                                           entity_ids=[x for x in (tid, track_id) if isinstance(x, str)],
                                           operation_ids=[oid]))
            continue
        by_operation[oid] = a

        if get_value(track, "usable_length_m", 0) < get_value(train, "length_m", 0):
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Поезд длиннее полезной длины пути",
                                           entity_ids=[tid, track_id], operation_ids=[oid]))

        kind = get_value(op, "kind", "")
        expected_kind = _expected_track_kind(get_value(train, "kind", ""), kind)
        if expected_kind and get_value(track, "kind") != expected_kind:
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Назначение пути несовместимо с операцией",
                                           entity_ids=[track_id], operation_ids=[oid]))

        route_id = get_value(a, "route_id")
        if kind in MOVING:
            route = routes.get(route_id)
            if route is None or get_value(route, "duration_s") != get_value(op, "duration_s"):
                problems.append(make_violation("NO_FEASIBLE_SLOT", "Неизвестный маршрут или неверная длительность",
                                               entity_ids=[route_id], operation_ids=[oid]))
            elif kind == "departure":
                if get_value(route, "from_id") != track_id or get_value(route, "to_id") != exit_node:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Путь не соответствует маршруту отправления",
                                                   entity_ids=[route_id, track_id], operation_ids=[oid]))
            elif get_value(route, "to_id") != track_id:
                problems.append(make_violation("NO_FEASIBLE_SLOT", "Целевой путь не соответствует маршруту",
                                               entity_ids=[route_id, track_id], operation_ids=[oid]))
        elif route_id is not None:
            problems.append(make_violation("NO_FEASIBLE_SLOT", "У неподвижной операции не должно быть маршрута",
                                           operation_ids=[oid]))

        resource_ids = get_value(a, "resource_ids", []) or []
        if len(resource_ids) != len(set(resource_ids)):
            problems.append(make_violation("RESOURCE_UNAVAILABLE", "Ресурс указан дважды",
                                           operation_ids=[oid]))

        selected = []
        for rid in resource_ids:
            resource = resources.get(rid)
            if resource is None:
                problems.append(make_violation("RESOURCE_UNAVAILABLE", f"Неизвестный ресурс {rid}",
                                               entity_ids=[rid], operation_ids=[oid]))
                continue
            selected.append(resource)
            unavailable_until = get_value(resource, "unavailable_until_s")
            if get_value(resource, "availability") != "available" and (
                unavailable_until is None or start_s < unavailable_until
            ):
                problems.append(make_violation("RESOURCE_UNAVAILABLE", f"Ресурс {rid} недоступен",
                                               entity_ids=[rid], operation_ids=[oid]))

        for resource_kind, capability in _resource_need(kind):
            if not any(
                get_value(r, "kind") == resource_kind
                and capability in (get_value(r, "capabilities", []) or [])
                for r in selected
            ):
                problems.append(make_violation("RESOURCE_UNAVAILABLE",
                                               f"Не назначен ресурс {resource_kind}/{capability}",
                                               operation_ids=[oid]))

        if kind == "cargo" and not _cargo_front_ok(track_id, resource_ids, resources):
            problems.append(make_violation("RESOURCE_UNAVAILABLE", "Грузовой фронт не соответствует пути",
                                           entity_ids=[track_id], operation_ids=[oid]))

    required = {
        oid for oid, op in operations.items()
        if get_value(op, "status") == "pending"
    }
    missing = sorted(required - seen) if require_complete else []
    for oid in missing:
        problems.append(make_violation("NO_FEASIBLE_SLOT", "Будущая операция отсутствует в полном плане",
                                       operation_ids=[oid]))

    # Predecessor timing and arrival/early-departure rules.
    actual_end = {
        oid: get_value(op, "actual_end_s")
        for oid, op in operations.items()
        if get_value(op, "status") == "completed"
    }
    for oid, ra in running.items():
        actual_end[oid] = get_value(ra, "end_s")

    for oid, a in by_operation.items():
        op = operations.get(oid)
        if not op:
            continue
        start_s, end_s = a["start_s"], a["end_s"]
        train = trains[get_value(op, "train_id")]

        for pred_id in get_value(op, "predecessor_ids", []) or []:
            pred_end = actual_end.get(pred_id)
            if pred_end is None and pred_id in by_operation:
                pred_end = get_value(by_operation[pred_id], "end_s")
            if pred_end is None or pred_end > start_s:
                problems.append(make_violation("PREDECESSOR_INCOMPLETE",
                                               f"Предшественник {pred_id} не завершён к старту {oid}",
                                               operation_ids=[pred_id, oid]))

        if get_value(op, "kind") == "arrival" and start_s < get_value(train, "expected_arrival_s", 0):
            problems.append(make_violation("NO_FEASIBLE_SLOT", "Приём назначен раньше прибытия поезда",
                                           entity_ids=[get_value(train, "id")], operation_ids=[oid]))

        if get_value(op, "kind") == "departure" and get_value(train, "kind") in ("passenger", "transit"):
            if end_s < get_value(train, "scheduled_departure_s", 0):
                problems.append(make_violation("NO_FEASIBLE_SLOT", "Раннее отправление запрещено",
                                               entity_ids=[get_value(train, "id")], operation_ids=[oid]))

    # Resource and route-zone overlap.
    resource_usage: dict[str, list[tuple[int, int, str]]] = {}
    zone_usage: dict[str, list[tuple[int, int, str]]] = {}

    for oid, a in by_operation.items():
        op = operations.get(oid)
        if not op:
            continue
        for rid in get_value(a, "resource_ids", []) or []:
            resource_usage.setdefault(rid, []).append((a["start_s"], a["end_s"], oid))
        if get_value(op, "kind") in MOVING:
            route = routes.get(get_value(a, "route_id"))
            if route:
                for zone in get_value(route, "conflict_zone_ids", []) or []:
                    zone_usage.setdefault(zone, []).append((a["start_s"], a["end_s"], oid))

    for rid, items in resource_usage.items():
        items.sort()
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if intervals_overlap(items[i][0], items[i][1], items[j][0], items[j][1]):
                    problems.append(make_violation("RESOURCE_UNAVAILABLE",
                                                   f"Ресурс {rid} назначен одновременно",
                                                   entity_ids=[rid],
                                                   operation_ids=[items[i][2], items[j][2]]))

    for zone, items in zone_usage.items():
        items.sort()
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if intervals_overlap(items[i][0], items[i][1], items[j][0], items[j][1]):
                    problems.append(make_violation("ROUTE_BUSY",
                                                   f"Конфликтная зона {zone} используется одновременно",
                                                   entity_ids=[zone],
                                                   operation_ids=[items[i][2], items[j][2]]))

    # Track continuity + hold intervals between movements.
    occupancy: dict[str, list[tuple[int, int, str]]] = {}
    # A train still standing at the end of the plan holds its track to the end of the planning
    # window. The window is computed from the schedule (app.topology.planning_horizon) and is
    # always after now, so holds never become empty intervals late in a run.
    horizon = planning_horizon(snapshot, topology)

    for tid, train in trains.items():
        pending_ops = [
            operations[oid] for oid in by_operation
            if get_value(operations[oid], "train_id") == tid
            and get_value(operations[oid], "status") == "pending"
        ]
        ordered = _topological_for_train(pending_ops)

        current_track = get_value(train, "track_id")
        hold_start = now if current_track else None

        # If a running movement exists, use its destination as the future position.
        for roid, ra in running.items():
            rop = operations.get(roid)
            if rop and get_value(rop, "train_id") == tid and get_value(rop, "kind") in MOVING:
                rr = routes.get(get_value(ra, "route_id"))
                if current_track:
                    occupancy.setdefault(current_track, []).append((now, get_value(ra, "end_s"), tid))
                if rr and get_value(rr, "to_id") in tracks:
                    current_track = get_value(rr, "to_id")
                    hold_start = now
                else:
                    current_track, hold_start = None, None

        for op in ordered:
            oid = get_value(op, "id")
            a = by_operation[oid]
            kind = get_value(op, "kind")
            track_id = get_value(a, "track_id")
            route = routes.get(get_value(a, "route_id")) if kind in MOVING else None

            if kind == "arrival":
                if current_track is not None:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Повторный приём поезда на путь",
                                                   entity_ids=[tid], operation_ids=[oid]))
                if route and get_value(route, "from_id") != entry_node:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Маршрут приёма должен начинаться на входе станции",
                                                   operation_ids=[oid]))
                current_track, hold_start = track_id, a["start_s"]

            elif kind == "departure":
                if current_track != track_id:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Отправление не с текущего пути",
                                                   entity_ids=[tid, track_id], operation_ids=[oid]))
                if current_track is not None:
                    occupancy.setdefault(current_track, []).append((hold_start or now, a["end_s"], tid))
                current_track, hold_start = None, None

            elif kind.startswith("shunt_"):
                if current_track is None or not route or get_value(route, "from_id") != current_track:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Маневровый маршрут не начинается с текущего пути",
                                                   entity_ids=[tid], operation_ids=[oid]))
                if current_track is not None:
                    occupancy.setdefault(current_track, []).append((hold_start or now, a["end_s"], tid))
                current_track, hold_start = track_id, a["start_s"]

            else:
                if current_track != track_id:
                    problems.append(make_violation("NO_FEASIBLE_SLOT", "Неподвижная операция назначена не на текущий путь",
                                                   entity_ids=[tid, track_id], operation_ids=[oid]))

        if current_track is not None:
            occupancy.setdefault(current_track, []).append((hold_start or now, horizon, tid))

    for track_id, items in occupancy.items():
        track = tracks.get(track_id)
        if not track:
            continue
        items.sort()
        closed_until = get_value(track, "closed_until_s")
        for start_s, end_s, tid in items:
            # Closed track may remain occupied; only future entries are prohibited.
            if closed_until and get_value(track, "availability") == "closed":
                train = trains.get(tid)
                if get_value(train, "track_id") != track_id and start_s < closed_until:
                    problems.append(make_violation("TRACK_CLOSED", f"Путь {track_id} закрыт до {closed_until}",
                                                   entity_ids=[track_id, tid], start_s=start_s, end_s=end_s))
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[i][2] == items[j][2]:
                    continue
                if intervals_overlap(items[i][0], items[i][1], items[j][0], items[j][1]):
                    problems.append(make_violation("TRACK_OCCUPIED",
                                                   f"Путь {track_id} удерживается двумя поездами одновременно",
                                                   entity_ids=[track_id, items[i][2], items[j][2]],
                                                   start_s=max(items[i][0], items[j][0]),
                                                   end_s=min(items[i][1], items[j][1])))

    return problems


class RulesAdapter:
    """Object expected by backend.app.simulation.engine.Rules."""

    def can_start(self, context: Json, operation: Json, assignment: Json) -> list[Json]:
        return _can_start_conflicts(context, operation, assignment)

    def validate_plan(self, context: Json, plan: Json, *, require_complete: bool = True) -> list[Json]:
        return validate_plan(context, plan, require_complete=require_complete)


RULES = RulesAdapter()
