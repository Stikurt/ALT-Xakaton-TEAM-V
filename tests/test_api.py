from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.domain.models import StateResponse
from app.main import create_app
from app.settings import Settings
from app.storage.repository import NotInitialized


class FakeRepository:
    """Only for isolated API tests; never an application persistence fallback."""
    def __init__(self, error=None):
        self.error = error

    def check_ready(self):
        if self.error:
            raise self.error

    def current_state(self):
        self.check_ready()
        return StateResponse.model_validate_json((Path(__file__).resolve().parents[1] /
            "shared/examples/state.initial.json").read_text(encoding="utf-8"))


def client(error=None):
    return TestClient(create_app(Settings(_env_file=None), FakeRepository(error)))


def test_health_state_and_openapi():
    with client() as c:
        assert c.get("/health").json()["database"] == "ready"
        state = c.get("/api/state")
        assert state.status_code == 200
        assert state.json()["snapshot"]["paused"] is True
        assert c.get("/docs").status_code == 200
        assert set(c.get("/openapi.json").json()["paths"]) == {"/health", "/api/state"}


@pytest.mark.parametrize("exc,code", [(NotInitialized(),"NOT_INITIALIZED"),
    (psycopg.OperationalError("secret_database_password"),"DATABASE_UNAVAILABLE")])
@pytest.mark.parametrize("path", ["/health","/api/state"])
def test_database_failure_is_visible_without_leaking_secrets(exc, code, path):
    with client(exc) as c:
        r = c.get(path)
        assert r.status_code == 503
        assert r.json()["code"] == code
        assert "secret_database_password" not in r.text
        assert set(r.json()) == {"code","message","details"}


def test_missing_route_has_common_error_format():
    with client() as c:
        r = c.post("/api/simulation/control",json={})
        assert r.status_code == 404
        assert set(r.json()) == {"code","message","details"}


def test_cors_is_explicit():
    with client() as c:
        assert c.get("/health",headers={"Origin":"http://localhost:5173"}).headers["access-control-allow-origin"] == "http://localhost:5173"
        assert "access-control-allow-origin" not in c.get("/health",headers={"Origin":"https://unknown.example"}).headers


def test_project_paths_point_inside_checkout():
    from app.settings import PROJECT_ROOT
    assert (PROJECT_ROOT / "shared/examples/state.initial.json").is_file()
    assert (PROJECT_ROOT / "backend/app/settings.py").is_file()
