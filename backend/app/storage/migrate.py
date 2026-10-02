"""Explicit, transactional SQL migrations; no destructive startup migrations."""
import hashlib
from pathlib import Path

import psycopg

from app.settings import Settings

MIGRATIONS = Path(__file__).with_name("migrations")


def migrate(database_url: str) -> None:
    with psycopg.connect(database_url, connect_timeout=5) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(12001001)")
        conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            name TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            row = conn.execute(
                "SELECT sha256 FROM schema_migrations WHERE name = %s", (path.name,)
            ).fetchone()
            if row:
                if row[0] != checksum:
                    raise RuntimeError(f"Applied migration was modified: {path.name}")
                continue
            conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations(name, sha256) VALUES (%s, %s)",
                         (path.name, checksum))


if __name__ == "__main__":
    migrate(Settings().database_url.get_secret_value())
    print("Migrations applied successfully.")
