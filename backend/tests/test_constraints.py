from app.constraints import RULES, intervals_overlap, validate_plan


def make_context():
    snapshot = {
        "run_id": "run-1",
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
            }
        ],
        "trains": [
            {
                "id": "T01",
                "kind": "passenger",
                "length_m": 350,
                "priority": 3,
                "expected_arrival_s": 0,
                "scheduled_arrival_s": 0,
                "scheduled_departure_s": 600,
                "status": "waiting_entry",
                "track_id": None,
                "movement": None,
            }
        ],
        "resources": [],
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
    topology = {
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
        ],
    }
    return {
        "snapshot": snapshot,
        "topology": topology,
        "reservations": {},
        "busy_zones": {},
        "running_assignments": {},
    }


def test_half_open_intervals():
    assert intervals_overlap(0, 10, 10, 20) is False
    assert intervals_overlap(0, 10, 9, 20) is True


def test_simulation_rules_can_start_arrival():
    context = make_context()
    operation = context["snapshot"]["operations"][0]
    assignment = {
        "operation_id": operation["id"],
        "start_s": 0,
        "end_s": 120,
        "track_id": "P01",
        "route_id": "R_W_P01",
        "resource_ids": [],
    }

    assert RULES.can_start(context, operation, assignment) == []


def test_simulation_rules_reject_closed_target():
    context = make_context()
    context["snapshot"]["tracks"][0]["availability"] = "closed"
    context["snapshot"]["tracks"][0]["closed_until_s"] = 600

    operation = context["snapshot"]["operations"][0]
    assignment = {
        "operation_id": operation["id"],
        "start_s": 0,
        "end_s": 120,
        "track_id": "P01",
        "route_id": "R_W_P01",
        "resource_ids": [],
    }

    codes = [x["code"] for x in RULES.can_start(context, operation, assignment)]
    assert "TRACK_CLOSED" in codes


def test_validate_complete_one_train_plan():
    context = make_context()
    plan = {
        "id": "plan-1",
        "run_id": "run-1",
        "based_on_version": 0,
        "status": "feasible",
        "unassigned": [],
        "assignments": [
            {
                "operation_id": "T01_01_arrival",
                "start_s": 0,
                "end_s": 120,
                "track_id": "P01",
                "route_id": "R_W_P01",
                "resource_ids": [],
            },
            {
                "operation_id": "T01_02_dwell",
                "start_s": 120,
                "end_s": 480,
                "track_id": "P01",
                "route_id": None,
                "resource_ids": [],
            },
            {
                "operation_id": "T01_03_departure",
                "start_s": 480,
                "end_s": 600,
                "track_id": "P01",
                "route_id": "R_P01_E",
                "resource_ids": [],
            },
        ],
    }

    assert validate_plan(context, plan) == []


def test_validate_rejects_early_departure():
    context = make_context()
    plan = {
        "id": "plan-early",
        "run_id": "run-1",
        "based_on_version": 0,
        "status": "feasible",
        "unassigned": [],
        "assignments": [
            {
                "operation_id": "T01_01_arrival",
                "start_s": 0,
                "end_s": 120,
                "track_id": "P01",
                "route_id": "R_W_P01",
                "resource_ids": [],
            },
            {
                "operation_id": "T01_02_dwell",
                "start_s": 120,
                "end_s": 480,
                "track_id": "P01",
                "route_id": None,
                "resource_ids": [],
            },
            {
                "operation_id": "T01_03_departure",
                "start_s": 470,
                "end_s": 590,
                "track_id": "P01",
                "route_id": "R_P01_E",
                "resource_ids": [],
            },
        ],
    }

    codes = [x["code"] for x in validate_plan(context, plan)]
    assert "NO_FEASIBLE_SLOT" in codes
