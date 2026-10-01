from __future__ import annotations

import itertools
import time
import uuid

from app.constraints import _expected_track_kind, _resource_need, get_value, make_violation, validate_plan
from app.topology import MOVING, TopologyError, cargo_front_for, planning_horizon, station_boundary
from .calendars import ResourceCalendar
from .strategies import sort_trains


class PlannerInputError(ValueError):
    """Input the planner cannot work with; plan() turns it into an infeasible plan with a reason."""


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


def _boundary(config, snapshot):
    tracks = [get_value(t, "id") for t in get_value(snapshot, "tracks", []) or []]
    routes = _routes(config, snapshot)
    try:
        return station_boundary({"routes": routes, "boundary": get_value(config, "boundary")}, tracks)
    except TopologyError as exc:
        raise PlannerInputError(str(exc)) from exc


def build_calendars(snapshot, config, horizon=None):
    now = get_value(snapshot, "sim_time_s", 0)
    if horizon is None:
        horizon = planning_horizon(snapshot, config)
    _, exit_node = _boundary(config, snapshot)

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
                train_id = get_value(op, "train_id")
                source,target = get_value(route,"from_id"),get_value(route,"to_id")
                if source in calendars['tracks']:
                    calendars['tracks'][source].remove_owner(f"train:{train_id}")
                    closed_until=get_value(_track_map(snapshot).get(source),'closed_until_s',0) or 0
                    free_after=max(now,closed_until)
                    if free_after<end_s:
                        calendars['tracks'][source].reserve(free_after,end_s,f"running:{oid}",allow_same_owner=True)
                if target in calendars['tracks'] and target != exit_node:
                    calendars['tracks'][target].reserve(now,horizon,f"train:{train_id}",allow_same_owner=True)
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


def _train_ordinal(snapshot, train, kind):
    """Position of the train among trains of the same kind (deterministic rotation key)."""
    peers = sorted(
        [
            t for t in get_value(snapshot, "trains", [])
            if get_value(t, "kind") == kind
        ],
        key=lambda t: get_value(t, "id", ""),
    )
    train_id = get_value(train, "id")
    for index, peer in enumerate(peers):
        if get_value(peer, "id") == train_id:
            return index
    return 0


def _rotate(items, offset):
    items = list(items)
    if not items:
        return items
    k = offset % len(items)
    return items[k:] + items[:k]


def _track_preference(track, train_kind):
    """Optional station hint: tracks that list the train kind in ``preferred_train_kinds`` come
    first, tracks that list other kinds only come last. Without hints all tracks rank equally."""
    kinds = get_value(track, "preferred_train_kinds") or []
    if not kinds:
        return 1
    return 0 if train_kind in kinds else 2


def _candidate_tracks(snapshot, train, operation):
    """Tracks suitable for the operation, taken from the station data.

    The required track kind comes from the shared rule table (constraints._expected_track_kind);
    tracks shorter than the train are skipped. Ordering is deterministic: station preference hint
    (``preferred_train_kinds``), then a rotation by the train's ordinal among trains of its kind so
    that consecutive trains spread over equivalent tracks, then station order.
    """
    kind = get_value(operation, "kind", "")
    if kind not in MOVING or kind == "departure":
        return []
    train_kind = get_value(train, "kind", "")
    expected = _expected_track_kind(train_kind, kind)
    if expected is None:
        return []
    length = get_value(train, "length_m", 0)
    if not isinstance(length, (int, float)) or isinstance(length, bool):
        raise PlannerInputError(f"Поезд {get_value(train, 'id')}: length_m должен быть числом")
    pool = [
        get_value(t, "id") for t in get_value(snapshot, "tracks", []) or []
        if get_value(t, "kind") == expected and (get_value(t, "usable_length_m", 0) or 0) >= length
    ]
    tracks = _track_map(snapshot)
    ranks = sorted({_track_preference(tracks[tid], train_kind) for tid in pool})
    ordinal = _train_ordinal(snapshot, train, train_kind)
    result = []
    for rank in ranks:
        group = [tid for tid in pool if _track_preference(tracks[tid], train_kind) == rank]
        result.extend(_rotate(group, ordinal))
    return result


def _route_id(config, snapshot, from_id, to_id):
    for route in _routes(config, snapshot):
        if get_value(route, "from_id") == from_id and get_value(route, "to_id") == to_id:
            return get_value(route, "id")
    return None


