"""Sessions, roles, CSRF and Origin checks. Secrets never leave this module in clear text."""
import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Literal
from urllib.parse import urlsplit

from app.auth.passwords import InvalidHash, ScryptHash, hash_password, parse_hash, verify_password
from app.auth.store import SessionRecord, SessionStore

logger = logging.getLogger("app.auth")

Role = Literal["viewer", "dispatcher", "admin"]
ROLE_RANK: dict[str, int] = {"viewer": 1, "dispatcher": 2, "admin": 3}
# Fixed accounts of the station: username == role. No registration (out of scope of the TZ).
USER_ROLES: dict[str, str] = {"viewer": "viewer", "dispatcher": "dispatcher", "admin": "admin"}
HASH_ENV = {"admin": "ADMIN_PASSWORD_HASH", "dispatcher": "DISPATCHER_PASSWORD_HASH",
            "viewer": "VIEWER_PASSWORD_HASH"}
PERMISSIONS: dict[str, list[str]] = {
    "viewer": ["state:read"],
    "dispatcher": ["state:read", "simulation:control"],
    "admin": ["state:read", "simulation:control", "config:write"],
}
CSRF_HEADER = "X-CSRF-Token"
MIN_SECRET_CHARS = 32
MAX_TOKEN_CHARS = 128
WS_SESSION_ENDED = 4401      # application close code: log in again, then reconnect
WS_TRY_AGAIN = 1013


class AuthError(Exception):
    def __init__(self, status: int, code: str, message: str, *, headers=None, clear_cookie=False):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}
        self.clear_cookie = clear_cookie


def unauthorized(code="AUTH_REQUIRED", message="Требуется вход в систему.", clear_cookie=False):
    return AuthError(401, code, message, clear_cookie=clear_cookie)


def forbidden(code="FORBIDDEN", message="Недостаточно прав для этого действия."):
    return AuthError(403, code, message)


@dataclass(frozen=True)
class Principal:
    session_id: str
    username: str
    role: str
    expires_at: datetime
    csrf_token: str = field(repr=False)
    token_hash: bytes = field(repr=False)

    @property
    def permissions(self) -> list[str]:
        return list(PERMISSIONS[self.role])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None


def safe_username(username: str) -> str:
    """Log only known account names; arbitrary input could forge log lines."""
    return username if username in USER_ROLES else "<unknown>"


