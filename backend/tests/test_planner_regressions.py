"""Regression tests for the planner/constraints/engine fixes of the integration audit.

* dynamic planning horizon (no fixed 7200/10800 s limit)
* no station-specific identifiers in production code (other IDs: PLATFORM_A, LINE_X, BLOCK_Z ...)
* planner input validation instead of exceptions, partial vs infeasible status
* simulation completes and releases every resource
"""
import copy
import json
import re
from pathlib import Path

import pytest

from app.constraints import RULES, validate_plan
from app.planner import plan
from app.simulation import SimulationError, advance_to, apply_command, apply_plan, create_initial_state, snapshot
from app.simulation.engine import rules_context
from app.topology import planning_horizon

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
STRATEGIES = ("passenger_first", "earliest_departure")


def station():
    return json.loads((ROOT / "shared" / "station.json").read_text(encoding="utf-8"))


def one_train():
    return json.loads((ROOT / "shared" / "scenarios" / "one_train.json").read_text(encoding="utf-8"))


RENAME = {
    "P01": "PLATFORM_A", "P02": "PLATFORM_B", "P03": "MAIN_1", "P04": "MAIN_2", "P05": "MAIN_3",
    "P06": "MAIN_4", "P07": "YARD_1", "P08": "YARD_2", "P09": "YARD_3", "P10": "DOCK_A",
    "P11": "DOCK_B", "P12": "DEPOT", "L01": "LINE_X", "L02": "LINE_Y", "B01": "BLOCK_Z",
    "B02": "BLOCK_Q", "B03": "CREW_C", "B04": "CREW_D", "F10": "FRONT_A", "F11": "FRONT_B",
    "W": "ENTRY_WEST", "E": "EXIT_EAST", "GW": "THROAT_1", "GE": "THROAT_2",
}


def renamed(value):
    """Same station, every infrastructure id replaced (also inside route ids like R_W_P01)."""
    if isinstance(value, dict):
        return {renamed(k): renamed(v) for k, v in value.items()}
    if isinstance(value, list):
        return [renamed(v) for v in value]
    if isinstance(value, str):
        return "_".join(RENAME.get(token, token) for token in value.split("_"))
    return value


def shifted(config, offset):
    """The whole schedule moved later by ``offset`` seconds."""
    c = copy.deepcopy(config)
    for t in c["trains"]:
        for key in ("scheduled_arrival_s", "expected_arrival_s", "scheduled_departure_s"):
            if t.get(key) is not None:
                t[key] += offset
    return c


def run_to_completion(config, strategy="earliest_departure"):
    state = create_initial_state(config, run_id="regression")
    candidate = plan(snapshot(state), config, strategy, 4.0)
    assert candidate["status"] == "feasible", (candidate["unassigned"], candidate["violations"][:3])
    state = apply_plan(state, candidate, rules=RULES).state
    state = apply_command(state, dict(command_id="start", run_id=state.run_id, action="start"), rules=RULES).state
    events = []
    for _ in range(200):
        transition = advance_to(state, state.sim_time_s + 600, rules=RULES)
        state = transition.state
        events.extend(transition.events)
        if state.paused:
            break
    return candidate, state, events


# ---------------------------------------------------------------- horizon


def test_horizon_is_computed_from_the_schedule():
    full, short = station(), one_train()
    assert "horizon_s" not in full  # no hard-coded window in the station data any more
    h_full = planning_horizon(snapshot(create_initial_state(full)), full)
    h_short = planning_horizon(snapshot(create_initial_state(short)), short)
    last = max(t["scheduled_departure_s"] for t in full["trains"])
    work = sum(o["duration_s"] for o in full["operations"])
    assert h_full >= last + work  # serialized worst case fits
    assert h_short < h_full and h_short <= 3600  # a short scenario does not get a huge window
    # More trains or a later schedule -> a later window.
    assert planning_horizon(snapshot(create_initial_state(shifted(full, 20000))), shifted(full, 20000)) == h_full + 20000


@pytest.mark.parametrize("offset", [6900, 10300, 10800, 20000, 50000])
def test_schedule_after_old_fixed_horizon_is_planned(offset):
    # Old code: one_train re-planned at now=6900 was infeasible; anything past 10800 broke.
    config = shifted(one_train(), offset)
    state = create_initial_state(config)
    snap = snapshot(state)
    snap["sim_time_s"] = offset
    candidate = plan(snap, config, "passenger_first", 2.0)
    assert candidate["status"] == "feasible" and not candidate["violations"]
    assert candidate["horizon_s"] > max(a["end_s"] for a in candidate["assignments"])


def test_full_station_shifted_past_old_horizon_runs_to_completion():
    config = shifted(station(), 9000)  # last scheduled departure 15720 s > 10800 s
    candidate, state, events = run_to_completion(config)
    assert len(candidate["assignments"]) == 80
    assert all(t["status"] == "departed" for t in state.trains.values())
    assert events[-1]["type"] == "simulation_completed"


