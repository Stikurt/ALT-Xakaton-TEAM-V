from __future__ import annotations

import time
import uuid

from app.constraints import get_value, validate_plan
from .calendars import ResourceCalendar
from .strategies import sort_trains


DEFAULT_HORIZON_S = 7200


def build_calendars(snapshot):
    calendars = {
        "tracks": {},
        "resources": {},
        "routes": {},
    }

    for track in get_value(snapshot, "tracks", []):
        track_id = get_value(track, "id")
        calendars["tracks"][track_id] = ResourceCalendar()

    for resource in get_value(snapshot, "resources", []):
        resource_id = get_value(resource, "id")
        calendars["resources"][resource_id] = ResourceCalendar()

    routes = get_value(snapshot, "routes", [])
    topology = get_value(snapshot, "topology")

    if topology:
        topology_routes = get_value(topology, "routes", [])
        if not routes:
            routes = topology_routes

    for route in routes:
        route_id = get_value(route, "id")
        calendars["routes"][route_id] = ResourceCalendar()

    sim_time_s = get_value(snapshot, "sim_time_s", 0)

    for operation in get_value(snapshot, "operations", []):
        status = get_value(operation, "status")
        if status != "running":
            continue

        operation_id = get_value(operation, "id")
        start_s = get_value(operation, "actual_start_s", sim_time_s)
        end_s = get_value(operation, "actual_end_s")

        if end_s is None:
            end_s = get_value(operation, "expected_end_s")

        if end_s is None:
            duration_s = get_value(operation, "duration_s", 0)
            end_s = start_s + duration_s

        track_id = get_value(operation, "track_id")
        if track_id and track_id in calendars["tracks"]:
            reserve_if_possible(
                calendars["tracks"][track_id],
                start_s,
                end_s,
                operation_id,
            )

        resource_ids = get_value(operation, "resource_ids", []) or []
        for resource_id in resource_ids:
            calendar = calendars["resources"].get(resource_id)
            if calendar:
                reserve_if_possible(
                    calendar,
                    start_s,
                    end_s,
                    operation_id,
                )

    return calendars


def reserve_if_possible(calendar, start_s, end_s, owner_id):
    if start_s is None or end_s is None or end_s <= start_s:
        return

    if calendar.is_free(start_s, end_s):
        calendar.reserve(start_s, end_s, owner_id)


def get_train_operations(snapshot, train_id):
    result = []
    for operation in get_value(snapshot, "operations", []):
        if get_value(operation, "train_id") == train_id:
            result.append(operation)
    return result


def topological_operations(operations):
    by_id = {
        get_value(operation, "id"): operation
        for operation in operations
    }

    completed = set()
    result = []

    while len(result) < len(operations):
        available = []

        for operation in operations:
            operation_id = get_value(operation, "id")

            if operation_id in completed:
                continue

            predecessor_ids = (
                get_value(operation, "predecessor_ids", []) or []
            )

            valid = True
            for predecessor_id in predecessor_ids:
                if predecessor_id in by_id and predecessor_id not in completed:
                    valid = False
                    break

            if valid:
                available.append(operation)

        if not available:
            raise ValueError("Обнаружен цикл зависимостей операций.")

        available.sort(
            key=lambda operation: get_value(operation, "id", "")
        )

        selected = available[0]
        selected_id = get_value(selected, "id")
        completed.add(selected_id)
        result.append(selected)

    return result


def suitable_tracks(snapshot, train, operation):
    train_kind = str(get_value(train, "kind", "")).lower()
    operation_kind = str(get_value(operation, "kind", "")).lower()
    train_length = get_value(train, "length_m", 0)
    tracks = get_value(snapshot, "tracks", [])

    candidate_ids = []

    if "passenger" in train_kind or "пассаж" in train_kind:
        candidate_ids = ["P01", "P02"]

    elif "local" in train_kind or "местн" in train_kind:
        if any(
            word in operation_kind
            for word in (
                "cargo",
                "load",
                "unload",
                "груз",
                "погруз",
                "выгруз",
            )
        ):
            candidate_ids = ["P10", "P11"]

        elif any(
            word in operation_kind
            for word in (
                "formation",
                "accumulation",
                "формир",
                "накоп",
            )
        ):
            candidate_ids = ["P07", "P08", "P09"]

        else:
            candidate_ids = ["P03", "P04", "P05", "P06"]

    else:
        candidate_ids = ["P03", "P04", "P05", "P06"]

    result = []

    for track_id in candidate_ids:
        track = next(
            (
                track
                for track in tracks
                if get_value(track, "id") == track_id
            ),
            None,
        )

        if track is None:
            continue

        usable_length_m = get_value(track, "usable_length_m", 0)

        if train_length > usable_length_m:
            continue

        availability = get_value(track, "availability", "open")

        if availability == "closed":
            continue

        result.append(track_id)

    return result


