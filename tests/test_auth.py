"""Stage 6: login, roles, sessions, CSRF/Origin and WebSocket access.

Most tests use MemorySessionStore (a test double). test_postgres_* run the same flows
against real PostgreSQL when TEST_DATABASE_URL is set (marker: postgres)."""
import io
import json
import logging
import os
import re
from contextlib import contextmanager, redirect_stdout
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool
from starlette.websockets import WebSocketDisconnect

from app.auth import require_role
from app.auth.__main__ import main as auth_cli
from app.auth.passwords import InvalidHash, hash_password, parse_hash, verify_password
from app.auth.service import AuthService, LoginThrottle
from app.auth.store import PostgresSessionStore
from app.main import create_app
from app.runtime.coordinator import Coordinator
from app.runtime.state import prepare_scenario
from auth_support import (ORIGIN, PASSWORDS, SECRET, Clock, MemorySessionStore, auth_settings, login,
                          password_hashes)
from test_runtime import MemoryRepository

ROOT = Path(__file__).resolve().parents[1]
ERROR_KEYS = {"code", "message", "details"}


def prepared():
    config = json.loads((ROOT / "shared/scenarios/one_train.json").read_text(encoding="utf-8"))
    state, events, plan = prepare_scenario(config)
    repo = MemoryRepository()
    return Coordinator(repo, state, plan), repo


def cmd(owner, action, cid=None, **kw):
    return dict(command_id=cid or uuid4().hex, run_id=owner.state.run_id, action=action, **kw)


@contextmanager
def station(clock=None, base_url="http://testserver", **overrides):
    owner, repo = prepared()
    store = MemorySessionStore(clock)
    app = create_app(auth_settings(**overrides), repo, owner, sessions=store)
    if clock:
        app.state.auth.clock = clock
    with TestClient(app, base_url=base_url) as client:
        yield client, owner, repo, store, app


def assert_error(reply, status, code):
    assert reply.status_code == status, reply.text
    assert set(reply.json()) == ERROR_KEYS
    assert reply.json()["code"] == code


def use_token(client, token):
    """Replace the session cookie (cookies.set alone would add a second cookie with another domain)."""
    client.cookies.clear()
    client.cookies.set("uzel12_session", token, domain="testserver.local")
    assert list(client.cookies.jar)[0].value == token and len(client.cookies.jar) == 1


def cookie_header(reply):
    return "; ".join(v for k, v in reply.headers.multi_items() if k.lower() == "set-cookie")


# --- passwords and configuration ---------------------------------------------------------

def test_password_hash_roundtrip_and_format():
    encoded = password_hashes()["dispatcher"]
    parsed = parse_hash(encoded)
    assert encoded.startswith("scrypt:15:8:3:") and "$" not in encoded and "=" not in encoded
    assert verify_password(PASSWORDS["dispatcher"], parsed)
    assert not verify_password(PASSWORDS["dispatcher"] + "x", parsed)
    assert PASSWORDS["dispatcher"] not in encoded
    assert "key" not in repr(parsed) and parsed.encode() == encoded
    assert hash_password("same-password-1") != hash_password("same-password-1")  # random salt


def test_password_unicode_is_normalized():
    composed, decomposed = "пароль-й-12345", "пароль-и\u0306-12345"
    assert verify_password(decomposed, parse_hash(hash_password(composed)))


@pytest.mark.parametrize("value", [
    "", "plain-password", "scrypt:15:8:3:abc", "scrypt:x:8:3:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43,
    "scrypt:10:8:1:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43,       # too weak
    "scrypt:16:8:1:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43,       # N*p below the OWASP floor
    "scrypt:20:32:1:AAAAAAAAAAAAAAAAAAAAAA:" + "A" * 43,      # needs > 256 MiB
    "scrypt:15:8:3:AAAA:" + "A" * 43,                          # short salt
    "scrypt:15:8:3:AAAAAAAAAAAAAAAAAAAAAA:A$A",
])
def test_bad_hashes_are_rejected(value):
    with pytest.raises(InvalidHash):
        parse_hash(value)


