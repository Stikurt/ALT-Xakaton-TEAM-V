"""Test-only helpers. The in-memory store is a double; the application always uses PostgreSQL."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from functools import cache

from app.auth.passwords import hash_password
from app.auth.store import SessionRecord
from app.settings import Settings

# Random-looking per-test values, generated here and never used outside the test suite.
SECRET = "test-session-secret-" + "x" * 40
PASSWORDS = {"viewer": "viewer-pass-1234", "dispatcher": "dispatcher-pass-1234", "admin": "admin-pass-1234"}
ORIGIN = "http://localhost:5173"


@cache
def password_hashes() -> dict[str, str]:
    return {user: hash_password(pw) for user, pw in PASSWORDS.items()}


def auth_settings(**overrides) -> Settings:
    hashes = password_hashes()
    values = dict(_env_file=None, session_secret=SECRET, session_cookie_secure=False,
                  admin_password_hash=hashes["admin"], dispatcher_password_hash=hashes["dispatcher"],
                  viewer_password_hash=hashes["viewer"])
    values.update(overrides)
    return Settings(**values)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class MemorySessionStore:
    """Same contract as PostgresSessionStore, with an injectable clock."""
    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.rows: dict[bytes, dict] = {}
        self.fail = False

    def _check(self):
        if self.fail:
            raise RuntimeError("Injected session store failure")

    def create(self, *, session_id, token_hash, username, role, credential_tag, ttl_s):
        self._check()
        now = self.clock()
        record = SessionRecord(session_id, username, role, credential_tag, now, now + timedelta(seconds=ttl_s))
        self.rows[token_hash] = {"record": record, "revoked_at": None}
        return record

    def get_active(self, token_hash):
        self._check()
        row = self.rows.get(token_hash)
        if row is None or row["revoked_at"] is not None or row["record"].expires_at <= self.clock():
            return None
        return row["record"]

    def revoke(self, token_hash):
        self._check()
        row = self.rows.get(token_hash)
        if row is None or row["revoked_at"] is not None:
            return None
        row["revoked_at"] = self.clock()
        return row["record"].id

    def revoke_user(self, username=None):
        self._check()
        count = 0
        for row in self.rows.values():
            if row["revoked_at"] is None and (username is None or row["record"].username == username):
                row["revoked_at"] = self.clock()
                count += 1
        return count

    def expire_all(self):
        for row in self.rows.values():
            row["record"] = replace(row["record"], expires_at=self.clock() - timedelta(seconds=1))


def login(client, role, origin=ORIGIN):
    """Log in through the real endpoint; returns headers for mutating requests."""
    reply = client.post("/api/login", json={"username": role, "password": PASSWORDS[role]},
                        headers={"Origin": origin})
    assert reply.status_code == 200, reply.text
    return {"Origin": origin, "X-CSRF-Token": reply.json()["csrf_token"]}


@contextmanager
def signed_in_client(repository, coordinator, role="dispatcher", **settings):
    """TestClient logged in as `role`; Origin and X-CSRF-Token become default headers,
    so existing request code stays unchanged (a per-request Origin still overrides it)."""
    from fastapi.testclient import TestClient

    from app.main import create_app
    app = create_app(auth_settings(**settings), repository, coordinator, sessions=MemorySessionStore())
    with TestClient(app) as client:
        client.headers.update(login(client, role))
        yield client
