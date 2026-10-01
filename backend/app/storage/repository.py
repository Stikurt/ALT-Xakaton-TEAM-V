from psycopg_pool import ConnectionPool

from app.domain.models import StateResponse


class NotInitialized(Exception):
    pass


class Repository:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def check_ready(self) -> None:
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE name='001_initial.sql'),
                       EXISTS(SELECT 1 FROM runs r JOIN snapshots s ON s.run_id=r.id
                              WHERE r.status='active')
            """).fetchone()
            if not all(row):
                raise NotInitialized("Run migrations and bootstrap first")

    def current_state(self) -> StateResponse:
        # A single statement sees config + snapshot in one PostgreSQL MVCC snapshot.
        with self.pool.connection() as conn:
            row = conn.execute("""
                SELECT s.payload, c.config->'topology'
                FROM runs r
                JOIN station_config c ON c.id=r.station_config_id
                JOIN LATERAL (
                    SELECT payload FROM snapshots WHERE run_id=r.id
                    ORDER BY sim_time_s DESC, last_seq DESC LIMIT 1
                ) s ON true
                WHERE r.status='active'
            """).fetchone()
        if row is None:
            raise NotInitialized("No active run")
        return StateResponse(snapshot=row[0], topology=row[1])