def test_invalid_configuration_fails_fast_without_echoing_values():
    leaked = "scrypt:15:8:3:SECRETSALTVALUE:not-a-real-key"
    with pytest.raises(RuntimeError) as error:
        AuthService.from_settings(auth_settings(admin_password_hash=leaked))
    assert "ADMIN_PASSWORD_HASH" in str(error.value) and "SECRETSALT" not in str(error.value)
    with pytest.raises(RuntimeError, match="SESSION_SECRET") as error:
        AuthService.from_settings(auth_settings(session_secret="short-secret-value"))
    assert "short-secret-value" not in str(error.value)
    with pytest.raises(ValueError):
        auth_settings(allowed_origins=["*"])
    with pytest.raises(ValueError):
        auth_settings(session_cookie_samesite="none", session_cookie_secure=False)


def test_env_example_is_valid_and_leaves_auth_unconfigured():
    from app.settings import PROJECT_ROOT, Settings
    settings = Settings(_env_file=PROJECT_ROOT / ".env.example")
    auth = AuthService.from_settings(settings)
    assert not auth.configured and auth.cookie_name == "uzel12_session"
    assert settings.session_ttl_s == 28800 and settings.session_cookie_samesite == "strict"


def test_login_without_configuration_is_503():
    with station(session_secret=None) as (client, *_):
        reply = client.post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]})
        assert_error(reply, 503, "AUTH_NOT_CONFIGURED")


def test_cli_hash_password_and_secret(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO("cli-password-123\n"))
    with redirect_stdout(out):
        assert auth_cli(["hash-password", "--stdin"]) == 0
    encoded = out.getvalue().strip()
    assert verify_password("cli-password-123", parse_hash(encoded))
    assert "cli-password-123" not in out.getvalue()
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert auth_cli(["hash-password", "--stdin"]) == 1
    out = io.StringIO()
    with redirect_stdout(out):
        assert auth_cli(["generate-secret"]) == 0
    assert len(out.getvalue().strip()) >= 64


# --- login, logout, me -------------------------------------------------------------------

def test_wrong_password_and_unknown_user_look_the_same():
    with station() as (client, *_):
        wrong = client.post("/api/login", json={"username": "dispatcher", "password": "wrong-password-1"})
        unknown = client.post("/api/login", json={"username": "root", "password": "wrong-password-1"})
        for reply in (wrong, unknown):
            assert_error(reply, 401, "INVALID_CREDENTIALS")
            assert "set-cookie" not in reply.headers
        assert wrong.json() == unknown.json()
        assert_error(client.get("/api/me"), 401, "AUTH_REQUIRED")


def test_login_me_logout_revokes_the_session():
    with station() as (client, owner, repo, store, app):
        reply = client.post("/api/login", json={"username": "dispatcher", "password": PASSWORDS["dispatcher"]},
                            headers={"Origin": ORIGIN})
        assert reply.status_code == 200
        body = reply.json()
        assert body["username"] == "dispatcher" and body["role"] == "dispatcher"
        assert body["permissions"] == ["state:read", "simulation:control"]
        assert reply.headers["cache-control"] == "no-store"
        cookie = cookie_header(reply)
        assert "uzel12_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert "Path=/" in cookie and "Max-Age=28800" in cookie and "Secure" not in cookie
        token = client.cookies.get("uzel12_session")
        assert token and token not in reply.text and body["csrf_token"] != token
        # Only an HMAC of the token is stored.
        assert all(token.encode() != key and len(key) == 32 for key in store.rows)
        me = client.get("/api/me")
        assert me.status_code == 200 and me.json() == body
        assert client.get("/api/state").status_code == 200

        assert_error(client.post("/api/logout", headers={"Origin": ORIGIN}), 403, "CSRF_FAILED")
        assert client.get("/api/me").status_code == 200   # a forged logout does nothing
        out = client.post("/api/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": body["csrf_token"]})
        assert out.status_code == 204
        assert 'uzel12_session=""' in cookie_header(out) and "Max-Age=0" in cookie_header(out)
        assert_error(client.get("/api/me"), 401, "AUTH_REQUIRED")
        # A copied cookie is dead too: the server revoked the session, not just the browser cookie.
        use_token(client, token)
        assert_error(client.get("/api/me"), 401, "SESSION_EXPIRED")
        assert len(client.cookies.jar) == 0          # the server also told the browser to drop it
        use_token(client, token)
        assert_error(client.get("/api/state"), 401, "SESSION_EXPIRED")
        assert client.post("/api/logout").status_code == 204   # idempotent


