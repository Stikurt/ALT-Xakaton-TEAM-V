import asyncio
from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from app.main import create_app
from app.runtime.coordinator import Coordinator
from app.runtime.state import decode_checkpoint
from app.settings import Settings
from app.simulation.engine import SimulationError
from test_runtime import prepared, cmd


CLOSE = dict(kind='close_track',target_id='P04',duration_s=600)
LOCO = dict(kind='locomotive_unavailable',target_id='L01',duration_s=300)
DELAY = dict(kind='delay_train',target_id='T01',delay_s=120)


def incident(owner, spec, cid='incident'):
    return cmd(owner,'incident',cid=cid,incident=spec)


@pytest.mark.parametrize('spec',[CLOSE,LOCO,DELAY])
def test_incident_http_and_websocket(spec):
    owner,repo=prepared()
    with TestClient(create_app(Settings(_env_file=None),repo,owner)) as client:
        with client.websocket_connect('/ws') as ws:
            first=ws.receive_json()
            body=dict(command_id='incident',run_id=owner.state.run_id,**spec)
            reply=client.post('/api/incidents',json=body)
            assert reply.status_code==200 and reply.json()['replan_required']
            message=ws.receive_json()
            snapshot=message['payload']['snapshot']
            assert message['type']=='state_updated' and snapshot['replan_required']
            assert snapshot['state_version']==first['state_version']+1
            assert snapshot['last_seq']==first['payload']['snapshot']['last_seq']+1
            count=len(repo.saved)
            assert client.post('/api/incidents',json=body).json()==reply.json()
            assert len(repo.saved)==count
            assert client.get('/api/state').json()['snapshot']==snapshot
        with client.websocket_connect('/ws') as ws:
            assert ws.receive_json()['payload']['snapshot']['replan_required']


def test_batch_persists_one_pending_request_and_reset_clears_it():
    async def run():
        owner,repo=prepared()
        before=owner.state.state_version
        command=cmd(owner,'incidents',incidents=[CLOSE,LOCO,DELAY])
        reply=await owner.execute(command)
        assert reply['state_version']==before+3
        assert owner.state.tracks['P04']['closed_until_s']==600
        assert owner.state.resources['L01']['unavailable_until_s']==300
        assert owner.state.trains['T01']['expected_arrival_s']==120
        assert len(repo.pending)==1 and len(repo.saved)==3
        assert await owner.execute(command)==reply
        restored=Coordinator(repo,*decode_checkpoint(repo.checkpoint),replan_required=True)
        assert await restored.execute(command)==reply
        assert restored.get_state().snapshot.replan_required
        await restored.execute(cmd(restored,'speed',cid='speed',speed=5))
        assert restored.get_state().snapshot.replan_required
        await restored.execute(cmd(restored,'reset',cid='reset'))
        assert not restored.get_state().snapshot.replan_required and not repo.pending
    asyncio.run(run())


def test_invalid_second_incident_rolls_back_first_and_caches_refusal():
    async def run():
        owner,repo=prepared()
        await owner.execute(cmd(owner,'start'))
        before=deepcopy(owner.state)
        count=len(repo.saved)
        sub=owner.broadcast.subscribe()
        command=cmd(owner,'incidents',cid='batch',incidents=[CLOSE,dict(CLOSE,target_id='P01')])
        with pytest.raises(SimulationError) as error:
            await owner.execute(command)
        assert error.value.code=='INCIDENT_REJECTED'
        assert owner.state==before and len(repo.saved)==count
        assert not repo.pending and sub.queue.empty()
        # Arrival releases the reservation. Occupied tracks may now be closed.
        await owner.tick(120)
        with pytest.raises(SimulationError) as retry:
            await owner.execute(command)
        assert retry.value.message==error.value.message
        assert owner.state.tracks['P04']['availability']=='open'
        await owner.execute(dict(command,command_id='new-batch'))
        assert owner.state.tracks['P01']['availability']=='closed'
        assert owner.state.tracks['P01']['occupant_train_id']=='T01'
    asyncio.run(run())


