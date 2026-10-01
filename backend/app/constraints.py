from __future__ import annotations

from typing import Any


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


def find_track(snapshot, track_id: str):
    for track in get_value(snapshot, "tracks", []):
        if get_value(track, "id") == track_id:
            return track
    return None


def find_route(snapshot, route_id: str):
    for route in get_value(snapshot, "routes", []):
        if get_value(route, "id") == route_id:
            return route

    topology = get_value(snapshot, "topology")
    if topology:
        for route in get_value(topology, "routes", []):
            if get_value(route, "id") == route_id:
                return route
    return None


def find_train(snapshot, train_id: str):
    for train in get_value(snapshot, "trains", []):
        if get_value(train, "id") == train_id:
            return train
    return None


def find_resource(snapshot, resource_id: str):
    for resource in get_value(snapshot, "resources", []):
        if get_value(resource, "id") == resource_id:
            return resource
    return None


def find_operation(snapshot, operation_id: str):
    for operation in get_value(snapshot, "operations", []):
        if get_value(operation, "id") == operation_id:
            return operation
    return None


def can_start(snapshot, assignment):
    reasons = []

    operation_id = get_value(assignment, "operation_id")
    start_s = get_value(assignment, "start_s")
    end_s = get_value(assignment, "end_s")
    track_id = get_value(assignment, "track_id")
    route_id = get_value(assignment, "route_id")
    resource_ids = get_value(assignment, "resource_ids", []) or []
    sim_time_s = get_value(snapshot, "sim_time_s", 0)

    operation = find_operation(snapshot, operation_id)

    if operation is None:
        reasons.append(
            make_violation(
                "UNKNOWN_OPERATION",
                f"Операция {operation_id} не существует.",
                operation_ids=[operation_id],
            )
        )
        return {"allowed": False, "reasons": reasons}

    train_id = get_value(operation, "train_id")
    train = find_train(snapshot, train_id)

    if start_s is None or end_s is None:
        reasons.append(
            make_violation(
                "INVALID_INTERVAL",
                "У назначения отсутствует start_s или end_s.",
                operation_ids=[operation_id],
            )
        )
        return {"allowed": False, "reasons": reasons}

    if end_s <= start_s:
        reasons.append(
            make_violation(
                "INVALID_INTERVAL",
                "Интервал должен иметь положительную длительность.",
                operation_ids=[operation_id],
                start_s=start_s,
                end_s=end_s,
            )
        )

    if start_s < sim_time_s:
        reasons.append(
            make_violation(
                "START_IN_PAST",
                (
                    f"Операция {operation_id} начинается "
                    f"в {start_s} сек., но текущее время {sim_time_s} сек."
                ),
                operation_ids=[operation_id],
                start_s=start_s,
                end_s=end_s,
            )
        )

    predecessor_ids = get_value(operation, "predecessor_ids", []) or []

    for predecessor_id in predecessor_ids:
        predecessor = find_operation(snapshot, predecessor_id)

        if predecessor is None:
            reasons.append(
                make_violation(
                    "PREDECESSOR_INCOMPLETE",
                    f"Предшественник {predecessor_id} отсутствует.",
                    operation_ids=[predecessor_id, operation_id],
                )
            )
            continue

        predecessor_status = get_value(predecessor, "status")
        predecessor_end = get_value(predecessor, "actual_end_s")

        if predecessor_status != "completed" and predecessor_end is None:
            reasons.append(
                make_violation(
                    "PREDECESSOR_INCOMPLETE",
                    (
                        f"Операция {operation_id} не может начаться, "
                        f"потому что {predecessor_id} ещё не завершена."
                    ),
                    operation_ids=[predecessor_id, operation_id],
                )
            )

    if track_id:
        track = find_track(snapshot, track_id)

        if track is None:
            reasons.append(
                make_violation(
                    "UNKNOWN_TRACK",
                    f"Путь {track_id} не существует.",
                    entity_ids=[track_id],
                    operation_ids=[operation_id],
                )
            )
        else:
            availability = get_value(track, "availability", "open")
            closed_until_s = get_value(track, "closed_until_s", 0) or 0

            if availability == "closed" and start_s < closed_until_s:
                reasons.append(
                    make_violation(
                        "TRACK_CLOSED",
                        f"Путь {track_id} закрыт до {closed_until_s} сек.",
                        entity_ids=[track_id],
                        operation_ids=[operation_id],
                        start_s=start_s,
                        end_s=end_s,
                    )
                )

            occupant_train_id = get_value(track, "occupant_train_id")

            if occupant_train_id is not None and occupant_train_id != train_id:
                reasons.append(
                    make_violation(
                        "TRACK_OCCUPIED",
                        f"Путь {track_id} уже занят поездом {occupant_train_id}.",
                        entity_ids=[track_id, occupant_train_id],
                        operation_ids=[operation_id],
                    )
                )

            if train is not None:
                train_length = get_value(train, "length_m", 0)
                track_length = get_value(track, "usable_length_m", 0)

                if train_length > track_length:
                    reasons.append(
                        make_violation(
                            "TRACK_TOO_SHORT",
                            (
                                f"Поезд {train_id} имеет длину {train_length} м, "
                                f"а путь {track_id} имеет длину {track_length} м."
                            ),
                            entity_ids=[train_id, track_id],
                            operation_ids=[operation_id],
                        )
                    )

    for resource_id in resource_ids:
        resource = find_resource(snapshot, resource_id)

        if resource is None:
            reasons.append(
                make_violation(
                    "UNKNOWN_RESOURCE",
                    f"Ресурс {resource_id} не существует.",
                    entity_ids=[resource_id],
                    operation_ids=[operation_id],
                )
            )
            continue

        availability = get_value(resource, "availability", "available")
        unavailable_until_s = get_value(resource, "unavailable_until_s", 0) or 0

        if availability != "available" and start_s < unavailable_until_s:
            reasons.append(
                make_violation(
                    "RESOURCE_UNAVAILABLE",
                    f"Ресурс {resource_id} недоступен до {unavailable_until_s} сек.",
                    entity_ids=[resource_id],
                    operation_ids=[operation_id],
                )
            )

        active_operation_id = get_value(resource, "active_operation_id")

        if active_operation_id is not None and active_operation_id != operation_id:
            reasons.append(
                make_violation(
                    "RESOURCE_UNAVAILABLE",
                    (
                        f"Ресурс {resource_id} уже используется "
                        f"операцией {active_operation_id}."
                    ),
                    entity_ids=[resource_id],
                    operation_ids=[operation_id, active_operation_id],
                )
            )

    if route_id:
        route = find_route(snapshot, route_id)

        if route is None:
            reasons.append(
                make_violation(
                    "UNKNOWN_ROUTE",
                    f"Маршрут {route_id} не существует.",
                    entity_ids=[route_id],
                    operation_ids=[operation_id],
                )
            )

    return {"allowed": len(reasons) == 0, "reasons": reasons}


