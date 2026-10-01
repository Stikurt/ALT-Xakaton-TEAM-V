import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.models import Assignment, ControlCommand, IncidentCommand, Plan, Snapshot, StateResponse, WsEnvelope

EXAMPLES = Path(__file__).resolve().parents[1] / "shared/examples"


def read(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("filename,model", [
    ("state.initial.json", StateResponse), ("state.moving.json", StateResponse),
    ("plan.feasible.json", Plan), ("plan.infeasible.json", Plan),
    ("incident.close_track.json", IncidentCommand),
])
def test_examples_match_contract(filename, model):
    model.model_validate(read(filename))


@pytest.mark.parametrize("kind", ["snapshot","state_updated","clock_sync","replan_started","replan_finished","replan_failed","simulation_error"])
def test_websocket_examples(kind):
    msg = WsEnvelope.model_validate(read(f"ws.{kind}.json"))
    if kind == "snapshot":
        StateResponse.model_validate(msg.payload)
    if kind == "state_updated":
        Snapshot.model_validate(msg.payload["snapshot"])


@pytest.mark.parametrize("start,end", [(0,0),(10,9),(-1,1),(1.5,3),(True,3)])
def test_invalid_intervals_rejected(start, end):
    with pytest.raises(ValidationError):
        Assignment(operation_id="o",start_s=start,end_s=end)


def test_unknown_fields_rejected():
    data = read("state.initial.json")
    data["snapshot"]["trains"][0]["browser_x"] = 42
    with pytest.raises(ValidationError):
        StateResponse.model_validate(data)


def test_unknown_references_rejected():
    data = read("state.initial.json")
    data["snapshot"]["operations"][0]["train_id"] = "T99"
    with pytest.raises(ValidationError):
        StateResponse.model_validate(data)


def test_moving_train_requires_route_and_times():
    data = read("state.moving.json")
    data["snapshot"]["trains"][0]["movement"] = None
    with pytest.raises(ValidationError):
        StateResponse.model_validate(data)


def test_engine_open_conflict_and_structured_wait_reason():
    data = read("state.initial.json")
    data["snapshot"]["operations"][0]["wait_reason"] = {
        "code":"TRACK_CLOSED", "message":"Путь закрыт"}
    data["snapshot"]["conflicts"] = [dict(id="wait:T01_01_arrival",code="TRACK_CLOSED",
        severity="error",entity_ids=["T01"],operation_ids=["T01_01_arrival"],
        start_s=0,end_s=None,message="Путь закрыт")]
    StateResponse.model_validate(data)


def test_feasible_example_references_and_durations():
    state = StateResponse.model_validate(read("state.initial.json"))
    plan = Plan.model_validate(read("plan.feasible.json"))
    ops = {o.id:o for o in state.snapshot.operations}
    routes = {r.id for r in state.topology.routes}
    tracks = {t.id for t in state.snapshot.tracks}
    assert plan.run_id == state.snapshot.run_id
    assert {a.operation_id for a in plan.assignments} == set(ops)
    for a in plan.assignments:
        assert a.end_s - a.start_s == ops[a.operation_id].duration_s
        assert a.track_id in tracks
        assert a.route_id is None or a.route_id in routes
    assert plan.assignments[-1].end_s == state.snapshot.trains[0].scheduled_departure_s


def test_partial_plan_cannot_masquerade_as_feasible():
    data = read("plan.infeasible.json")
    data["status"] = "feasible"
    with pytest.raises(ValidationError):
        Plan.model_validate(data)


@pytest.mark.parametrize("fields", [dict(action="speed"),dict(action="speed",speed=2),dict(action="pause",speed=5)])
def test_invalid_control_commands(fields):
    with pytest.raises(ValidationError):
        ControlCommand(command_id="c",run_id="r",**fields)


@pytest.mark.parametrize("fields", [dict(kind="close_track",delay_s=10),dict(kind="delay_train",duration_s=10),dict(kind="close_track",duration_s=0)])
def test_invalid_incidents(fields):
    with pytest.raises(ValidationError):
        IncidentCommand(command_id="c",run_id="r",target_id="P04",**fields)
