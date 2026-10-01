"""Backend start against a database that is not reachable: bounded retries, then a clear 503."""
import logging
import socket

from fastapi.testclient import TestClient

import app.main as main
from auth_support import auth_settings


def closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_unreachable_database_is_retried_then_reported(monkeypatch, caplog):
    monkeypatch.setattr(main, "STARTUP_ATTEMPTS", 3)
    monkeypatch.setattr(main, "STARTUP_RETRY_S", 0)
    url = f"postgresql://nobody:nothing@127.0.0.1:{closed_port()}/none"
    settings = auth_settings(database_url=url, db_timeout_s=0.3)
    caplog.set_level(logging.WARNING, logger="app.main")
    with TestClient(main.create_app(settings)) as client:
        assert client.app.state.runtime is None
        assert client.app.state.runtime_error == main.DB_UNAVAILABLE
        assert client.get("/health").status_code == 503
    retries = [r for r in caplog.records if "database_not_ready" in r.getMessage()]
    failures = [r for r in caplog.records if "runtime_unavailable" in r.getMessage()]
    assert len(retries) == 2 and len(failures) == 1
    assert "nothing" not in caplog.text  # no credentials in logs
