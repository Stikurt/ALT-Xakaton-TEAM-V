import asyncio
from copy import deepcopy
import csv
import io
import os
import time
from uuid import uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool
import pytest

from app.api.history import export_csv
from app.main import create_app
from app.planner import RULES
from app.runtime.coordinator import Coordinator
from app.runtime.planning import PlannerProcess,calculate_variants
from app.runtime.state import encode_checkpoint
from app.settings import Settings
from app.simulation import apply_plan
from app.simulation.engine import SimulationError
from app.storage.bootstrap import bootstrap
from app.storage.history import HistoryError
from app.storage.migrate import migrate
from app.storage.repository import Repository
from test_runtime import cmd


@pytest.fixture
def repository():
    url=os.environ.get('TEST_DATABASE_URL')
    if not url: pytest.skip('Set TEST_DATABASE_URL for PostgreSQL integration')
    schema='test_'+uuid4().hex
    with psycopg.connect(url,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
    isolated=make_conninfo(url,options=f'-c search_path={schema}')
    try:
        migrate(isolated)
        bootstrap(isolated)
        with ConnectionPool(isolated,min_size=1,max_size=4) as pool:
            repo=Repository(pool)
            repo.claim_owner()
            try: yield repo
            finally: repo.release_owner()
    finally:
        # Only this test's freshly generated schema is removed.
        with psycopg.connect(url,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


@pytest.mark.postgres
def test_history_replays_events_between_snapshots_and_archive_csv(repository):
    repo=repository
    async def run():
        owner=Coordinator(repo,*repo.load_runtime())
        old_run=owner.state.run_id
        await owner.execute(cmd(owner,'start'))
        await owner.tick(35)
        await owner.execute(cmd(owner,'pause',cid='pause'))
        await owner.execute(cmd(owner,'start',cid='resume'))
        await owner.tick(965)
        before=deepcopy(repo.load_runtime()[0])
        for at in (0,30,34,35,59,60,119,120,479,480,599,600,999,1000):
            history=repo.history(old_run,at)
            assert history.snapshot.sim_time_s==at and history.read_only
            train=history.snapshot.trains[0]
            expected='moving' if at<120 or 480<=at<600 else 'on_track' if at<480 else 'departed'
            assert train.status==expected
        # at=35 is reconstructed from a command event after the time-0 snapshot.
        with repo.pool.connection() as conn:
            assert conn.execute('SELECT count(*) FROM snapshots WHERE run_id=%s AND sim_time_s=35',(old_run,)).fetchone()[0]==0
            points=[row[0] for row in conn.execute('SELECT sim_time_s FROM snapshots WHERE run_id=%s',(old_run,)).fetchall()]
            assert set(range(60,961,60))<=set(points)
        assert repo.load_runtime()[0]==before
        with pytest.raises(HistoryError) as error: repo.history(old_run,1001)
        assert error.value.code=='HISTORY_IN_FUTURE'
        await owner.execute(cmd(owner,'reset',cid='reset'))
        assert repo.history(old_run,600).snapshot.trains[0].status=='departed'
        assert repo.history(owner.state.run_id,0).snapshot.trains[0].status=='scheduled'
        row=list(csv.DictReader(io.StringIO(export_csv(repo.history(old_run)).lstrip('\ufeff'))))[0]
        assert row['run_id']==old_run and row['actual_departure_s']=='600'
        early=list(csv.DictReader(io.StringIO(export_csv(repo.history(old_run,599)).lstrip('\ufeff'))))[0]
        assert early['actual_departure_s']==''
        with repo.pool.connection() as conn:
            conn.execute('UPDATE events SET projection=NULL WHERE run_id=%s AND sim_time_s=35',(old_run,))
        with pytest.raises(HistoryError) as gap: repo.history(old_run,35)
        assert gap.value.code=='HISTORY_UNAVAILABLE'
    asyncio.run(run())


@pytest.mark.postgres
def test_planning_jobs_recovery_coalescing_and_atomic_acceptance(repository):
    repo=repository
    async def run():
        owner=Coordinator(repo,*repo.load_runtime())
        request=cmd(owner,'replan')
        reply=await owner.execute(request)
        assert await owner.execute(request)==reply
        job=repo.claim_replan(owner.state)
        assert job['job_id']==reply['job_id'] and repo.get_job(job['job_id'])['status']=='running'
        assert repo.needs_replan(owner.state.run_id)
        second=await owner.execute(cmd(owner,'replan',cid='second'))
        third=await owner.execute(cmd(owner,'replan',cid='third'))
        assert repo.get_job(second['job_id'])['status']=='superseded'
        assert repo.get_job(third['job_id'])['status']=='queued'
        repo.recover_planning(owner.state)
        assert repo.get_job(job['job_id'])['error']['code']=='PLANNER_INTERRUPTED'
        job=repo.claim_replan(owner.state)
        candidates=calculate_variants(encode_checkpoint(owner.state,owner.initial_plan),2)
        assert repo.finish_replan(job,dict(plans=candidates,identical=True,stale=False))
        assert repo.needs_replan(owner.state.run_id) and repo.get_replan_request(owner.state.run_id) is None
        candidate=candidates[0]
        assert repo.get_plan(candidate['id'])==candidate
        command=cmd(owner,'apply_plan',cid='apply',plan_id=candidate['id'],expected_state_version=owner.state.state_version)
        # A deliberately duplicated event rolls back plan acceptance and required flag.
        transition=apply_plan(owner.state,candidate,rules=RULES)
        transition.events.append(deepcopy(transition.events[0]))
        with pytest.raises(psycopg.errors.UniqueViolation):
            repo.save_transition(owner.state,transition,owner.initial_plan,command,'failed')
        assert repo.needs_replan(owner.state.run_id)
        assert repo.load_runtime()[0].active_plan['id']!=candidate['id']
        with repo.pool.connection() as conn:
            assert conn.execute('SELECT applied_at FROM plans WHERE id=%s',(candidate['id'],)).fetchone()[0] is None
        result=await owner.execute(command)
        assert not result['replan_required'] and not repo.needs_replan(owner.state.run_id)
        restored=Coordinator(repo,*repo.load_runtime(),replan_required=repo.needs_replan(owner.state.run_id))
        assert await restored.execute(command)==result
        with repo.pool.connection() as conn:
            assert conn.execute('SELECT applied_at FROM plans WHERE id=%s',(candidate['id'],)).fetchone()[0] is not None
        stale=dict(command,command_id='stale')
        with pytest.raises(SimulationError) as error: await restored.execute(stale)
        assert error.value.code=='STALE_PLAN'
        assert repo.command_result(stale['run_id'],'stale')['response_status']==409
    asyncio.run(run())


@pytest.mark.postgres
def test_http_incident_to_background_plan_to_departure_and_history(repository):
    repo=repository
    owner=Coordinator(repo,*repo.load_runtime(),planner=PlannerProcess())
    with TestClient(create_app(Settings(_env_file=None),repo,owner)) as client:
        run_id=owner.state.run_id
        assert client.post('/api/incidents',json=dict(command_id='delay',run_id=run_id,
            kind='delay_train',target_id='T01',delay_s=120)).status_code==200
        request=dict(command_id='request',run_id=run_id)
        accepted=client.post('/api/replans',json=request)
        assert accepted.status_code==202
        assert client.post('/api/replans',json=request).json()==accepted.json()
        job_id=accepted.json()['job_id']
        deadline=time.monotonic()+6
        while True:
            job=client.get('/api/replans/'+job_id).json()
            if job['status'] in ('failed','completed'): break
            assert time.monotonic()<deadline
            assert client.get('/api/state').status_code==200
            time.sleep(.03)
        assert job['status']=='completed'
        listed=client.get('/api/replans',params=dict(run_id=run_id)).json()
        assert job_id in [item['job_id'] for item in listed]
        plan_id=job['plan_ids'][0]
        assert client.get('/api/plans/'+plan_id).json()['applicable']
        body=dict(command_id='accept',run_id=run_id,expected_state_version=owner.state.state_version)
        assert client.post('/api/plans/'+plan_id+'/apply',json=body).status_code==200
        assert client.get('/api/history',params=dict(run_id=run_id,at_s=0)).status_code==200
        assert client.get('/api/export.csv',params=dict(run_id=run_id)).status_code==200
    # Resume deterministically after the HTTP actor has stopped.
    async def finish():
        restored=Coordinator(repo,*repo.load_runtime())
        await restored.execute(cmd(restored,'start',cid='start'))
        await restored.tick(720)
        assert restored.state.trains['T01']['status']=='departed'
        assert repo.history(run_id,719).snapshot.trains[0].status=='moving'
        assert repo.history(run_id,720).snapshot.trains[0].status=='departed'
    asyncio.run(finish())
