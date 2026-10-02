"""Regression tests for the frontend <-> backend contract (audit: CSRF, plan variants, incident batch).

The payloads are the ones frontend/src/api/client.ts sends (see frontend/src/api/client.test.ts, which checks
the client against the same shared/examples files), exercised through the real FastAPI app with the real
auth middleware, planner process, constraints and engine.
"""
import json
import time
from pathlib import Path

import pytest

from app.runtime.coordinator import Coordinator
from app.runtime.planning import PlannerProcess
from app.runtime.state import prepare_scenario
from auth_support import signed_in_client
from test_planning import PlanningMemory

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "shared" / "examples"


def full_station_owner(**kw):
    config = json.loads((ROOT / "shared" / "station.json").read_text(encoding="utf-8"))
    state, _, plan = prepare_scenario(config)
    repo = PlanningMemory()
    return Coordinator(repo, state, plan, **kw), repo


def example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- CSRF


def test_post_without_csrf_header_is_rejected_and_with_header_accepted():
    owner, repo = full_station_owner()
    with signed_in_client(repo, owner) as client:
        token = client.headers.pop("X-CSRF-Token")
        body = dict(command_id="speed-1", run_id=owner.state.run_id, action="speed", speed=5)
        refused = client.post("/api/simulation/control", json=body)
        assert refused.status_code == 403 and refused.json()["code"] == "CSRF_FAILED"
        wrong = client.post("/api/simulation/control", json=body, headers={"X-CSRF-Token": token + "x"})
        assert wrong.status_code == 403 and wrong.json()["code"] == "CSRF_FAILED"
        ok = client.post("/api/simulation/control", json=body, headers={"X-CSRF-Token": token})
        assert ok.status_code == 200 and ok.json()["state_version"] >= 1
        # GET requests never need the header; /api/me hands the same token back after a page reload.
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/me").json()["csrf_token"] == token


# ---------------------------------------------------------------- incident batch


def test_valid_incident_batch_from_shared_example_is_accepted():
    owner, repo = full_station_owner()
    with signed_in_client(repo, owner) as client:
        body = example("incident.batch.json") | dict(command_id="batch-1", run_id=owner.state.run_id)
        reply = client.post("/api/incidents/batch", json=body)
        assert reply.status_code == 200, reply.text
        assert reply.json()["replan_required"] is True
        tracks = {t["id"]: t for t in client.get("/api/state").json()["snapshot"]["tracks"]}
        assert tracks["P04"]["closed_until_s"] == 600


@pytest.mark.parametrize("body,field", [
    ({"batch": [{"kind": "delay_train", "target_id": "T05", "delay_s": 300}]}, "batch"),  # old frontend payload
    ({"incidents": []}, "incidents"),
    ({"incidents": [{"kind": "delay_train", "target_id": "T05", "duration_s": 300}]}, "incidents"),
    ({"incidents": [{"kind": "flood", "target_id": "P04", "duration_s": 300}]}, "incidents"),
    ({"incidents": [{"kind": "delay_train", "target_id": "T05", "delay_s": -5}]}, "incidents"),
    ({"incidents": [{"kind": "close_track", "target_id": "P04", "duration_s": "600"}]}, "incidents"),
])
def test_invalid_incident_batch_is_422(body, field):
    owner, repo = full_station_owner()
    with signed_in_client(repo, owner) as client:
        before = client.get("/api/state").json()["snapshot"]["state_version"]
        reply = client.post("/api/incidents/batch", json=dict(command_id="bad", run_id=owner.state.run_id, **body))
        assert reply.status_code == 422, reply.text
        assert reply.json()["code"] == "VALIDATION_ERROR"
        assert any(field in d["loc"] for d in reply.json()["details"])
        assert client.get("/api/state").json()["snapshot"]["state_version"] == before


