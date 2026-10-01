from __future__ import annotations


def get_value(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def sort_trains(trains, strategy: str):
    trains = list(trains)

    if strategy == "passenger_first":
        return sorted(
            trains,
            key=lambda train: (
                -get_value(train, "priority", 0),
                get_value(train, "scheduled_departure_s", 10**12),
                get_value(train, "id", ""),
            ),
        )

    if strategy == "earliest_departure":
        return sorted(
            trains,
            key=lambda train: (
                get_value(train, "scheduled_departure_s", 10**12),
                -get_value(train, "priority", 0),
                get_value(train, "id", ""),
            ),
        )

    raise ValueError(f"Unknown planner strategy: {strategy}")
