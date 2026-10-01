import asyncio
from copy import deepcopy
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.domain.models import ControlCommand
from app.main import create_app
from app.runtime.broadcast import Broadcast
from app.runtime.coordinator import Coordinator, RuntimeUnavailable
from app.runtime.state import prepare_scenario, encode_checkpoint, decode_checkpoint, response
from app.simulation.engine import SimulationError
from auth_support import ORIGIN, MemorySessionStore, auth_settings, login

ROOT = Path(__file__).resolve().parents[1]


class MemoryRepository:
    """Fault-injectable test double; production always uses PostgreSQL."""
    def __init__(self):
        self.saved = []
        self.commands = {}
        self.pending = {}
        self.fail = False

    def check_ready(self): pass
    def command_result(self, run_id, command_id):
        return self.commands.get((run_id,command_id))

    def get_replan_request(self, run_id):
        return self.pending.get(run_id)

    def save_command_rejection(self, command, request_hash, error):
        if self.fail: raise RuntimeError("Injected storage failure")
        self.commands[(command['run_id'],command['command_id'])] = {
            'request_hash':request_hash, 'response_status':409,
            'response':dict(code=error.code,message=error.message,details=error.details or [])}

    def save_transition(self, previous, transition, initial_plan, command=None, request_hash=None):
        if self.fail: raise RuntimeError("Injected storage failure")
        self.saved.extend(deepcopy(transition.events))
        self.checkpoint = encode_checkpoint(transition.state,initial_plan)
        if previous.run_id != transition.state.run_id:
            self.pending.pop(previous.run_id,None)
        if transition.replan_required:
            self.pending[transition.state.run_id] = dict(based_on_version=transition.state.state_version)
        if command:
            self.commands[(command['run_id'],command['command_id'])] = {
                'request_hash':request_hash,'response':deepcopy(transition.result)}


def prepared():
    config=json.loads((ROOT/'shared/scenarios/one_train.json').read_text(encoding='utf-8'))
    state,events,plan=prepare_scenario(config)
    repo=MemoryRepository()
    return Coordinator(repo,state,plan),repo


def cmd(owner,action,cid='command-1',**kw):
    return dict(command_id=cid,run_id=owner.state.run_id,action=action,**kw)


def test_actual_planner_engine_and_live_state_complete_one_train():
    async def scenario():
        owner,repo=prepared()
        await owner.execute(cmd(owner,'start'))
        assert response(owner.state).snapshot.trains[0].status=='moving'
        await owner.tick(600)
        assert response(owner.state).snapshot.trains[0].status=='departed'
        assert owner.state.operations['T01_03_departure']['actual_end_s']==600
        assert [e['seq'] for e in repo.saved]==list(range(2,owner.state.last_seq+1))
    asyncio.run(scenario())


def test_idempotency_reset_and_old_run_rejection():
    async def scenario():
        owner,repo=prepared()
        command=cmd(owner,'speed',speed=5)
        result=await owner.execute(command)
        n=len(repo.saved)
        assert await owner.execute(command)==result
        assert len(repo.saved)==n
        with pytest.raises(SimulationError,match='command_id'):
            await owner.execute(dict(command,speed=10))
        reset=cmd(owner,'reset',cid='reset')
        new_result=await owner.execute(reset)
        new_run=owner.state.run_id
        assert new_run!=reset['run_id'] and owner.state.paused
        assert owner.state.active_plan['run_id']==new_run
        assert await owner.execute(reset)==new_result
        assert owner.state.run_id==new_run
        with pytest.raises(SimulationError) as error:
            await owner.execute(dict(reset,command_id='different-old-command'))
        assert error.value.code=='STALE_RUN'
    asyncio.run(scenario())