def test_relogin_rotates_the_token():
    with station() as (client, *_):
        login(client, "viewer")
        first = client.cookies.get("uzel12_session")
        login(client, "dispatcher")
        assert client.get("/api/me").json()["role"] == "dispatcher"
        use_token(client, first)
        assert_error(client.get("/api/me"), 401, "SESSION_EXPIRED")


def test_session_expiry_revokes_access():
    clock = Clock()
    with station(clock=clock, session_ttl_s=60) as (client, *_):
        login(client, "viewer")
        assert client.get("/api/me").json()["expires_at"] == "2026-10-01T12:01:00Z"
        clock.advance(59)
        assert client.get("/api/state").status_code == 200
        clock.advance(1)
        reply = client.get("/api/state")
        assert_error(reply, 401, "SESSION_EXPIRED")
        assert "Max-Age=0" in cookie_header(reply)


def test_service_clock_also_enforces_expiry_if_store_lags():
    clock = Clock()
    with station(clock=clock, session_ttl_s=60) as (client, owner, repo, store, app):
        login(client, "viewer")
        store.clock = lambda: clock.now - timedelta(hours=1)
        clock.advance(61)
        assert_error(client.get("/api/me"), 401, "SESSION_EXPIRED")


def test_rotated_password_hash_invalidates_existing_sessions():
    store = MemorySessionStore()
    owner, repo = prepared()
    first = create_app(auth_settings(), repo, owner, sessions=store)
    with TestClient(first) as client:
        login(client, "dispatcher")
        token = client.cookies.get("uzel12_session")
    rotated = auth_settings(dispatcher_password_hash=hash_password("rotated-password-1"))
    owner2, repo2 = prepared()
    with TestClient(create_app(rotated, repo2, owner2, sessions=store)) as client:
        use_token(client, token)
        assert_error(client.get("/api/me"), 401, "SESSION_EXPIRED")
    assert all(row["revoked_at"] is not None for row in store.rows.values())


def test_login_throttling():
    with station() as (client, *_):
        for _ in range(5):
            assert_error(client.post("/api/login", json={"username": "admin", "password": "bad-password-1"}),
                         401, "INVALID_CREDENTIALS")
        blocked = client.post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]})
        assert_error(blocked, 429, "LOGIN_RATE_LIMITED")
        assert int(blocked.headers["retry-after"]) > 0
        login(client, "viewer")   # other accounts are not locked by attacks on admin


def test_login_throttle_window_and_bounds():
    now = [0.0]
    throttle = LoginThrottle(per_user=2, per_client=3, window_s=10, clock=lambda: now[0], max_keys=50)
    throttle.failure("ip", "admin"); throttle.failure("ip", "admin")
    assert throttle.retry_after("ip", "admin") == 11 and throttle.retry_after("ip", "viewer") == 0
    throttle.failure("ip", "viewer")
    assert throttle.retry_after("ip", "dispatcher") > 0     # per-client cap
    now[0] = 10.5
    assert throttle.retry_after("ip", "admin") == 0
    for i in range(500):
        throttle.failure(f"ip{i}", "admin")
    assert len(throttle.failures) <= 52


# --- roles ------------------------------------------------------------------------------

def test_no_session_is_401_for_protected_endpoints_and_health_is_public():
    with station() as (client, owner, *_):
        assert client.get("/health").status_code == 200
        assert client.get("/docs").status_code == 200
        assert_error(client.get("/api/state"), 401, "AUTH_REQUIRED")
        assert_error(client.get("/api/me"), 401, "AUTH_REQUIRED")
        assert_error(client.post("/api/simulation/control", json=cmd(owner, "start")), 401, "AUTH_REQUIRED")
        # Authentication is checked before the body is validated.
        assert_error(client.post("/api/simulation/control", json={"action": "start"}), 401, "AUTH_REQUIRED")
        use_token(client, "forged-token")
        assert_error(client.get("/api/state"), 401, "SESSION_EXPIRED")