def _matching_resources(snapshot, kind, capability):
    return [
        get_value(r, "id") for r in get_value(snapshot, "resources", []) or []
        if get_value(r, "kind") == kind and capability in (get_value(r, "capabilities", []) or [])
    ]


def _resource_options(snapshot, train, operation_kind, track_id):
    """Resource sets that can serve the operation, from resource kinds and capabilities.

    The need per operation comes from the shared rule table (constraints._resource_need), e.g.
    shunting = one locomotive + one crew with the ``shunt`` capability. Options are combinations
    of matching resources; the k-th train of a kind starts from the k-th option, so equal work
    is spread over equal resources deterministically. A cargo operation uses the cargo front that
    serves the track (topology.cargo_front_for).
    """
    need = _resource_need(operation_kind)
    if not need:
        return [()]
    if operation_kind == "cargo":
        rid = cargo_front_for(track_id, get_value(snapshot, "resources", []) or [])
        return [(rid,)] if rid else []
    pools = [sorted(_matching_resources(snapshot, kind, cap)) for kind, cap in need]
    if any(not pool for pool in pools):
        return []
    ordinal = _train_ordinal(snapshot, train, get_value(train, "kind", ""))
    if len(pools) == 1:
        return [(rid,) for rid in _rotate(pools[0], ordinal)]
    # Pair the i-th resource of each pool first (L1+B1, L2+B2, ...), then the remaining mixes.
    width = max(len(pool) for pool in pools)
    paired = [tuple(pool[i % len(pool)] for pool in pools) for i in range(width)]
    rest = [combo for combo in itertools.product(*pools) if combo not in paired]
    return _rotate(paired, ordinal) + rest


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


def _future_position(snapshot, config, train):
    now = get_value(snapshot,"sim_time_s",0)
    track = get_value(train,"track_id")
    operations = _operation_map(snapshot)
    routes = _route_map(config,snapshot)
    for oid,a in (get_value(snapshot,"running_assignments",{}) or {}).items():
        op = operations.get(oid)
        if op and get_value(op,'train_id')==get_value(train,'id'):
            now = max(now,get_value(a,'end_s',now))
            route = routes.get(get_value(a,'route_id'))
            if route:
                target=get_value(route,'to_id')
                track = target if target in _track_map(snapshot) else None
    return now,track


def _occupancy_intervals(snapshot, config, train, assignments):
    routes = _route_map(config, snapshot)
    _,current_track = _future_position(snapshot,config,train)
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


def _restore_train_placeholder(calendars, snapshot, config, train, horizon):
    track_id = get_value(train, "track_id")
    if not track_id or track_id not in calendars["tracks"]:
        return
    now = get_value(snapshot, "sim_time_s", 0)
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

    available_s,current_track = _future_position(snapshot,config,train)
    current_s = max(current_s,available_s)
    entry_node, exit_node = _boundary(config, snapshot)
    raw_assignments = []
    trial = {
        group: {ident: cal.clone() for ident, cal in values.items()}
        for group, values in calendars.items()
    }

    for operation_index, operation in enumerate(operations):
        oid = get_value(operation, "id")
        kind = get_value(operation, "kind")
        duration = get_value(operation, "duration_s", 0)

        if type(duration) is not int:
            raise PlannerInputError(f"Операция {oid}: duration_s должен быть целым числом")
        if duration <= 0:
            _restore_train_placeholder(calendars, snapshot, config, train, horizon_s)
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
        for track_rank, track_id in enumerate(track_candidates):
            if not track_id:
                continue

            if kind == "arrival":
                route_id = _route_id(config, snapshot, entry_node, track_id)
            elif kind.startswith("shunt_"):
                route_id = _route_id(config, snapshot, current_track, track_id)
            elif kind == "departure":
                route_id = _route_id(config, snapshot, current_track, exit_node)
            else:
                route_id = None

            if kind in MOVING and route_id is None:
                continue

            for resource_rank, resources in enumerate(
                _resource_options(snapshot, train, kind, track_id)
            ):
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
                    track_rank,
                    start_s,
                    resource_rank,
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
            _restore_train_placeholder(calendars, snapshot, config, train, horizon_s)
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
            _restore_train_placeholder(calendars, snapshot, config, train, horizon_s)
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


TRAIN_KINDS = {"passenger", "transit", "local"}


def _is_int(value):
    return type(value) is int