def test_storage_failure_publishes_no_uncommitted_fact():
    async def scenario():
        owner,repo=prepared()
        original=response(owner.state).model_dump()
        sub=owner.broadcast.subscribe()
        await owner.start()
        repo.fail=True
        with pytest.raises(RuntimeUnavailable):
            await owner.submit(cmd(owner,'speed',speed=10))
        assert response(owner.state).model_dump()==original
        message=sub.queue.get_nowait()
        assert message['type']=='simulation_error'
        assert sub.queue.empty()
        await owner.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("pause_at",[60,120,500])
def test_checkpoint_roundtrip_preserves_heap_and_paused_clock(pause_at):
    async def scenario():
        owner,repo=prepared()
        await owner.execute(cmd(owner,'start'))
        await owner.tick(pause_at)
        owner.state,owner.initial_plan=decode_checkpoint(json.loads(json.dumps(repo.checkpoint)))
        await owner.execute(cmd(owner,'pause',cid='pause'))
        at=owner.state.sim_time_s
        await owner.tick(1000)
        assert owner.state.sim_time_s==at
        await owner.execute(cmd(owner,'start',cid='resume'))
        await owner.tick(600-pause_at)
        assert owner.state.trains['T01']['status']=='departed'
    asyncio.run(scenario())


def test_slow_subscriber_is_bounded_and_does_not_block_others():
    channel=Broadcast(capacity=1)
    slow=channel.subscribe()
    channel.publish({'type':'first'})
    fast=channel.subscribe()
    channel.publish({'type':'second'})
    assert slow.overflow.is_set()
    assert fast.queue.get_nowait()['type']=='second'
    assert slow.queue.qsize()==1


def test_http_and_websocket_share_one_state_and_reconnect_snapshot():
    owner,repo=prepared()
    with TestClient(create_app(auth_settings(),repo,owner,sessions=MemorySessionStore())) as client:
        headers=login(client,'dispatcher')
        with client.websocket_connect('/ws',headers={'Origin':ORIGIN}) as ws:
            first=ws.receive_json()
            assert first['type']=='snapshot' and first['ws_seq']==1
            body=cmd(owner,'speed',speed=10)
            reply=client.post('/api/simulation/control',json=body,headers=headers)
            assert reply.status_code==200
            updated=ws.receive_json()
            assert updated['type']=='state_updated' and updated['ws_seq']==2
            assert updated['payload']['snapshot']['speed']==10
            assert client.get('/api/state').json()['snapshot']['state_version']==updated['state_version']
            assert client.post('/api/simulation/control',json=body,headers=headers).json()==reply.json()
        with client.websocket_connect('/ws',headers={'Origin':ORIGIN}) as ws:
            assert ws.receive_json()['payload']['snapshot']['speed']==10


def test_bad_origin_and_bad_command_rejected():
    owner,repo=prepared()
    with TestClient(create_app(auth_settings(),repo,owner,sessions=MemorySessionStore())) as client:
        headers=login(client,'dispatcher')
        assert client.post('/api/simulation/control',json=cmd(owner,'pause'),headers=dict(headers,Origin='https://bad.example')).status_code==403
        assert client.post('/api/simulation/control',json={'action':'start'},headers=headers).status_code==422


def test_clock_sync_runs_in_pause():
    owner,repo=prepared()
    with TestClient(create_app(auth_settings(),repo,owner,sessions=MemorySessionStore())) as client:
        login(client,'viewer')
        with client.websocket_connect('/ws',headers={'Origin':ORIGIN}) as ws:
            initial=ws.receive_json()
            clock=ws.receive_json()
            assert clock['type']=='clock_sync' and clock['payload']['paused']
            assert clock['state_version']==initial['state_version']


def test_concurrent_identical_commands_change_state_once():
    async def scenario():
        owner,repo=prepared()
        await owner.start()
        command=cmd(owner,'speed',speed=5)
        replies=await asyncio.gather(*(owner.submit(command) for _ in range(5)))
        assert all(reply==replies[0] for reply in replies)
        assert len(repo.saved)==1
        await owner.stop()
    asyncio.run(scenario())
