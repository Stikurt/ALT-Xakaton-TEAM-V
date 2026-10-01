"""Server-side sessions. PostgreSQL stores only an HMAC of the opaque cookie token."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from psycopg_pool import ConnectionPool

# Revoked/expired rows are kept a day for audit, then removed opportunistically on login.
RETENTION = "1 day"


@dataclass(frozen=True)
class SessionRecord:
    id: str
    username: str
    role: str
    credential_tag: str
    created_at: datetime
    expires_at: datetime


class SessionStore(Protocol):
    """Synchronous; the auth service calls it through a worker thread."""
    def create(self, *, session_id: str, token_hash: bytes, username: str, role: str,
               credential_tag: str, ttl_s: int) -> SessionRecord: ...
    def get_active(self, token_hash: bytes) -> SessionRecord | None: ...
    def revoke(self, token_hash: bytes) -> str | None: ...
    def revoke_user(self, username: str | None) -> int: ...


_COLUMNS = "id,username,role,credential_tag,created_at,expires_at"


class PostgresSessionStore:
    """Expiry is decided by PostgreSQL now(), so one clock rules every check."""
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def create(self, *, session_id, token_hash, username, role, credential_tag, ttl_s):
        with self.pool.connection() as conn:
            conn.execute(f"""DELETE FROM auth_sessions WHERE expires_at < now() - interval '{RETENTION}'
                             OR revoked_at < now() - interval '{RETENTION}'""")
            row = conn.execute(f"""INSERT INTO auth_sessions(id,token_hash,username,role,credential_tag,expires_at)
                VALUES (%s,%s,%s,%s,%s,now() + make_interval(secs => %s)) RETURNING {_COLUMNS}""",
                (session_id, token_hash, username, role, credential_tag, ttl_s)).fetchone()
        return SessionRecord(*row)

    def get_active(self, token_hash):
        with self.pool.connection() as conn:
            row = conn.execute(f"""SELECT {_COLUMNS} FROM auth_sessions
                WHERE token_hash=%s AND revoked_at IS NULL AND expires_at > now()""",
                (token_hash,)).fetchone()
        return SessionRecord(*row) if row else None

    def revoke(self, token_hash):
        with self.pool.connection() as conn:
            row = conn.execute("""UPDATE auth_sessions SET revoked_at=now()
                WHERE token_hash=%s AND revoked_at IS NULL RETURNING id""", (token_hash,)).fetchone()
        return row[0] if row else None

    def revoke_user(self, username=None):
        """Revoke every active session of one user, or of everybody when username is None."""
        with self.pool.connection() as conn:
            cursor = conn.execute("""UPDATE auth_sessions SET revoked_at=now()
                WHERE revoked_at IS NULL AND expires_at > now() AND (%s::text IS NULL OR username=%s)""",
                (username, username))
            return cursor.rowcount
