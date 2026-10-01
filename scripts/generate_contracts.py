"""Generate documented fixtures/schemas from the single source of truth in domain."""
import json
import sys
from pathlib import Path
from typing import Union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic import TypeAdapter
from app.domain.models import (
    ApplyPlanCommand, ControlCommand, Event, IncidentCommand, IncidentBatchCommand, CommandResult, Plan, StateResponse, WsEnvelope,
)
from app.main import create_app


def write(path, value):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate():
    tracks = []
    for n in range(1, 13):
        kind = "passenger" if n <= 2 else "freight" if n <= 6 else "storage" if n <= 9 else "cargo" if n <= 11 else "locomotive"
        length = 500 if n <= 2 else 900 if n <= 9 else 700 if n <= 11 else 150
        tracks.append(dict(id=f"P{n:02}", kind=kind, usable_length_m=length,
                           geometry=[[320, 60+60*n], [1080, 60+60*n]]))
    topology = dict(station_id="uzel12", name="Узел 12 — контракт одного поезда",
                    view_box=[0, 0, 1400, 900],
                    boundary_nodes={"W": [40,450], "E": [1360,450]},
                    conflict_zones={"GW": [180,450], "GE": [1220,450]},
                    routes=[
                        dict(id="R_W_P01", from_id="W", to_id="P01", conflict_zone_ids=["GW"], duration_s=120,
                             polyline=[[40,450],[180,450],[320,120],[700,120]]),
                        dict(id="R_P01_E", from_id="P01", to_id="E", conflict_zone_ids=["GE"], duration_s=120,
                             polyline=[[700,120],[1080,120],[1220,450],[1360,450]])])
    resources = [dict(id=f"L{i:02}",kind="locomotive",capabilities=["shunt"]) for i in (1,2)]
    resources += [dict(id=f"B{i:02}",kind="crew",capabilities=["shunt","formation"] if i <= 2 else ["inspection","preparation"]) for i in range(1,5)]
    resources += [dict(id=f"F{i}",kind="cargo_front",capabilities=["cargo"]) for i in (10,11)]
    operations = [dict(id="T01_01_arrival",train_id="T01",kind="arrival",duration_s=120,predecessor_ids=[]),
                  dict(id="T01_02_dwell",train_id="T01",kind="dwell",duration_s=360,predecessor_ids=["T01_01_arrival"]),
                  dict(id="T01_03_departure",train_id="T01",kind="departure",duration_s=120,predecessor_ids=["T01_02_dwell"])]
    initial = StateResponse.model_validate(dict(topology=topology, snapshot=dict(
        run_id="example-run", state_version=0, last_seq=0, sim_time_s=0, speed=1, paused=True,
        tracks=tracks, resources=resources, operations=operations,
        trains=[dict(id="T01",kind="passenger",length_m=350,priority=3,scheduled_arrival_s=0,
                     expected_arrival_s=0,scheduled_departure_s=600,status="scheduled")])) )
    write("shared/examples/state.initial.json", initial.model_dump(mode="json"))
    moving_data = initial.model_dump(mode="json")
    snap = moving_data["snapshot"]
    snap.update(state_version=2,last_seq=2,sim_time_s=60,paused=False,active_plan_id="example-plan")
    snap["trains"][0].update(status="moving", movement=dict(route_id="R_W_P01",started_at_s=0,expected_end_at_s=120))
    # Target reservation is private engine state; occupancy starts at arrival.
    snap["operations"][0].update(status="running", actual_start_s=0)
    moving = StateResponse.model_validate(moving_data)
    write("shared/examples/state.moving.json", moving.model_dump(mode="json"))
    incident = IncidentCommand(command_id="example-command",run_id="example-run",kind="close_track",target_id="P04",duration_s=600)
    write("shared/examples/incident.close_track.json", incident.model_dump(mode="json"))
    batch = IncidentBatchCommand(command_id="example-batch",run_id="example-run",incidents=[
        dict(kind="close_track",target_id="P04",duration_s=600),
        dict(kind="locomotive_unavailable",target_id="L01",duration_s=300)])
    write("shared/examples/incident.batch.json",batch.model_dump(mode="json",exclude_none=True))
    feasible = Plan(id="example-plan",run_id="example-run",based_on_version=0,strategy="passenger_first",status="feasible",
                    assignments=[dict(operation_id="T01_01_arrival",start_s=0,end_s=120,track_id="P01",route_id="R_W_P01"),
                                 dict(operation_id="T01_02_dwell",start_s=120,end_s=480,track_id="P01"),
                                 dict(operation_id="T01_03_departure",start_s=480,end_s=600,track_id="P01",route_id="R_P01_E")],
                    unassigned=[],explanations=["Учебный пример контракта. Не результат вызова планировщика."])
    write("shared/examples/plan.feasible.json",feasible.model_dump(mode="json"))
    infeasible = feasible.model_dump(mode="json")
    infeasible.update(id="example-no-plan",status="infeasible",assignments=[],unassigned=[
        dict(operation_id=o["id"],code="NO_FEASIBLE_SLOT",message="Иллюстрация формата отказа; это не расчёт данного снимка.") for o in operations])
    write("shared/examples/plan.infeasible.json",Plan.model_validate(infeasible).model_dump(mode="json"))
    messages = {
        "snapshot": initial.model_dump(mode="json"),
        "state_updated": {"snapshot": moving.snapshot.model_dump(mode="json")},
        "clock_sync": {"sim_time_s": 60, "speed": 1, "paused": False},
        "replan_started": {"job_id":"example-job","based_on_version":2},
        "replan_finished": {"job_id":"example-job","plan_ids":["example-plan"],"based_on_version":0,"stale":True},
        "replan_failed": {"job_id":"example-job","code":"PLANNER_ERROR","message":"Расчёт не выполнен."},
        "simulation_error": {"code":"DATABASE_UNAVAILABLE","message":"Сохранение недоступно, модель остановлена."},
    }
    for kind,payload in messages.items():
        envelope = WsEnvelope(run_id="example-run",ws_seq=1 if kind=="snapshot" else 2,
                              state_version=0 if kind=="snapshot" else 2,type=kind,payload=payload)
        write(f"shared/examples/ws.{kind}.json",envelope.model_dump(mode="json"))
    schema = TypeAdapter(Union[StateResponse, Plan, Event, ControlCommand, IncidentCommand, IncidentBatchCommand, CommandResult,
                              ApplyPlanCommand, WsEnvelope]).json_schema()
    write("shared/schemas/contracts.schema.json",schema)
    write("shared/schemas/openapi.json",create_app().openapi())


if __name__ == "__main__":
    generate()