class LoginThrottle:
    """In-process limiter (one Uvicorn worker by design): per (client, username) and per client."""
    def __init__(self, *, per_user=5, per_client=20, window_s=300, clock=time.monotonic, max_keys=10_000):
        self.per_user, self.per_client, self.window_s = per_user, per_client, window_s
        self.clock, self.max_keys = clock, max_keys
        self.failures: dict[tuple, deque] = {}

    def _bucket(self, key):
        now = self.clock()
        bucket = self.failures.get(key)
        if bucket is not None:
            while bucket and bucket[0] <= now - self.window_s:
                bucket.popleft()
            if not bucket:
                del self.failures[key]
                bucket = None
        return bucket, now

    def retry_after(self, client: str, username: str) -> int:
        wait = 0
        for key, limit in (((client, username), self.per_user), ((client,), self.per_client)):
            bucket, now = self._bucket(key)
            if bucket is not None and len(bucket) >= limit:
                wait = max(wait, int(bucket[0] + self.window_s - now) + 1)
        return wait

    def failure(self, client: str, username: str) -> None:
        if len(self.failures) >= self.max_keys:   # bounded memory under a username spray
            for key in list(self.failures)[: self.max_keys // 10]:
                del self.failures[key]
        now = self.clock()
        for key in ((client, username), (client,)):
            self.failures.setdefault(key, deque()).append(now)

    def success(self, client: str, username: str) -> None:
        self.failures.pop((client, username), None)


class AuthService:
    def __init__(self, *, secret: str | None, users: dict[str, ScryptHash], ttl_s: int,
                 cookie_secure: bool, samesite: str, allowed_origins: list[str],
                 recheck_s: float, store: SessionStore | None = None,
                 clock: Callable[[], datetime] = _utcnow, throttle: LoginThrottle | None = None):
        self._secret = secret.encode("utf-8") if secret else None
        self._users = users
        self.ttl_s, self.cookie_secure, self.samesite = ttl_s, cookie_secure, samesite
        self.allowed_origins = frozenset(allowed_origins)
        self.recheck_s = recheck_s
        self.store = store
        self.clock = clock
        self.throttle = throttle or LoginThrottle()
        self._hash_slots = asyncio.Semaphore(2)   # bounds CPU and scrypt memory
        self._dummy: ScryptHash | None = None
        self._watchers: dict[str, set[asyncio.Event]] = {}
        # __Host- forbids Domain and requires Secure + Path=/, so it is used only over HTTPS.
        self.cookie_name = "__Host-uzel12_session" if cookie_secure else "uzel12_session"

    def __repr__(self):
        return f"AuthService(users={sorted(self._users)}, ttl_s={self.ttl_s}, secure={self.cookie_secure})"

    @classmethod
    def from_settings(cls, settings, **kwargs) -> "AuthService":
        secret = settings.session_secret.get_secret_value() if settings.session_secret else None
        if secret is not None and len(secret) < MIN_SECRET_CHARS:
            raise RuntimeError(f"SESSION_SECRET must contain at least {MIN_SECRET_CHARS} characters; "
                               "generate one with: python -m app.auth generate-secret")
        users = {}
        for username, env in HASH_ENV.items():
            value = getattr(settings, env.lower())
            if value is None or not value.get_secret_value().strip():
                continue
            try:
                users[username] = parse_hash(value.get_secret_value())
            except InvalidHash as exc:   # the message describes the format only, never the value
                raise RuntimeError(f"{env} is invalid ({exc}); regenerate it with: "
                                   "python -m app.auth hash-password") from None
        if secret is None or not users:
            logger.warning("auth_not_configured: set SESSION_SECRET and at least one *_PASSWORD_HASH; "
                           "login answers 503 until then")
        if not settings.session_cookie_secure:
            logger.warning("session_cookie_secure=false: HTTP development mode, do not expose the server")
        return cls(secret=secret, users=users, ttl_s=settings.session_ttl_s,
                   cookie_secure=settings.session_cookie_secure,
                   samesite=settings.session_cookie_samesite,
                   allowed_origins=settings.allowed_origins,
                   recheck_s=settings.ws_session_recheck_s, **kwargs)

    # --- derived values ------------------------------------------------------------------
    def _mac(self, label: bytes, data: bytes) -> bytes:
        return hmac.new(self._secret, label + b"\0" + data, hashlib.sha256).digest()

    def token_hash(self, token: str) -> bytes:
        return self._mac(b"session-token", token.encode("ascii", "replace"))

    def _csrf(self, token: str) -> str:
        return base64.urlsafe_b64encode(self._mac(b"csrf", token.encode("ascii", "replace"))).rstrip(b"=").decode()

    def _credential_tag(self, username: str) -> str:
        return self._mac(b"credential", f"{username}\0{self._users[username].encode()}".encode()).hex()[:32]

    @property
    def configured(self) -> bool:
        return self._secret is not None and bool(self._users)

    def _store(self) -> SessionStore:
        if self.store is None:
            raise AuthError(503, "AUTH_UNAVAILABLE", "Хранилище сессий не подключено.")
        return self.store

    def _dummy_hash(self) -> ScryptHash:
        # Unknown users cost the same scrypt work as known ones (no username oracle by timing).
        if self._dummy is None:
            template = next(iter(self._users.values()), None)
            kw = dict(log_n=template.log_n, r=template.r, p=template.p) if template else {}
            self._dummy = parse_hash(hash_password(secrets.token_urlsafe(32), **kw))
        return self._dummy

    def _principal(self, record: SessionRecord, token: str, token_hash: bytes) -> Principal:
        return Principal(record.id, record.username, record.role, record.expires_at,
                         self._csrf(token), token_hash)

    # --- login / session lifecycle -------------------------------------------------------
    async def login(self, username: str, password: str, client: str) -> tuple[str, Principal]:
        if not self.configured:
            raise AuthError(503, "AUTH_NOT_CONFIGURED",
                            "Вход не настроен: задайте SESSION_SECRET и хеши паролей (docs/auth.md).")
        store = self._store()
        wait = self.throttle.retry_after(client, username)
        if wait:
            logger.warning("login_throttled user=%s", safe_username(username))
            raise AuthError(429, "LOGIN_RATE_LIMITED", "Слишком много неудачных попыток входа. Повторите позже.",
                            headers={"Retry-After": str(wait)})
        expected = self._users.get(username)
        async with self._hash_slots:
            reference = expected or await asyncio.to_thread(self._dummy_hash)
            matches = await asyncio.to_thread(verify_password, password, reference)
        if not (matches and expected is not None):
            self.throttle.failure(client, username)
            logger.warning("login_failed user=%s", safe_username(username))
            raise unauthorized("INVALID_CREDENTIALS", "Неверное имя пользователя или пароль.")
        self.throttle.success(client, username)
        token = secrets.token_urlsafe(32)
        token_hash = self.token_hash(token)
        record = await asyncio.to_thread(
            store.create, session_id=secrets.token_hex(8), token_hash=token_hash, username=username,
            role=USER_ROLES[username], credential_tag=self._credential_tag(username), ttl_s=self.ttl_s)
        logger.info("login_succeeded user=%s role=%s session=%s", username, record.role, record.id)
        return token, self._principal(record, token, token_hash)

    async def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise unauthorized()
        if self._secret is None or len(token) > MAX_TOKEN_CHARS:
            raise unauthorized("SESSION_EXPIRED", "Сессия недействительна. Войдите снова.", clear_cookie=True)
        token_hash = self.token_hash(token)
        record = await asyncio.to_thread(self._store().get_active, token_hash)
        if record is None or record.expires_at <= self.clock():
            raise unauthorized("SESSION_EXPIRED", "Сессия истекла или завершена. Войдите снова.",
                               clear_cookie=True)
        if (record.username not in self._users or USER_ROLES[record.username] != record.role
                or not hmac.compare_digest(record.credential_tag, self._credential_tag(record.username))):
            # The password hash was rotated or the account disabled: the old session is dead.
            await asyncio.to_thread(self._store().revoke, token_hash)
            self._notify(record.id)
            raise unauthorized("SESSION_EXPIRED", "Учётные данные изменены. Войдите снова.", clear_cookie=True)
        return self._principal(record, token, token_hash)

    async def logout(self, token: str) -> str | None:
        if self._secret is None or not token or len(token) > MAX_TOKEN_CHARS:
            return None
        session_id = await asyncio.to_thread(self._store().revoke, self.token_hash(token))
        if session_id:
            logger.info("logout session=%s", session_id)
            self._notify(session_id)
        return session_id

    # --- request checks ------------------------------------------------------------------
    def check_origin(self, headers, *, require: bool = False) -> None:
        """Origin must be in the exact allow-list. Without Origin, Referer is checked instead;
        with neither (curl, scripts) a mutating request still needs the CSRF header."""
        origin = headers.get("origin")
        if origin is None and headers.get("referer") is not None:
            origin = _origin_of(headers["referer"]) or "null"
        if origin is None:
            if require:
                raise forbidden("ORIGIN_FORBIDDEN", "Origin не разрешён")
            return
        if origin not in self.allowed_origins:
            raise forbidden("ORIGIN_FORBIDDEN", "Origin не разрешён")

    def check_csrf(self, principal: Principal, headers) -> None:
        supplied = headers.get(CSRF_HEADER) or ""
        if not hmac.compare_digest(supplied.encode("utf-8", "replace"), principal.csrf_token.encode()):
            raise forbidden("CSRF_FAILED", f"Отсутствует или неверен заголовок {CSRF_HEADER}.")

    # --- cookies -------------------------------------------------------------------------
    def set_cookie(self, response, token: str) -> None:
        response.set_cookie(self.cookie_name, token, max_age=self.ttl_s, path="/", httponly=True,
                            secure=self.cookie_secure, samesite=self.samesite)

    def clear_cookie(self, response) -> None:
        response.delete_cookie(self.cookie_name, path="/", httponly=True,
                               secure=self.cookie_secure, samesite=self.samesite)

    # --- WebSocket lifetime --------------------------------------------------------------
    def _notify(self, session_id: str) -> None:
        for event in tuple(self._watchers.get(session_id, ())):
            event.set()

    async def wait_session_end(self, principal: Principal) -> tuple[int, str]:
        """Returns a close (code, reason) once the session expires, is revoked or can't be checked.
        Logout in this process wakes it at once; other revocations are seen within recheck_s."""
        event = asyncio.Event()
        self._watchers.setdefault(principal.session_id, set()).add(event)
        try:
            while True:
                remaining = (principal.expires_at - self.clock()).total_seconds()
                if remaining <= 0:
                    return WS_SESSION_ENDED, "Session expired"
                try:
                    await asyncio.wait_for(event.wait(), timeout=min(remaining, self.recheck_s))
                    return WS_SESSION_ENDED, "Session revoked"
                except asyncio.TimeoutError:
                    pass
                try:
                    record = await asyncio.to_thread(self._store().get_active, principal.token_hash)
                except Exception as exc:   # fail closed: an unverifiable session is not kept open
                    logger.error("ws_session_check_failed error_type=%s", type(exc).__name__)
                    return WS_TRY_AGAIN, "Session check unavailable"
                if record is None:
                    expired = principal.expires_at <= self.clock()
                    return WS_SESSION_ENDED, "Session expired" if expired else "Session revoked"
        finally:
            watchers = self._watchers.get(principal.session_id)
            if watchers is not None:
                watchers.discard(event)
                if not watchers:
                    del self._watchers[principal.session_id]
