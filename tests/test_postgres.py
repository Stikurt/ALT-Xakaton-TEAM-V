"""Integration test creates and drops only its own randomly named schema."""
import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from app.storage.bootstrap import bootstrap
from app.storage.migrate import migrate
from app.storage.repository import Repository


@pytest.mark.postgres
def test_migrations_bootstrap_roundtrip_and_unique_events():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to test with real PostgreSQL")
    schema = "test_" + uuid4().hex
    with psycopg.connect(url,autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    isolated = make_conninfo(url,options=f"-c search_path={schema}")
    try:
        migrate(isolated)
        migrate(isolated)
        run_id = bootstrap(isolated)
        assert bootstrap(isolated) == run_id
        with ConnectionPool(isolated,min_size=1,max_size=2) as pool:
            repo = Repository(pool)
            repo.check_ready()
            snapshot = repo.current_state().snapshot
            assert snapshot.run_id == run_id and snapshot.last_seq == 1
            assert snapshot.trains[0].id == "T01"
        with psycopg.connect(isolated) as conn:
            assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
            with pytest.raises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    conn.execute("INSERT INTO events(run_id,event_id,seq,sim_time_s,type,payload) SELECT run_id,event_id,2,sim_time_s,type,payload FROM events LIMIT 1")
            with pytest.raises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    conn.execute("INSERT INTO events(run_id,event_id,seq,sim_time_s,type,payload) SELECT run_id,'another-event',seq,sim_time_s,type,payload FROM events LIMIT 1")
            assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    finally:
        with psycopg.connect(url,autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
