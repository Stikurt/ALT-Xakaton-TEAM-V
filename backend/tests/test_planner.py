from app.planner import plan


def make_snapshot():
    return {
        "run_id": "run-test",
        "state_version": 0,
        "sim_time_s": 0,
        "tracks": [
            {
                "id": "P01",
                "kind": "passenger",
                "usable_length_m": 500,
                "availability": "open",
                "closed_until_s": None,
                "occupant_train_id": None,
            },
            {
                "id": "P02",
                "kind": "passenger",
                "usable_length_m": 500,
                "availability": "open",
                "closed_until_s": None,
                "occupant_train_id": None,
            },
        ],
        "resources": [],
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
                "track_id": None,
                "movement": None,
            }
        ],
        "operations": [
            {
                "id": "T01_01_arrival",
                "train_id": "T01",
                "kind": "arrival",
                "duration_s": 120,
                "predecessor_ids": [],
                "status": "pending",
                "actual_start_s": None,
                "actual_end_s": None,
            },
            {
                "id": "T01_02_dwell",
                "train_id": "T01",
                "kind": "dwell",
                "duration_s": 360,
                "predecessor_ids": ["T01_01_arrival"],
                "status": "pending",
                "actual_start_s": None,
                "actual_end_s": None,
            },
            {
                "id": "T01_03_departure",
                "train_id": "T01",
                "kind": "departure",
                "duration_s": 120,
                "predecessor_ids": ["T01_02_dwell"],
                "status": "pending",
                "actual_start_s": None,
                "actual_end_s": None,
            },
        ],
    }


def make_config():
    return {
        "horizon_s": 7200,
        "routes": [
            {
                "id": "R_W_P01",
                "from_id": "W",
                "to_id": "P01",
                "conflict_zone_ids": ["GW"],
                "duration_s": 120,
            },
            {
                "id": "R_P01_E",
                "from_id": "P01",
                "to_id": "E",
                "conflict_zone_ids": ["GE"],
                "duration_s": 120,
            },
            {
                "id": "R_W_P02",
                "from_id": "W",
                "to_id": "P02",
                "conflict_zone_ids": ["GW"],
                "duration_s": 120,
            },
            {
                "id": "R_P02_E",
                "from_id": "P02",
                "to_id": "E",
                "conflict_zone_ids": ["GE"],
                "duration_s": 120,
            },
        ],
    }


def test_plan_is_deterministic():
    first = plan(make_snapshot(), make_config(), "passenger_first", 1.0)
    second = plan(make_snapshot(), make_config(), "passenger_first", 1.0)

    assert first["assignments"] == second["assignments"]


def test_plan_is_feasible():
    result = plan(make_snapshot(), make_config(), "passenger_first", 1.0)
    assert result["status"] == "feasible"
    assert result["violations"] == []
    assert result["unassigned"] == []


def test_plan_creates_routes():
    result = plan(make_snapshot(), make_config(), "passenger_first", 1.0)

    arrival = next(a for a in result["assignments"] if a["operation_id"] == "T01_01_arrival")
    departure = next(a for a in result["assignments"] if a["operation_id"] == "T01_03_departure")

    assert arrival["route_id"] == "R_W_P01"
    assert departure["route_id"] == "R_P01_E"


def test_departure_reaches_e_not_early():
    result = plan(make_snapshot(), make_config(), "passenger_first", 1.0)
    departure = next(a for a in result["assignments"] if a["operation_id"] == "T01_03_departure")

    assert departure["end_s"] >= 600


def test_second_strategy():
    result = plan(make_snapshot(), make_config(), "earliest_departure", 1.0)
    assert result["strategy"] == "earliest_departure"
    assert result["status"] == "feasible"
