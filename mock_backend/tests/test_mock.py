"""Проверки мок-сервера: план допустим, сбои применяются по правилам ТЗ, права и история работают."""
from fastapi.testclient import TestClient

from app.planner import plan, validate_plan
from app.sim import Station


def started_station():
    st = Station()
    st.apply_plan(plan(st.snapshot(), "earliest_departure"))
    st.paused = False
    st.process()
    return st


def test_initial_plan_feasible_and_deterministic():
    a = plan(Station().snapshot(), "earliest_departure")
    b = plan(Station().snapshot(), "earliest_departure")
    assert a["status"] == "feasible" and a["violations"] == []
    assert [x["start_s"] for x in a["assignments"]] == [x["start_s"] for x in b["assignments"]]


def test_full_run_departs_all_trains():
    st = started_station()
    for _ in range(9000):
        st.step()
    assert all(t["status"] == "departed" for t in st.trains)


def test_close_track_replan_is_valid():
    st = started_station()
    for _ in range(700):
        st.step()
    assert st.incident("close_track", "P05", 600)[0] == 200
    snap = st.snapshot()
    for strategy in ("passenger_first", "earliest_departure"):
        p = plan(snap, strategy)
        assert p["status"] == "feasible"
        assert validate_plan(snap, p) == []


def test_incident_names_from_backend_contract():
    st = started_station()
    for _ in range(200):
        st.step()
    assert st.incident("delay_train", "T01", 300)[0] == 409  # уже прибыл
    assert st.incident("delay_train", "T15", 300)[0] == 200
    assert st.incident("locomotive_unavailable", "L02", 600)[0] == 200


def test_api_roles_and_history():
    from app.main import app
    with TestClient(app) as c:
        assert c.get("/api/state").status_code == 401
        assert c.post("/api/login", json={"username": "viewer", "password": "viewer"}).status_code == 200
        run = c.get("/api/state").json()["snapshot"]["run_id"]
        r = c.post("/api/simulation/control", json={"command_id": "v1", "run_id": run, "action": "start"})
        assert r.status_code == 403
        assert c.post("/api/login", json={"username": "dispatcher", "password": "dispatcher"}).status_code == 200
        ok = c.post("/api/simulation/control", json={"command_id": "d1", "run_id": run, "action": "start"})
        again = c.post("/api/simulation/control", json={"command_id": "d1", "run_id": run, "action": "start"})
        assert ok.status_code == 200 and again.json() == ok.json()  # идемпотентность command_id
        assert c.get(f"/api/history?run_id={run}&at_s=0").status_code == 200
        csv = c.get(f"/api/export.csv?run_id={run}")
        assert csv.status_code == 200 and "train_id" in csv.text
