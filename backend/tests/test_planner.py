from app.planner import plan


def make_snapshot():
    return {
        "run_id": "run-test",
        "state_version": 1,
        "sim_time_s": 0,
        "active_plan_id": None,
        "tracks": [
            {
                "id": "P01",
                "usable_length_m": 500,
                "availability": "open",
                "closed_until_s": 0,
                "occupant_train_id": None,
            },
            {
                "id": "P02",
                "usable_length_m": 500,
                "availability": "open",
                "closed_until_s": 0,
                "occupant_train_id": None,
            },
        ],
        "resources": [],
        "routes": [],
        "trains": [
            {
                "id": "T01",
                "kind": "passenger",
                "length_m": 350,
                "priority": 3,
                "scheduled_arrival_s": 0,
                "expected_arrival_s": 0,
                "scheduled_departure_s": 600,
                "status": "scheduled",
            }
        ],
        "operations": [
            {
                "id": "T01_STOP",
                "train_id": "T01",
                "kind": "dwell",
                "duration_s": 360,
                "predecessor_ids": [],
                "status": "pending",
            },
            {
                "id": "T01_DEPARTURE",
                "train_id": "T01",
                "kind": "departure",
                "duration_s": 120,
                "predecessor_ids": ["T01_STOP"],
                "status": "pending",
            },
        ],
    }


def test_plan_is_deterministic():
    snapshot = make_snapshot()
    config = {"planning_horizon_s": 7200}

    first = plan(
        snapshot,
        config,
        "passenger_first",
        1.0,
    )

    second = plan(
        snapshot,
        config,
        "passenger_first",
        1.0,
    )

    assert first["assignments"] == second["assignments"]


def test_no_early_departure():
    snapshot = make_snapshot()

    result = plan(
        snapshot,
        {"planning_horizon_s": 7200},
        "passenger_first",
        1.0,
    )

    departure = next(
        assignment
        for assignment in result["assignments"]
        if assignment["operation_id"] == "T01_DEPARTURE"
    )

    assert departure["start_s"] >= 600


def test_plan_status():
    snapshot = make_snapshot()

    result = plan(
        snapshot,
        {"planning_horizon_s": 7200},
        "passenger_first",
        1.0,
    )

    assert result["status"] in (
        "feasible",
        "partial",
        "infeasible",
    )


def test_plan_contains_metrics():
    snapshot = make_snapshot()

    result = plan(
        snapshot,
        {"planning_horizon_s": 7200},
        "passenger_first",
        1.0,
    )

    assert "metrics" in result
    assert "total_positive_delay_s" in result["metrics"]
    assert "max_delay_s" in result["metrics"]
    assert "unassigned_count" in result["metrics"]


def test_earliest_departure_strategy():
    snapshot = make_snapshot()

    result = plan(
        snapshot,
        {"planning_horizon_s": 7200},
        "earliest_departure",
        1.0,
    )

    assert result["strategy"] == "earliest_departure"
    assert len(result["assignments"]) > 0
