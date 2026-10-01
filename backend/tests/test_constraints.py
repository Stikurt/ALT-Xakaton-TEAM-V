from app.constraints import (
    intervals_overlap,
    can_start,
    validate_plan,
)


def test_half_open_intervals():
    assert intervals_overlap(0, 10, 10, 20) is False
    assert intervals_overlap(0, 10, 9, 20) is True


def make_snapshot():
    return {
        "run_id": "run-1",
        "state_version": 1,
        "sim_time_s": 100,
        "tracks": [
            {
                "id": "P01",
                "usable_length_m": 500,
                "availability": "open",
                "closed_until_s": 0,
                "occupant_train_id": None,
            },
            {
                "id": "P03",
                "usable_length_m": 900,
                "availability": "open",
                "closed_until_s": 0,
                "occupant_train_id": None,
            },
        ],
        "trains": [
            {
                "id": "T01",
                "kind": "passenger",
                "length_m": 350,
                "priority": 3,
                "scheduled_arrival_s": 0,
                "expected_arrival_s": 0,
                "scheduled_departure_s": 600,
            }
        ],
        "resources": [
            {
                "id": "B03",
                "kind": "inspection_crew",
                "availability": "available",
                "unavailable_until_s": 0,
                "active_operation_id": None,
            }
        ],
        "operations": [
            {
                "id": "T01_DWELL",
                "train_id": "T01",
                "kind": "dwell",
                "duration_s": 360,
                "predecessor_ids": [],
                "status": "pending",
            }
        ],
        "routes": [],
    }


def test_assignment_can_start():
    snapshot = make_snapshot()

    assignment = {
        "operation_id": "T01_DWELL",
        "start_s": 100,
        "end_s": 460,
        "track_id": "P01",
        "route_id": None,
        "resource_ids": [],
    }

    result = can_start(snapshot, assignment)

    assert result["allowed"] is True
    assert result["reasons"] == []


def test_track_too_short():
    snapshot = make_snapshot()
    snapshot["tracks"][0]["usable_length_m"] = 300

    assignment = {
        "operation_id": "T01_DWELL",
        "start_s": 100,
        "end_s": 460,
        "track_id": "P01",
        "route_id": None,
        "resource_ids": [],
    }

    result = can_start(snapshot, assignment)

    assert result["allowed"] is False

    codes = [
        reason["code"]
        for reason in result["reasons"]
    ]

    assert "TRACK_TOO_SHORT" in codes


def test_closed_track():
    snapshot = make_snapshot()

    snapshot["tracks"][0]["availability"] = "closed"
    snapshot["tracks"][0]["closed_until_s"] = 1000

    assignment = {
        "operation_id": "T01_DWELL",
        "start_s": 100,
        "end_s": 460,
        "track_id": "P01",
        "route_id": None,
        "resource_ids": [],
    }

    result = can_start(snapshot, assignment)

    assert result["allowed"] is False

    codes = [
        reason["code"]
        for reason in result["reasons"]
    ]

    assert "TRACK_CLOSED" in codes


def test_occupied_track():
    snapshot = make_snapshot()
    snapshot["tracks"][0]["occupant_train_id"] = "T99"

    assignment = {
        "operation_id": "T01_DWELL",
        "start_s": 100,
        "end_s": 460,
        "track_id": "P01",
        "route_id": None,
        "resource_ids": [],
    }

    result = can_start(snapshot, assignment)

    assert result["allowed"] is False

    codes = [
        reason["code"]
        for reason in result["reasons"]
    ]

    assert "TRACK_OCCUPIED" in codes


def test_double_track_assignment():
    snapshot = make_snapshot()

    snapshot["operations"].append({
        "id": "T01_OTHER",
        "train_id": "T01",
        "kind": "test",
        "duration_s": 300,
        "predecessor_ids": [],
        "status": "pending",
    })

    plan = {
        "assignments": [
            {
                "operation_id": "T01_DWELL",
                "start_s": 100,
                "end_s": 400,
                "track_id": "P01",
                "route_id": None,
                "resource_ids": [],
            },
            {
                "operation_id": "T01_OTHER",
                "start_s": 200,
                "end_s": 500,
                "track_id": "P01",
                "route_id": None,
                "resource_ids": [],
            },
        ]
    }

    violations = validate_plan(snapshot, plan)

    codes = [
        violation["code"]
        for violation in violations
    ]

    assert "TRACK_OCCUPIED" in codes