@pytest.mark.parametrize("action,extra", [("speed", {"speed": 10}), ("start", {}), ("reset", {}), ("pause", {})])
def test_viewer_cannot_control_simulation(action, extra):
    with station() as (client, owner, repo, *_):
        headers = login(client, "viewer")
        before = (owner.state.run_id, owner.state.state_version, owner.state.speed, owner.state.paused)
        reply = client.post("/api/simulation/control", json=cmd(owner, action, **extra), headers=headers)
        assert_error(reply, 403, "FORBIDDEN")
        assert (owner.state.run_id, owner.state.state_version, owner.state.speed, owner.state.paused) == before
        assert repo.saved == [] and repo.commands == {}
        assert client.get("/api/state").status_code == 200


@pytest.mark.parametrize("role", ["dispatcher", "admin"])
def test_dispatcher_and_admin_control_simulation(role):
    with station() as (client, owner, repo, *_):
        headers = login(client, role)
        speed = cmd(owner, "speed", speed=10)
        reply = client.post("/api/simulation/control", json=speed, headers=headers)
        assert reply.status_code == 200 and owner.state.speed == 10
        # Idempotency and the command contract are unchanged by auth.
        assert client.post("/api/simulation/control", json=speed, headers=headers).json() == reply.json()
        assert client.post("/api/simulation/control", json=cmd(owner, "start"), headers=headers).status_code == 200
        assert not owner.state.paused
        old_run = owner.state.run_id
        assert client.post("/api/simulation/control", json=cmd(owner, "reset"), headers=headers).status_code == 200
        assert owner.state.run_id != old_run


def test_require_role_admin_dependency():
    with station() as (client, *rest):
        app = rest[-1]

        @app.patch("/api/test-admin-only", dependencies=[Depends(require_role("admin"))])
        async def admin_only():
            return {"ok": True}

        @app.get("/api/test-admin-read")
        async def admin_read(principal=Depends(require_role("admin"))):
            return {"user": principal.username}

        assert_error(client.patch("/api/test-admin-only"), 401, "AUTH_REQUIRED")
        for role in ("viewer", "dispatcher"):
            headers = login(client, role)
            assert_error(client.patch("/api/test-admin-only", headers=headers), 403, "FORBIDDEN")
            assert_error(client.get("/api/test-admin-read"), 403, "FORBIDDEN")
        headers = login(client, "admin")
        assert_error(client.patch("/api/test-admin-only", headers={"Origin": ORIGIN}), 403, "CSRF_FAILED")
        assert client.patch("/api/test-admin-only", headers=headers).json() == {"ok": True}
        assert client.get("/api/test-admin-read").json() == {"user": "admin"}
    with pytest.raises(ValueError):
        require_role("superuser")


def test_every_api_route_is_protected():
    """Safety net for parallel work: any new /api route in OpenAPI must answer 401 without a
    session (before body validation). Only login/logout are public by design."""
    public = {"/api/login", "/api/logout"}
    with station() as (client, *_):
        paths = client.get("/openapi.json").json()["paths"]
        assert {"/api/state", "/api/me", "/api/simulation/control"} <= set(paths)
        checked = 0
        for path, methods in paths.items():
            if not path.startswith("/api/") or path in public:
                continue
            url = re.sub(r"\{[^}]+\}", "x", path)
            for method in methods:
                reply = client.request(method.upper(), url, json={})
                assert reply.status_code == 401, f"{method.upper()} {path} is not protected"
                checked += 1
        assert checked >= 3


# --- CSRF and Origin --------------------------------------------------------------------