def validate_input(snapshot, config):
    """Structural checks of planner input. Returns a list of violations (empty = usable)."""
    problems = []

    def bad(message, *, entity_ids=None, operation_ids=None):
        problems.append(make_violation("INVALID_INPUT", message, entity_ids=entity_ids, operation_ids=operation_ids))

    if not isinstance(snapshot, dict):
        bad("Снимок станции отсутствует или не является объектом")
        return problems
    if config is not None and not isinstance(config, dict):
        bad("Конфигурация станции должна быть объектом")
        return problems
    for key in ("trains", "tracks", "operations"):
        if not isinstance(snapshot.get(key), list):
            bad(f"В снимке нет списка {key}")
    if problems:
        return problems
    if not _is_int(snapshot.get("sim_time_s", 0)) or snapshot.get("sim_time_s", 0) < 0:
        bad("sim_time_s должен быть целым неотрицательным числом")
    if not _routes(config or {}, snapshot):
        bad("В конфигурации станции нет маршрутов")
    seen = set()
    for train in snapshot["trains"]:
        tid = get_value(train, "id")
        if not isinstance(tid, str) or not tid:
            bad("Поезд без id")
            continue
        if tid in seen:
            bad(f"Повторный id поезда {tid}", entity_ids=[tid])
        seen.add(tid)
        if get_value(train, "kind") not in TRAIN_KINDS:
            bad(f"Поезд {tid}: неизвестный тип {get_value(train, 'kind')!r}", entity_ids=[tid])
        length = get_value(train, "length_m")
        if not _is_int(length) or length <= 0:
            bad(f"Поезд {tid}: length_m должен быть целым положительным числом", entity_ids=[tid])
        arrival = get_value(train, "expected_arrival_s", get_value(train, "scheduled_arrival_s"))
        departure = get_value(train, "scheduled_departure_s")
        if not _is_int(get_value(train, "scheduled_arrival_s")) or not _is_int(departure) or not _is_int(arrival):
            bad(f"Поезд {tid}: время прибытия/отправления должно быть целым числом", entity_ids=[tid])
        elif departure < get_value(train, "scheduled_arrival_s"):
            bad(f"Поезд {tid}: плановое отправление раньше планового прибытия", entity_ids=[tid])
    op_ids = set()
    by_train = {}
    for op in snapshot["operations"]:
        oid = get_value(op, "id")
        if not isinstance(oid, str) or not oid or oid in op_ids:
            bad(f"Пустой или повторный id операции {oid!r}")
            continue
        op_ids.add(oid)
        if get_value(op, "train_id") not in seen:
            bad(f"Операция {oid}: неизвестный поезд", operation_ids=[oid])
        duration = get_value(op, "duration_s")
        if not _is_int(duration) or duration <= 0:
            bad(f"Операция {oid}: duration_s должен быть целым положительным числом", operation_ids=[oid])
        if not isinstance(get_value(op, "predecessor_ids", []) or [], list):
            bad(f"Операция {oid}: predecessor_ids должен быть списком", operation_ids=[oid])
        by_train.setdefault(get_value(op, "train_id"), []).append(op)
    for tid, ops in by_train.items():
        try:
            topological_operations(ops)
        except ValueError:
            bad(f"Поезд {tid}: цикл зависимостей операций", entity_ids=[tid] if tid else [])
    for track in snapshot["tracks"]:
        length = get_value(track, "usable_length_m")
        if not _is_int(length) or length <= 0:
            bad(f"Путь {get_value(track, 'id')}: usable_length_m должен быть целым положительным числом",
                entity_ids=[get_value(track, "id")] if get_value(track, "id") else [])
    if not problems:
        try:
            _boundary(config or {}, snapshot)
        except PlannerInputError as exc:
            bad(str(exc))
    return problems


def _rejected_plan(snapshot, strategy, violations, started_at):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    trains = [t for t in snapshot.get("trains") or [] if isinstance(t, dict)]
    return {
        "id": str(uuid.uuid4()),
        "run_id": get_value(snapshot, "run_id", ""),
        "based_on_version": get_value(snapshot, "state_version", 0),
        "strategy": strategy,
        "status": "infeasible",
        "assignments": [],
        "unassigned": [{"train_id": get_value(t, "id"), "reason": "INVALID_INPUT"} for t in trains if get_value(t, "id")],
        "metrics": {"total_positive_delay_s": 0, "max_delay_s": 0, "unassigned_count": len(trains),
                    "changed_future_assignments": 0},
        "explanations": [{"train_id": None, "reason_code": "INVALID_INPUT", "message": v["message"]} for v in violations],
        "timed_out": False,
        "violations": violations,
        "horizon_s": None,
        "calculation_time_ms": round((time.monotonic() - started_at) * 1000, 2),
    }