def validate_plan(snapshot, plan):
    violations = []
    assignments = get_value(plan, "assignments", []) or []

    for assignment in assignments:
        result = can_start(snapshot, assignment)
        violations.extend(result["reasons"])

    operation_assignments = {}

    for assignment in assignments:
        operation_id = get_value(assignment, "operation_id")

        if operation_id in operation_assignments:
            violations.append(
                make_violation(
                    "DOUBLE_ASSIGNMENT",
                    f"Операция {operation_id} назначена более одного раза.",
                    operation_ids=[operation_id],
                )
            )

        operation_assignments[operation_id] = assignment

    for i in range(len(assignments)):
        assignment_a = assignments[i]

        for j in range(i + 1, len(assignments)):
            assignment_b = assignments[j]

            start_a = get_value(assignment_a, "start_s")
            end_a = get_value(assignment_a, "end_s")
            start_b = get_value(assignment_b, "start_s")
            end_b = get_value(assignment_b, "end_s")

            if None in (start_a, end_a, start_b, end_b):
                continue

            if not intervals_overlap(start_a, end_a, start_b, end_b):
                continue

            track_a = get_value(assignment_a, "track_id")
            track_b = get_value(assignment_b, "track_id")

            if track_a and track_a == track_b:
                violations.append(
                    make_violation(
                        "TRACK_OCCUPIED",
                        f"Путь {track_a} назначен двум операциям одновременно.",
                        entity_ids=[track_a],
                        operation_ids=[
                            get_value(assignment_a, "operation_id"),
                            get_value(assignment_b, "operation_id"),
                        ],
                        start_s=max(start_a, start_b),
                        end_s=min(end_a, end_b),
                    )
                )

            resources_a = set(get_value(assignment_a, "resource_ids", []) or [])
            resources_b = set(get_value(assignment_b, "resource_ids", []) or [])

            for resource_id in resources_a & resources_b:
                violations.append(
                    make_violation(
                        "RESOURCE_UNAVAILABLE",
                        (
                            f"Ресурс {resource_id} используется одновременно "
                            f"двумя операциями."
                        ),
                        entity_ids=[resource_id],
                        operation_ids=[
                            get_value(assignment_a, "operation_id"),
                            get_value(assignment_b, "operation_id"),
                        ],
                    )
                )

            route_a_id = get_value(assignment_a, "route_id")
            route_b_id = get_value(assignment_b, "route_id")

            if route_a_id and route_b_id:
                route_a = find_route(snapshot, route_a_id)
                route_b = find_route(snapshot, route_b_id)

                if route_a and route_b:
                    zones_a = set(get_value(route_a, "conflict_zone_ids", []) or [])
                    zones_b = set(get_value(route_b, "conflict_zone_ids", []) or [])

                    if zones_a & zones_b:
                        violations.append(
                            make_violation(
                                "ROUTE_BUSY",
                                f"Маршруты {route_a_id} и {route_b_id} конфликтуют.",
                                entity_ids=[route_a_id, route_b_id],
                                operation_ids=[
                                    get_value(assignment_a, "operation_id"),
                                    get_value(assignment_b, "operation_id"),
                                ],
                            )
                        )

    for assignment in assignments:
        operation_id = get_value(assignment, "operation_id")
        operation = find_operation(snapshot, operation_id)

        if operation is None:
            continue

        current_start = get_value(assignment, "start_s")
        predecessor_ids = get_value(operation, "predecessor_ids", []) or []

        for predecessor_id in predecessor_ids:
            predecessor = find_operation(snapshot, predecessor_id)

            if predecessor and get_value(predecessor, "status") == "completed":
                continue

            predecessor_assignment = operation_assignments.get(predecessor_id)

            if predecessor_assignment is None:
                violations.append(
                    make_violation(
                        "PREDECESSOR_INCOMPLETE",
                        (
                            f"Для операции {operation_id} не запланирован "
                            f"предшественник {predecessor_id}."
                        ),
                        operation_ids=[predecessor_id, operation_id],
                    )
                )
                continue

            predecessor_end = get_value(predecessor_assignment, "end_s")

            if (
                predecessor_end is not None
                and current_start is not None
                and predecessor_end > current_start
            ):
                violations.append(
                    make_violation(
                        "PREDECESSOR_INCOMPLETE",
                        (
                            f"Операция {operation_id} начинается раньше "
                            f"завершения {predecessor_id}."
                        ),
                        operation_ids=[predecessor_id, operation_id],
                    )
                )

    return violations