@pytest.mark.parametrize("now", [100, 10700, 10900, 12000, 40000])
def test_track_conflict_is_detected_at_any_model_time(now):
    # Old validate_plan treated horizon_s=10800 as absolute time and missed this after 10800 s.
    config = station()
    snap = snapshot(create_initial_state(config))
    snap["sim_time_s"] = now
    for train in snap["trains"][:2]:
        train.update(track_id="P01", status="on_track")
    for op in snap["operations"]:
        if op["train_id"] in ("T01", "T02") and op["kind"] == "arrival":
            op["status"] = "completed"
    context = dict(snapshot=snap, topology=config, reservations={}, busy_zones={}, running_assignments={})
    codes = {v["code"] for v in validate_plan(context, {"assignments": []})}
    assert "TRACK_OCCUPIED" in codes


# ---------------------------------------------------------------- infrastructure ids


def production_sources():
    return [p for p in APP.rglob("*.py") if "__pycache__" not in p.parts]


def test_production_code_has_no_station_specific_ids():
    config = station()
    ids = {t["id"] for t in config["tracks"]} | {r["id"] for r in config["resources"]}
    ids |= {config["boundary"]["entry"], config["boundary"]["exit"]}
    ids |= {z for r in config["routes"] for z in r["conflict_zone_ids"]}
    literal = re.compile(r"""(['"])(%s)\1""" % "|".join(sorted(map(re.escape, ids), key=len, reverse=True)))
    naming = re.compile(r"""f['"][A-Z]\{[^}]*\[1:\]""")  # the old f"F{track_id[1:]}" convention
    found = []
    for path in production_sources():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if literal.search(line) or naming.search(line):
                found.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not found, "\n".join(found)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_planner_works_with_other_infrastructure_ids(strategy):
    config = renamed(station())
    assert {"PLATFORM_A", "MAIN_1", "DOCK_A"} <= {t["id"] for t in config["tracks"]}
    assert {"LINE_X", "BLOCK_Z", "FRONT_A"} <= {r["id"] for r in config["resources"]}
    state = create_initial_state(config)
    candidate = plan(snapshot(state), config, strategy, 4.0)
    assert candidate["status"] == "feasible", (candidate["unassigned"], candidate["violations"][:3])
    assert len(candidate["assignments"]) == 80
    used_tracks = {a["track_id"] for a in candidate["assignments"]}
    used_resources = {r for a in candidate["assignments"] for r in a["resource_ids"]}
    assert used_tracks <= set(RENAME.values()) and used_resources <= set(RENAME.values())
    assert {"LINE_X", "BLOCK_Z", "FRONT_A"} <= used_resources
    arrivals = [a for a in candidate["assignments"] if a["operation_id"].endswith("_arrival")]
    assert all(a["route_id"].startswith("R_ENTRY_WEST_") for a in arrivals)


def test_renamed_station_runs_to_completion_without_conflicts():
    config = renamed(station())
    config.pop("boundary")  # entry/exit inferred from the routes
    candidate, state, events = run_to_completion(config)
    assert all(t["status"] == "departed" for t in state.trains.values())
    assert not state.conflicts and not state.running and not state.reservations and not state.zones
    assert all(r["active_operation_id"] is None for r in state.resources.values())
    assert all(t["occupant_train_id"] is None for t in state.tracks.values())
    assert events[-1]["type"] == "simulation_completed"


def test_cargo_front_comes_from_track_id_field():
    config = renamed(station())
    for r in config["resources"]:
        if r["kind"] == "cargo_front":
            r["track_id"] = {"FRONT_A": "DOCK_B", "FRONT_B": "DOCK_A"}[r["id"]]  # cross-wired on purpose
    state = create_initial_state(config)
    candidate = plan(snapshot(state), config, "earliest_departure", 4.0)
    assert candidate["status"] == "feasible"
    for a in candidate["assignments"]:
        if re.search(r"_\d\d_cargo$", a["operation_id"]):
            assert a["resource_ids"] == [{"DOCK_B": "FRONT_A", "DOCK_A": "FRONT_B"}[a["track_id"]]]


# ---------------------------------------------------------------- 15 trains, scheduler + constraints


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_full_station_plan_has_no_overlaps(strategy):
    config = station()
    state = create_initial_state(config)
    candidate = plan(snapshot(state), config, strategy, 4.0)
    assert candidate["status"] == "feasible" and len(candidate["assignments"]) == 80
    assert validate_plan(rules_context(state), candidate) == []
    by_resource = {}
    for a in candidate["assignments"]:
        for rid in a["resource_ids"]:
            by_resource.setdefault(rid, []).append((a["start_s"], a["end_s"]))
    for spans in by_resource.values():
        spans.sort()
        assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_full_station_run_departs_every_train_and_completes():
    candidate, state, events = run_to_completion(station(), "passenger_first")
    departures = [o["actual_end_s"] for o in state.operations.values() if o["kind"] == "departure"]
    assert len(departures) == 15 and all(t["status"] == "departed" for t in state.trains.values())
    done = events[-1]
    assert done["type"] == "simulation_completed" and done["payload"]["departed"] == 15
    assert state.paused and state.sim_time_s == max(departures) == done["payload"]["last_departure_s"]
    assert not any(e["type"] == "operation_waiting" for e in events)
    # A finished run cannot be started again without a reset.
    with pytest.raises(SimulationError) as error:
        apply_command(state, dict(command_id="again", run_id=state.run_id, action="start"), rules=RULES)
    assert error.value.code == "SIMULATION_FINISHED"