def required_resource_kinds(operation):
    operation_kind = str(get_value(operation, "kind", "")).lower()
    result = []

    if any(
        word in operation_kind
        for word in (
            "shunt",
            "maneuver",
            "move",
            "маневр",
            "подача",
            "уборка",
            "перестанов",
        )
    ):
        result.extend([
            "shunting_locomotive",
            "shunting_crew",
        ])

    if any(
        word in operation_kind
        for word in (
            "inspect",
            "inspection",
            "prepare",
            "preparation",
            "осмотр",
            "подготов",
        )
    ):
        result.append("inspection_crew")

    if any(
        word in operation_kind
        for word in (
            "formation",
            "формир",
        )
    ):
        result.append("shunting_crew")

    return result


def resources_by_kind(snapshot, kind):
    result = []

    for resource in get_value(snapshot, "resources", []):
        resource_kind = get_value(resource, "kind", "")

        if resource_kind == kind:
            result.append(get_value(resource, "id"))

    return sorted(result)


def find_common_slot(
    calendars,
    resource_keys,
    earliest_s,
    duration_s,
    horizon_s,
):
    candidate = earliest_s

    while candidate + duration_s <= horizon_s:
        next_candidate = candidate
        all_available = True

        for group, resource_id in resource_keys:
            calendar = calendars[group].get(resource_id)

            if calendar is None:
                continue

            free_at = calendar.next_free_time(
                candidate,
                duration_s,
            )

            if free_at != candidate:
                all_available = False
                next_candidate = max(next_candidate, free_at)

        if all_available:
            return candidate

        candidate = next_candidate

    return None


def schedule_train(snapshot, train, calendars, horizon_s):
    train_id = get_value(train, "id")
    operations = get_train_operations(snapshot, train_id)
    operations = topological_operations(operations)

    assignments = []

    sim_time_s = get_value(snapshot, "sim_time_s", 0)
    expected_arrival_s = get_value(train, "expected_arrival_s")

    if expected_arrival_s is None:
        expected_arrival_s = get_value(
            train,
            "scheduled_arrival_s",
            sim_time_s,
        )

    current_s = max(
        sim_time_s,
        expected_arrival_s or sim_time_s,
    )

    for operation in operations:
        operation_id = get_value(operation, "id")
        status = get_value(operation, "status", "pending")

        if status == "completed":
            continue

        if status == "running":
            continue

        duration_s = get_value(operation, "duration_s", 0)

        if duration_s <= 0:
            return None, {
                "train_id": train_id,
                "operation_id": operation_id,
                "reason": "INVALID_DURATION",
            }

        candidate_tracks = suitable_tracks(
            snapshot,
            train,
            operation,
        )

        if not candidate_tracks:
            candidate_tracks = [None]

        required_kinds = required_resource_kinds(operation)
        scheduled = None

        for track_id in sorted(
            candidate_tracks,
            key=lambda value: value or "",
        ):
            selected_resources = []
            possible = True

            for kind in required_kinds:
                candidates = resources_by_kind(snapshot, kind)
                selected = None

                for resource_id in candidates:
                    already_selected = {
                        item[1]
                        for item in selected_resources
                    }

                    if resource_id not in already_selected:
                        selected = resource_id
                        break

                if selected is None:
                    possible = False
                    break

                selected_resources.append(
                    ("resources", selected)
                )

            if not possible:
                continue

            calendar_keys = list(selected_resources)

            if track_id:
                calendar_keys.append(
                    ("tracks", track_id)
                )

            start_s = find_common_slot(
                calendars,
                calendar_keys,
                current_s,
                duration_s,
                horizon_s,
            )

            if start_s is None:
                continue

            end_s = start_s + duration_s

            operation_kind = str(
                get_value(operation, "kind", "")
            ).lower()

            is_departure = (
                "departure" in operation_kind
                or "отправ" in operation_kind
            )

            if is_departure:
                scheduled_departure_s = (
                    get_value(
                        train,
                        "scheduled_departure_s",
                        0,
                    )
                    or 0
                )

                if start_s < scheduled_departure_s:
                    start_s = find_common_slot(
                        calendars,
                        calendar_keys,
                        scheduled_departure_s,
                        duration_s,
                        horizon_s,
                    )

                    if start_s is None:
                        continue

                    end_s = start_s + duration_s

            scheduled = {
                "operation_id": operation_id,
                "start_s": start_s,
                "end_s": end_s,
                "track_id": track_id,
                "route_id": None,
                "resource_ids": [
                    resource_id
                    for _, resource_id in selected_resources
                ],
            }

            for group, resource_id in calendar_keys:
                calendars[group][resource_id].reserve(
                    start_s,
                    end_s,
                    operation_id,
                )

            break

        if scheduled is None:
            return None, {
                "train_id": train_id,
                "operation_id": operation_id,
                "reason": "NO_FEASIBLE_SLOT",
            }

        assignments.append(scheduled)
        current_s = scheduled["end_s"]

    return assignments, None