def test_csrf_and_origin_protect_cookie_commands():
    with station() as (client, owner, repo, *_):
        headers = login(client, "dispatcher")
        url, body = "/api/simulation/control", cmd(owner, "speed", speed=5)
        assert_error(client.post(url, json=body, headers={"Origin": ORIGIN}), 403, "CSRF_FAILED")
        assert_error(client.post(url, json=body, headers={"Origin": ORIGIN, "X-CSRF-Token": "x" * 43}),
                     403, "CSRF_FAILED")
        for origin in ("https://evil.example", "null", "http://localhost:5173.evil.example"):
            assert_error(client.post(url, json=body, headers=dict(headers, Origin=origin)), 403, "ORIGIN_FORBIDDEN")
        assert_error(client.post(url, json=body, headers={"X-CSRF-Token": headers["X-CSRF-Token"],
                                                         "Referer": "https://evil.example/page"}),
                     403, "ORIGIN_FORBIDDEN")
        assert repo.saved == [] and owner.state.speed == 1
        # Bad Origin is refused even before the session is examined.
        anonymous = TestClient(client.app)
        assert_error(anonymous.post(url, json=body, headers={"Origin": "https://evil.example"}),
                     403, "ORIGIN_FORBIDDEN")
        # A non-browser client without Origin still needs the CSRF token.
        no_origin = {"X-CSRF-Token": headers["X-CSRF-Token"]}
        assert client.post(url, json=body, headers=no_origin).status_code == 200
        referer = dict(no_origin, Referer=ORIGIN + "/dashboard")
        assert client.post(url, json=cmd(owner, "speed", speed=10), headers=referer).status_code == 200


def test_csrf_token_is_bound_to_its_session():
    with station() as (client, owner, *_):
        other = TestClient(client.app)
        foreign = login(other, "admin")["X-CSRF-Token"]
        login(client, "dispatcher")
        assert_error(client.post("/api/simulation/control", json=cmd(owner, "pause"),
                                 headers={"Origin": ORIGIN, "X-CSRF-Token": foreign}), 403, "CSRF_FAILED")


def test_login_from_foreign_origin_is_refused():
    with station() as (client, *_):
        reply = client.post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]},
                            headers={"Origin": "https://evil.example"})
        assert_error(reply, 403, "ORIGIN_FORBIDDEN")
        assert "set-cookie" not in reply.headers