def test_closed_track_and_unavailable_locomotive_are_planned_around():
    config = station()
    state = create_initial_state(config)
    state = apply_command(state, dict(command_id="c", run_id=state.run_id, action="incidents", incidents=[
        dict(kind="close_track", target_id="P10", duration_s=3000),
        dict(kind="locomotive_unavailable", target_id="L01", duration_s=2000)]), rules=RULES).state
    candidate = plan(snapshot(state), config, "earliest_departure", 4.0)
    assert candidate["status"] == "feasible", candidate["unassigned"]
    for a in candidate["assignments"]:
        if a["track_id"] == "P10" and a["route_id"]:
            assert a["start_s"] >= 3000
        if "L01" in a["resource_ids"]:
            assert a["start_s"] >= 2000


# ---------------------------------------------------------------- input validation, statuses


def mutate(fn, config=None):
    config = copy.deepcopy(config or one_train())
    snap = snapshot(create_initial_state(config))
    fn(snap)
    return plan(snap, config, "passenger_first", 2.0)


@pytest.mark.parametrize("name,fn", [
    ("duration None", lambda s: s["operations"][0].update(duration_s=None)),
    ("length None", lambda s: s["trains"][0].update(length_m=None)),
    ("kind missing", lambda s: s["trains"][0].pop("kind")),
    ("departure before arrival", lambda s: s["trains"][0].update(scheduled_arrival_s=500, expected_arrival_s=500,
                                                              scheduled_departure_s=100)),
    ("cycle", lambda s: s["operations"][0].update(predecessor_ids=[s["operations"][-1]["id"]])),
    ("duplicate train", lambda s: s["trains"].append(copy.deepcopy(s["trains"][0]))),
    ("no tracks list", lambda s: s.pop("tracks")),
])
def test_bad_planner_input_is_reported_not_raised(name, fn):
    candidate = mutate(fn)
    assert candidate["status"] == "infeasible", name
    assert candidate["violations"] and all(v["code"] == "INVALID_INPUT" for v in candidate["violations"])
    assert candidate["assignments"] == []


@pytest.mark.parametrize("value", [None, {}, []])
def test_missing_snapshot_is_infeasible(value):
    candidate = plan(value, {}, "passenger_first", 1.0)
    assert candidate["status"] == "infeasible" and candidate["violations"][0]["code"] == "INVALID_INPUT"


def test_unknown_strategy_is_reported():
    candidate = plan(snapshot(create_initial_state(one_train())), one_train(), "random", 1.0)
    assert candidate["status"] == "infeasible" and "random" in candidate["violations"][0]["message"]


def test_train_that_cannot_fit_makes_plan_partial_not_infeasible():
    config = station()
    config["trains"][0]["length_m"] = 5000  # longer than every track
    candidate = plan(snapshot(create_initial_state(config)), config, "earliest_departure", 4.0)
    assert candidate["status"] == "partial"
    assert [u["train_id"] for u in candidate["unassigned"]] == ["T01"]
    assert candidate["violations"] == []
    assert len(candidate["assignments"]) == 80 - 3


def test_validate_plan_reports_malformed_assignment_instead_of_crashing():
    config = one_train()
    state = create_initial_state(config)
    candidate = plan(snapshot(state), config, "passenger_first", 2.0)
    broken = copy.deepcopy(candidate)
    broken["assignments"][0]["start_s"] = None
    codes = [v["message"] for v in validate_plan(rules_context(state), broken)]
    assert "Некорректный интервал" in codes


@pytest.mark.parametrize("drop", ["trains", "routes"])
def test_engine_rejects_incomplete_config_with_simulation_error(drop):
    config = one_train()
    config.pop(drop)
    with pytest.raises(SimulationError) as error:
        create_initial_state(config)
    assert error.value.code == "INVALID_CONFIG"


def test_engine_rejects_missing_predecessor_list_and_bad_plan_shape():
    config = one_train()
    for op in config["operations"]:
        op.pop("predecessor_ids")
    with pytest.raises(SimulationError) as error:
        create_initial_state(config)
    assert error.value.code == "INVALID_CONFIG"
    state = create_initial_state(one_train())
    bad = dict(id="p", run_id=state.run_id, based_on_version=state.state_version, status="feasible",
               unassigned=[], assignments=[{"x": 1}])
    with pytest.raises(SimulationError) as error:
        apply_plan(state, bad, rules=RULES)
    assert error.value.code == "INVALID_PLAN"