@pytest.mark.parametrize('spec',[
    dict(CLOSE,target_id='P99'),dict(LOCO,target_id='B01'),
    dict(DELAY,target_id='T99'),
])
def test_unknown_or_wrong_entity_is_domain_conflict(spec):
    owner,repo=prepared()
    with TestClient(create_app(Settings(_env_file=None),repo,owner)) as client:
        before=client.get('/api/state').json()
        body=dict(command_id='bad',run_id=owner.state.run_id,**spec)
        reply=client.post('/api/incidents',json=body)
        assert reply.status_code==409 and reply.json()['code']=='INCIDENT_REJECTED'
        assert client.get('/api/state').json()==before
        assert not repo.pending


@pytest.mark.parametrize('items',[
    [],[CLOSE]*51,[dict(CLOSE,duration_s=0)],[dict(CLOSE,duration_s=True)],
    [dict(CLOSE,duration_s=1.5)],[dict(CLOSE,delay_s=2)],
    [dict(CLOSE,extra='unexpected')],[dict(DELAY,delay_s=None)],
])
def test_batch_validation_before_any_effect(items):
    owner,repo=prepared()
    with TestClient(create_app(Settings(_env_file=None),repo,owner)) as client:
        reply=client.post('/api/incidents/batch',json=dict(command_id='bad',run_id=owner.state.run_id,incidents=items))
        assert reply.status_code==422
        assert not repo.saved and not repo.commands and not repo.pending


def test_batch_http_origin_stale_run_and_changed_payload():
    owner,repo=prepared()
    with TestClient(create_app(Settings(_env_file=None),repo,owner)) as client:
        body=dict(command_id='batch',run_id=owner.state.run_id,incidents=[CLOSE,LOCO])
        assert client.post('/api/incidents/batch',json=body,headers={'Origin':'https://bad.example'}).status_code==403
        stale=client.post('/api/incidents/batch',json=dict(body,run_id='unknown-run'))
        assert stale.status_code==409 and stale.json()['code']=='STALE_RUN'
        assert client.post('/api/incidents/batch',json=body).status_code==200
        conflict=client.post('/api/incidents/batch',json=dict(body,incidents=[DELAY]))
        assert conflict.status_code==409 and conflict.json()['code']=='COMMAND_ID_REUSED'
        assert len(repo.saved)==2


def test_simultaneous_batch_retries_execute_once():
    async def run():
        owner,repo=prepared()
        await owner.start()
        try:
            command=cmd(owner,'incidents',incidents=[CLOSE,LOCO])
            results=await asyncio.gather(*(owner.submit(command) for _ in range(10)))
            assert all(result==results[0] for result in results)
            assert len(repo.saved)==2 and len(repo.pending)==1
        finally:
            await owner.stop()
    asyncio.run(run())


def test_storage_failure_leaves_no_incident_or_pending_request():
    owner,repo=prepared()
    with TestClient(create_app(Settings(_env_file=None),repo,owner)) as client:
        before=owner.get_state().model_dump()
        repo.fail=True
        reply=client.post('/api/incidents',json=dict(command_id='failed',run_id=owner.state.run_id,**CLOSE))
        assert reply.status_code==503
        assert owner.get_state().model_dump()==before
        assert not repo.pending and not repo.saved and not repo.commands


def test_clocks_do_not_change_version_and_expiry_restores_resources():
    async def run():
        owner,repo=prepared()
        await owner.execute(cmd(owner,'incidents',incidents=[CLOSE,LOCO]))
        await owner.execute(cmd(owner,'start',cid='start'))
        version=owner.state.state_version
        await owner.tick(60)
        assert owner.state.state_version==version and owner.state.sim_time_s==60
        await owner.tick(540)
        assert owner.state.tracks['P04']['availability']=='open'
        assert owner.state.resources['L01']['availability']=='available'
        assert owner.get_state().snapshot.replan_required
    asyncio.run(run())