def test_cors_allows_credentials_and_csrf_header_for_exact_origins_only():
    with station() as (client, *_):
        preflight = client.options("/api/simulation/control", headers={
            "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-csrf-token"})
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == ORIGIN
        assert preflight.headers["access-control-allow-credentials"] == "true"
        assert "x-csrf-token" in preflight.headers["access-control-allow-headers"].lower()
        bad = client.options("/api/simulation/control", headers={
            "Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in bad.headers
        error = client.get("/api/me", headers={"Origin": ORIGIN})
        assert error.status_code == 401 and error.headers["access-control-allow-origin"] == ORIGIN
        assert "*" not in error.headers["access-control-allow-origin"]


def test_secure_cookie_mode_for_https():
    with station(base_url="https://testserver", session_cookie_secure=True) as (client, *_):
        reply = client.post("/api/login", json={"username": "viewer", "password": PASSWORDS["viewer"]},
                            headers={"Origin": ORIGIN})
        cookie = cookie_header(reply)
        assert cookie.startswith("__Host-uzel12_session=") and "Secure" in cookie and "Domain" not in cookie
        assert client.get("/api/me").status_code == 200


# --- WebSocket --------------------------------------------------------------------------

def ws_close_code(client, headers):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect("/ws", headers=headers) as ws:
            ws.receive_json()
    return closed.value.code


def test_websocket_requires_session_and_allowed_origin():
    with station() as (client, *_):
        assert ws_close_code(client, {"Origin": ORIGIN}) == 1008
        login(client, "viewer")
        assert ws_close_code(client, {"Origin": "https://evil.example"}) == 1008
        assert ws_close_code(client, {}) == 1008                       # Origin is mandatory for WS
        use_token(client, "forged-token")
        assert ws_close_code(client, {"Origin": ORIGIN}) == 1008
        login(client, "viewer")
        with client.websocket_connect("/ws", headers={"Origin": ORIGIN}) as ws:
            assert ws.receive_json()["type"] == "snapshot"


def test_logout_closes_open_websocket_immediately():
    with station(ws_session_recheck_s=300) as (client, *_):
        headers = login(client, "viewer")
        with client.websocket_connect("/ws", headers={"Origin": ORIGIN}) as ws:
            assert ws.receive_json()["type"] == "snapshot"
            assert client.post("/api/logout", headers=headers).status_code == 204
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    ws.receive_json()
            assert closed.value.code == 4401


def test_websocket_closes_when_session_expires():
    clock = Clock()
    with station(clock=clock, session_ttl_s=60, ws_session_recheck_s=0.05) as (client, *_):
        login(client, "viewer")
        with client.websocket_connect("/ws", headers={"Origin": ORIGIN}) as ws:
            assert ws.receive_json()["type"] == "snapshot"
            clock.advance(61)
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    ws.receive_json()
            assert closed.value.code == 4401 and "expired" in closed.value.reason


def test_websocket_closes_on_out_of_process_revocation_and_on_store_failure():
    with station(ws_session_recheck_s=0.05) as (client, owner, repo, store, app):
        login(client, "dispatcher")
        with client.websocket_connect("/ws", headers={"Origin": ORIGIN}) as ws:
            ws.receive_json()
            assert store.revoke_user("dispatcher") == 1     # e.g. python -m app.auth revoke-sessions
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    ws.receive_json()
            assert closed.value.code == 4401
        login(client, "dispatcher")
        with client.websocket_connect("/ws", headers={"Origin": ORIGIN}) as ws:
            ws.receive_json()
            store.fail = True
            with pytest.raises(WebSocketDisconnect) as closed:
                while True:
                    ws.receive_json()
            assert closed.value.code == 1013
        assert ws_close_code(client, {"Origin": ORIGIN}) == 1013   # unverifiable -> not accepted


# --- secrets ----------------------------------------------------------------------------

def test_no_secrets_in_responses_openapi_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    hashes = password_hashes()
    with station() as (client, owner, repo, store, app):
        bodies = []
        bodies.append(client.post("/api/login", json={"username": "admin", "password": "wrong-admin-pass"}))
        bodies.append(client.post("/api/login", json={"username": "admin", "password": "p" * 2000}))
        assert bodies[-1].status_code == 422
        reply = client.post("/api/login", json={"username": "admin", "password": PASSWORDS["admin"]},
                            headers={"Origin": ORIGIN})
        token = client.cookies.get("uzel12_session")
        bodies += [reply, client.get("/api/me"), client.get("/openapi.json"), client.get("/health"),
                   client.post("/api/simulation/control", json={"bad": 1},
                               headers={"Origin": ORIGIN, "X-CSRF-Token": reply.json()["csrf_token"]})]
        text = "\n".join(r.text for r in bodies)
        logs = caplog.text + repr(app.state.auth) + repr(auth_settings())
        for secret in [SECRET, *PASSWORDS.values(), *hashes.values(), "wrong-admin-pass", "p" * 2000]:
            assert secret not in text and secret not in logs
        assert token not in text and token not in logs
        assert "login_failed user=admin" in caplog.text and "login_succeeded user=admin" in caplog.text
        schema = client.get("/openapi.json").json()
        assert set(schema["components"]["schemas"]["LoginRequest"]["properties"]) == {"username", "password"}
        assert "example" not in json.dumps(schema["components"]["schemas"]["SessionInfo"])


# --- PostgreSQL integration ---------------------------------------------------------------

@pytest.fixture
def pg_schema():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to test with real PostgreSQL")
    schema = "test_" + uuid4().hex
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield make_conninfo(url, options=f"-c search_path={schema}")
    finally:
        with psycopg.connect(url, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.postgres
def test_postgres_session_store_expiry_and_revocation(pg_schema):
    from app.storage.migrate import migrate
    migrate(pg_schema)
    migrate(pg_schema)
    with ConnectionPool(pg_schema, min_size=1, max_size=2) as pool:
        store = PostgresSessionStore(pool)
        record = store.create(session_id="s1", token_hash=b"a" * 32, username="viewer", role="viewer",
                              credential_tag="tag", ttl_s=60)
        assert (record.expires_at - record.created_at).total_seconds() == 60
        assert store.get_active(b"a" * 32) == record
        assert store.get_active(b"b" * 32) is None
        store.create(session_id="s2", token_hash=b"b" * 32, username="admin", role="admin",
                     credential_tag="tag", ttl_s=60)
        with pool.connection() as conn:
            conn.execute("UPDATE auth_sessions SET created_at=now()-interval '2 minutes', "
                         "expires_at=now()-interval '1 second' WHERE id='s2'")
        assert store.get_active(b"b" * 32) is None                    # expired by PostgreSQL clock
        assert store.revoke(b"a" * 32) == "s1" and store.revoke(b"a" * 32) is None
        assert store.get_active(b"a" * 32) is None
        with pytest.raises(psycopg.errors.CheckViolation):
            store.create(session_id="s3", token_hash=b"c" * 32, username="root", role="root",
                         credential_tag="tag", ttl_s=60)
        with pytest.raises(psycopg.errors.CheckViolation):
            store.create(session_id="s4", token_hash=b"short", username="viewer", role="viewer",
                         credential_tag="tag", ttl_s=60)
        with pool.connection() as conn:   # old rows are purged on the next login
            conn.execute("UPDATE auth_sessions SET created_at=now()-interval '3 days', "
                         "expires_at=now()-interval '2 days', revoked_at=NULL")
        store.create(session_id="s5", token_hash=b"d" * 32, username="viewer", role="viewer",
                     credential_tag="tag", ttl_s=60)
        store.create(session_id="s6", token_hash=b"e" * 32, username="viewer", role="viewer",
                     credential_tag="tag", ttl_s=60)
        with pool.connection() as conn:
            assert [r[0] for r in conn.execute("SELECT id FROM auth_sessions ORDER BY id")] == ["s5", "s6"]
        assert store.revoke_user("viewer") == 2 and store.revoke_user(None) == 0


@pytest.mark.postgres
def test_postgres_http_and_websocket_flow(pg_schema):
    from app.storage.bootstrap import bootstrap
    from app.storage.migrate import migrate
    from app.storage.repository import Repository
    migrate(pg_schema)
    bootstrap(pg_schema, "shared/scenarios/one_train.json")
    with ConnectionPool(pg_schema, min_size=1, max_size=4) as pool:
        repo = Repository(pool)
        repo.claim_owner()
        try:
            owner = Coordinator(repo, *repo.load_runtime())
            app = create_app(auth_settings(ws_session_recheck_s=0.1), repo, owner,
                             sessions=PostgresSessionStore(pool))
            with TestClient(app) as client:
                viewer = TestClient(app)
                viewer_headers = login(viewer, "viewer")
                assert viewer.get("/api/state").status_code == 200
                run_id = viewer.get("/api/state").json()["snapshot"]["run_id"]
                assert_error(viewer.post("/api/simulation/control", headers=viewer_headers,
                                         json=dict(command_id="v-speed", run_id=run_id, action="speed", speed=10)),
                             403, "FORBIDDEN")
                headers = login(client, "dispatcher")
                token = client.cookies.get("uzel12_session")
                with pool.connection() as conn:
                    rows = conn.execute("SELECT token_hash, role FROM auth_sessions ORDER BY created_at").fetchall()
                assert [r[1] for r in rows] == ["viewer", "dispatcher"]
                assert all(bytes(r[0]) != token.encode() and len(r[0]) == 32 for r in rows)
                reply = client.post("/api/simulation/control", headers=headers,
                                    json=dict(command_id="d-speed", run_id=run_id, action="speed", speed=10))
                assert reply.status_code == 200
                assert repo.command_result(run_id, "d-speed")["response"] == reply.json()
                with viewer.websocket_connect("/ws", headers={"Origin": ORIGIN}) as ws:
                    assert ws.receive_json()["payload"]["snapshot"]["speed"] == 10
                    with pool.connection() as conn:   # revoked directly in SQL, out of process
                        conn.execute("UPDATE auth_sessions SET revoked_at=now() WHERE role='viewer'")
                    with pytest.raises(WebSocketDisconnect) as closed:
                        while True:
                            ws.receive_json()
                    assert closed.value.code == 4401
                assert_error(viewer.get("/api/state"), 401, "SESSION_EXPIRED")
                assert client.post("/api/logout", headers=headers).status_code == 204
                use_token(client, token)
                assert_error(client.get("/api/me"), 401, "SESSION_EXPIRED")
                with pool.connection() as conn:
                    assert conn.execute("SELECT count(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0] == 0
        finally:
            repo.release_owner()
