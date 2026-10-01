from __future__ import annotations


DEFAULT_WEIGHTS = {
    "delay": 0.35,
    "departures": 0.25,
    "track_utilization": 0.15,
    "conflicts": 0.15,
    "waiting": 0.10,
}


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def calculate_index(
    *,
    average_positive_delay_s: float,
    on_time_departure_ratio: float,
    track_utilization: float,
    conflict_count: int,
    blocked_train_seconds: float,
    active_train_count: int,
    window_s: int = 900,
    weights=None,
):
    weights = weights or DEFAULT_WEIGHTS

    p_delay = min(average_positive_delay_s / 600.0, 1.0)
    p_departures = 1.0 - clamp(on_time_departure_ratio)
    p_tracks = clamp((track_utilization - 0.75) / 0.25)
    p_conflicts = min(conflict_count / 5.0, 1.0)

    denominator = active_train_count * window_s
    if denominator > 0:
        p_waiting = min(blocked_train_seconds / denominator, 1.0)
    else:
        p_waiting = None

    penalties = {
        "delay": p_delay,
        "departures": p_departures,
        "track_utilization": p_tracks,
        "conflicts": p_conflicts,
        "waiting": p_waiting,
    }

    active_weights = {
        key: weights[key]
        for key, value in penalties.items()
        if value is not None
    }

    total_weight = sum(active_weights.values())

    if total_weight == 0:
        return {
            "index": None,
            "category": "Нет данных",
            "penalties": penalties,
            "normalized_weights": {},
            "contributions": {},
        }

    normalized_weights = {
        key: value / total_weight
        for key, value in active_weights.items()
    }

    weighted_penalty = sum(
        normalized_weights[key] * penalties[key]
        for key in normalized_weights
    )

    index_value = round(100 * (1.0 - weighted_penalty))
    index_value = max(0, min(100, index_value))

    if index_value >= 80:
        category = "Норма"
    elif index_value >= 50:
        category = "Внимание"
    else:
        category = "Критично"

    contributions = {
        key: round(
            100 * normalized_weights[key] * penalties[key],
            2,
        )
        for key in normalized_weights
    }

    return {
        "index": index_value,
        "category": category,
        "penalties": penalties,
        "normalized_weights": normalized_weights,
        "contributions": contributions,
    }


def compare_plan_metrics(first_metrics: dict, second_metrics: dict) -> int:
    first_key = (
        first_metrics.get("unassigned_count", 0),
        first_metrics.get("total_positive_delay_s", 0),
        first_metrics.get("changed_future_assignments", 0),
    )

    second_key = (
        second_metrics.get("unassigned_count", 0),
        second_metrics.get("total_positive_delay_s", 0),
        second_metrics.get("changed_future_assignments", 0),
    )

    if first_key < second_key:
        return -1
    if first_key > second_key:
        return 1
    return 0
