"""Integration test creates and drops only its own randomly named schema."""
import os
import asyncio
from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from app.storage.bootstrap import bootstrap
from app.storage.migrate import migrate
from app.storage.repository import Repository
from app.runtime.coordinator import Coordinator
from app.simulation import apply_command
from app.planner import RULES


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
        with ConnectionPool(isolated,min_size=1,max_size=2) as pool:
            repo = Repository(pool)
            repo.claim_owner()
            try:
                async def run():
                    state,template=repo.load_runtime()
                    owner=Coordinator(repo,state,template)
                    await owner.execute(dict(command_id='start',run_id=state.run_id,action='start'))
                    await owner.tick(600)
                    assert owner.state.trains['T01']['status']=='departed'
                    with pool.connection() as conn:
                        assert conn.execute("SELECT actual_end_s FROM operation_actuals WHERE operation_id='T01_03_departure'").fetchone()[0]==600
                    reset=dict(command_id='reset',run_id=state.run_id,action='reset')
                    result=await owner.execute(reset)
                    assert result['run_id']!=state.run_id
                    # A new coordinator uses durable idempotency across old run IDs.
                    restored=Coordinator(repo,*repo.load_runtime())
                    assert await restored.execute(reset)==result
                    assert restored.state.run_id==result['run_id']
                    # Inject a uniqueness error after one successful INSERT in a transaction.
                    speed=dict(command_id='failed-speed',run_id=restored.state.run_id,action='speed',speed=10)
                    transition=apply_command(restored.state,speed,rules=RULES)
                    duplicate=deepcopy(transition.events[0])
                    duplicate.update(event_id=f'{restored.state.run_id}:1',seq=1)
                    transition.events.append(duplicate)
                    with pytest.raises(psycopg.errors.UniqueViolation):
                        repo.save_transition(restored.state,transition,template,speed,'test-hash')
                    assert repo.load_runtime()[0].speed==1
                    assert repo.command_result(speed['run_id'],speed['command_id']) is None
                asyncio.run(run())
            finally:
                repo.release_owner()
    finally:
        with psycopg.connect(url,autocommit=True) as admin:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