def calculate_plan_metrics(snapshot, assignments, unassigned):
    assignments_by_operation = {
        assignment["operation_id"]: assignment
        for assignment in assignments
    }

    delays = []

    for train in get_value(snapshot, "trains", []):
        train_id = get_value(train, "id")
        scheduled_departure_s = get_value(
            train,
            "scheduled_departure_s",
        )

        if scheduled_departure_s is None:
            continue

        operations = get_train_operations(snapshot, train_id)
        departure_assignment = None

        for operation in operations:
            kind = str(
                get_value(operation, "kind", "")
            ).lower()

            if "departure" in kind or "отправ" in kind:
                departure_assignment = (
                    assignments_by_operation.get(
                        get_value(operation, "id")
                    )
                )

        if departure_assignment:
            predicted_departure_s = departure_assignment["end_s"]
            delay_s = max(
                0,
                predicted_departure_s - scheduled_departure_s,
            )
            delays.append(delay_s)

    return {
        "total_positive_delay_s": sum(delays),
        "max_delay_s": max(delays) if delays else 0,
        "unassigned_count": len(unassigned),
        "changed_future_assignments": 0,
    }


def plan(
    snapshot,
    config,
    strategy: str,
    budget_s: float = 2.0,
):
    started_at = time.monotonic()

    sim_time_s = get_value(snapshot, "sim_time_s", 0)
    state_version = get_value(snapshot, "state_version", 0)
    run_id = get_value(snapshot, "run_id", "")

    planning_horizon_s = get_value(
        config,
        "planning_horizon_s",
        DEFAULT_HORIZON_S,
    )

    horizon_s = sim_time_s + planning_horizon_s
    calendars = build_calendars(snapshot)

    trains = sort_trains(
        get_value(snapshot, "trains", []),
        strategy,
    )

    assignments = []
    unassigned = []
    timed_out = False

    for train in trains:
        elapsed = time.monotonic() - started_at

        if elapsed >= budget_s:
            timed_out = True
            break

        trial_calendars = {
            group: {
                resource_id: calendar.clone()
                for resource_id, calendar in group_calendars.items()
            }
            for group, group_calendars in calendars.items()
        }

        train_assignments, error = schedule_train(
            snapshot,
            train,
            trial_calendars,
            horizon_s,
        )

        if error:
            unassigned.append(error)
            continue

        calendars = trial_calendars
        assignments.extend(train_assignments)

    if timed_out:
        operation_to_train = {
            get_value(operation, "id"): get_value(operation, "train_id")
            for operation in get_value(snapshot, "operations", [])
        }

        assigned_train_ids = set()

        for assignment in assignments:
            train_id = operation_to_train.get(
                assignment["operation_id"]
            )

            if train_id:
                assigned_train_ids.add(train_id)

        already_unassigned = {
            item["train_id"]
            for item in unassigned
        }

        for train in trains:
            train_id = get_value(train, "id")

            if (
                train_id not in assigned_train_ids
                and train_id not in already_unassigned
            ):
                unassigned.append({
                    "train_id": train_id,
                    "reason": "TIMEOUT",
                })

    metrics = calculate_plan_metrics(
        snapshot,
        assignments,
        unassigned,
    )

    result = {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "based_on_version": state_version,
        "strategy": strategy,
        "status": "partial" if unassigned else "feasible",
        "assignments": assignments,
        "unassigned": unassigned,
        "metrics": metrics,
        "explanations": [],
        "timed_out": timed_out,
    }

    violations = validate_plan(
        snapshot,
        result,
    )

    if violations:
        result["status"] = "infeasible"
        result["violations"] = violations
    else:
        result["violations"] = []

    result["calculation_time_ms"] = round(
        (time.monotonic() - started_at) * 1000,
        2,
    )

    return result