def plan(snapshot, config, strategy: str, budget_s: float = 2.0):
    """Build a complete plan for all pending operations.

    Never raises on bad input: structural problems (missing lists, None or non-integer durations
    and lengths, unknown train kinds, duplicate ids, dependency cycles, departure before arrival,
    unknown strategy) come back as an ``infeasible`` plan with ``INVALID_INPUT`` violations.
    Status: ``feasible`` = every pending operation placed and no rule violated; ``partial`` = some
    trains could not be placed (``unassigned``) but everything placed obeys the rules;
    ``infeasible`` = a rule violation or unusable input.
    """
    started_at = time.monotonic()
    if strategy not in ("passenger_first", "earliest_departure"):
        return _rejected_plan(snapshot, strategy, [make_violation(
            "INVALID_INPUT", f"Неизвестная стратегия планировщика: {strategy}")], started_at)
    problems = validate_input(snapshot, config)
    if problems:
        return _rejected_plan(snapshot, strategy, problems, started_at)
    config = config or {}
    run_id = get_value(snapshot, "run_id", "")
    state_version = get_value(snapshot, "state_version", 0)

    horizon_s = planning_horizon(snapshot, config)
    base_calendars = build_calendars(snapshot, config, horizon_s)
    trains = sort_trains(get_value(snapshot, "trains", []), strategy)

    def greedy(order):
        calendars = {g: {k: c.clone() for k, c in v.items()} for g, v in base_calendars.items()}
        placed, failed, out_of_time = [], [], False
        for train in order:
            if time.monotonic() - started_at >= budget_s:
                out_of_time = True
                break
            result, error = schedule_train(snapshot, config, train, calendars, horizon_s)
            if error:
                failed.append(error)
                continue
            train_assignments, calendars = result
            placed.extend(train_assignments)
        if out_of_time:
            operations = _operation_map(snapshot)
            done = {get_value(operations[a["operation_id"]], "train_id") for a in placed
                    if a["operation_id"] in operations}
            known = {x["train_id"] for x in failed}
            failed.extend({"train_id": get_value(t, "id"), "reason": "TIMEOUT"} for t in order
                          if get_value(t, "id") not in done and get_value(t, "id") not in known)
        return placed, failed, out_of_time

    def score(attempt):
        placed, failed, _ = attempt
        return (len(failed), calculate_plan_metrics(snapshot, placed, failed)["total_positive_delay_s"])

    # Greedy pass in strategy order. Trains that could not be placed are moved to the front of
    # the queue and the pass is repeated (bounded by the number of trains and by budget_s); the
    # best attempt wins. Deterministic for the same input.
    order = list(trains)
    best = greedy(order)
    tried = {tuple(get_value(t, "id") for t in order)}
    for _ in range(len(trains)):
        if not best[1] or best[2] or time.monotonic() - started_at >= budget_s:
            break
        failed_ids = [x["train_id"] for x in best[1] if x.get("reason") != "TIMEOUT"]
        if not failed_ids:
            break
        front = [t for t in order if get_value(t, "id") in failed_ids]
        order = front + [t for t in order if get_value(t, "id") not in failed_ids]
        key = tuple(get_value(t, "id") for t in order)
        if key in tried:
            break
        tried.add(key)
        attempt = greedy(order)
        if score(attempt) < score(best):
            best = attempt
    assignments, unassigned, timed_out = best

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
        "horizon_s": horizon_s,
    }

    context = {
        "snapshot": snapshot,
        "topology": config,
        "reservations": get_value(snapshot,"reservations",{}) or {},
        "busy_zones": get_value(snapshot,"busy_zones",{}) or {},
        "running_assignments": get_value(snapshot, "running_assignments", {}) or {},
    }

    # Rule violations make the plan infeasible. Operations that were simply not placed are
    # reported in ``unassigned`` and make it partial (validate_plan(require_complete=True), used
    # when a plan is accepted, still rejects anything but a complete plan).
    violations = validate_plan(context, result, require_complete=False)
    result["violations"] = violations

    if violations:
        result["status"] = "infeasible"

    result["calculation_time_ms"] = round(
        (time.monotonic() - started_at) * 1000,
        2,
    )

    return result