def test_incident_batch_with_unknown_track_is_rejected_atomically():
    owner, repo = full_station_owner()
    with signed_in_client(repo, owner) as client:
        before = client.get("/api/state").json()["snapshot"]
        body = dict(command_id="unknown", run_id=owner.state.run_id, incidents=[
            {"kind": "close_track", "target_id": "P04", "duration_s": 600},
            {"kind": "close_track", "target_id": "NO_SUCH_TRACK", "duration_s": 600}])
        reply = client.post("/api/incidents/batch", json=body)
        assert reply.status_code == 409 and reply.json()["code"] == "INCIDENT_REJECTED"
        after = client.get("/api/state").json()["snapshot"]
        assert after["state_version"] == before["state_version"]
        assert {t["id"]: t["closed_until_s"] for t in after["tracks"]}["P04"] is None


def test_viewer_cannot_send_incidents():
    owner, repo = full_station_owner()
    with signed_in_client(repo, owner, role="viewer") as client:
        body = example("incident.batch.json") | dict(command_id="v", run_id=owner.state.run_id)
        assert client.post("/api/incidents/batch", json=body).status_code == 403


# ---------------------------------------------------------------- plan variants


def test_plan_variants_response_is_not_empty_after_incident_on_full_station():
    owner, repo = full_station_owner(planner=PlannerProcess(timeout_s=20, budget_s=2))
    with signed_in_client(repo, owner) as client:
        body = dict(command_id="batch", run_id=owner.state.run_id, incidents=[
            {"kind": "delay_train", "target_id": "T05", "delay_s": 300},
            {"kind": "close_track", "target_id": "P09", "duration_s": 900}])
        assert client.post("/api/incidents/batch", json=body).status_code == 200
        deadline = time.monotonic() + 25
        while not any(j["status"] == "completed" for j in repo.jobs.values()):
            assert time.monotonic() < deadline, repo.jobs
            time.sleep(.05)
        job = next(j for j in repo.jobs.values() if j["status"] == "completed")
        wire = client.get("/api/replans/" + job["job_id"]).json()
        assert len(wire["plan_ids"]) == 2  # passenger_first + earliest_departure
        strategies = set()
        for plan_id in wire["plan_ids"]:
            reply = client.get("/api/plans/" + plan_id).json()
            assert set(reply) == {"plan", "stale", "applicable"}
            plan = reply["plan"]
            assert plan["id"] == plan_id and plan["status"] == "feasible"
            pending = [o for o in owner.state.operations.values() if o["status"] == "pending"]
            assert len(plan["assignments"]) == len(pending) > 0
            assert {"total_positive_delay_s", "max_delay_s", "unassigned_count",
                    "changed_future_assignments"} <= set(plan["metrics"])
            assert reply["applicable"] is True and reply["stale"] is False
            strategies.add(plan["strategy"])
        assert strategies == {"passenger_first", "earliest_departure"}
        chosen = wire["plan_ids"][1]
        applied = client.post(f"/api/plans/{chosen}/apply", json=dict(
            command_id="apply", run_id=owner.state.run_id, expected_state_version=owner.state.state_version))
        assert applied.status_code == 200, applied.text
        assert client.get("/api/state").json()["snapshot"]["active_plan_id"] == chosen



# ---------------------------------------------------------------- unexpected errors


def test_unexpected_exception_is_500_api_error_without_details():
    from fastapi.testclient import TestClient

    from app.main import create_app
    from auth_support import MemorySessionStore, auth_settings, login

    owner, repo = full_station_owner()

    def broken():
        raise RuntimeError("secret internal detail")

    owner.get_state = broken
    app = create_app(auth_settings(), repo, owner, sessions=MemorySessionStore())
    with TestClient(app, raise_server_exceptions=False) as client:
        client.headers.update(login(client, "viewer"))
        reply = client.get("/api/state")
        assert reply.status_code == 500
        assert reply.json() == {"code": "INTERNAL_ERROR", "details": [],
                                "message": "Внутренняя ошибка сервера. Подробности — в журнале backend."}
        assert "secret" not in reply.text
