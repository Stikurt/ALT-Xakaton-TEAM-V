import asyncio
from copy import deepcopy
import time
from uuid import uuid4

import pytest

from app.main import create_app
from app.runtime.coordinator import Coordinator, RuntimeUnavailable
from app.runtime.planning import PlannerProcess, calculate_variants
from app.runtime.state import encode_checkpoint
from auth_support import signed_in_client
from app.simulation.engine import SimulationError
from test_runtime import MemoryRepository, prepared, cmd


class PlanningMemory(MemoryRepository):
    def __init__(self):
        super().__init__()
        self.jobs={}
        self.plans={}

    def queue(self,state):
        prior=self.pending.get(state.run_id)
        if prior: self.jobs[prior['job_id']]['status']='superseded'
        job=dict(job_id=str(uuid4()),run_id=state.run_id,based_on_version=state.state_version,
                 based_on_time_s=state.sim_time_s,status='queued')
        self.jobs[job['job_id']]=job
        self.pending[state.run_id]=job
        return job

    def save_transition(self,previous,transition,initial_plan,command=None,request_hash=None):
        # Keep this double deliberately small; transaction assertions use PostgreSQL.
        pending=deepcopy(self.pending)
        super().save_transition(previous,transition,initial_plan,command,request_hash)
        self.pending=pending
        if transition.replan_required: self.queue(transition.state)
        if command and command['action'] in ('reset','apply_plan'):
            self.pending.pop(previous.run_id,None)
            for job in self.jobs.values():
                if job['run_id']==previous.run_id and job['status'] in ('queued','running'):
                    job['status']='superseded'

    def save_replan_command(self,state,command,request_hash):
        if self.fail: raise RuntimeError('Injected failure')
        job=self.queue(state)
        reply=dict(job_id=job['job_id'],run_id=state.run_id,command_id=command['command_id'])
        self.commands[(state.run_id,command['command_id'])]=dict(request_hash=request_hash,response=reply,response_status=202)
        return deepcopy(reply)

    def recover_planning(self,state): pass

    def claim_replan(self,state):
        if self.fail: raise RuntimeError('Injected failure')
        job=self.pending.pop(state.run_id,None)
        if job:
            job.update(status='running',based_on_version=state.state_version,based_on_time_s=state.sim_time_s)
            return deepcopy(job)

    def finish_replan(self,job,result):
        if self.fail: raise RuntimeError('Injected failure')
        saved=self.jobs[job['job_id']]
        if saved['status']!='running': return False
        for plan in result.get('plans',[]): self.plans[plan['id']]=deepcopy(plan)
        saved.update({k:v for k,v in result.items() if k!='plans'})
        saved.update(status='failed' if result.get('error') else 'completed',plan_ids=[p['id'] for p in result.get('plans',[])])
        return True

    def get_job(self,job_id): return deepcopy(self.jobs.get(job_id))
    def get_plan(self,plan_id): return deepcopy(self.plans.get(plan_id))


def planning_owner(**kw):
    initial,_=prepared()
    repo=PlanningMemory()
    return Coordinator(repo,initial.state,initial.initial_plan,**kw),repo


def add_plan(owner,repo):
    plan=calculate_variants(encode_checkpoint(owner.state,owner.initial_plan),2)[0]
    repo.plans[plan['id']]=plan
    return plan


def apply(owner,plan,cid='apply'):
    return cmd(owner,'apply_plan',cid=cid,plan_id=plan['id'],expected_state_version=owner.state.state_version)


@pytest.mark.parametrize('at_s',[0,60,120,300,500,600])
def test_real_variants_preserve_running_and_completed_operations(at_s):
    async def run():
        owner,repo=planning_owner()
        await owner.execute(cmd(owner,'start'))
        await owner.tick(at_s)
        before=deepcopy(owner.state)
        candidates=calculate_variants(encode_checkpoint(owner.state,owner.initial_plan),2)
        assert owner.state==before
        assert all(p['status']=='feasible' and not p['violations'] for p in candidates)
        repo.plans[candidates[0]['id']]=candidates[0]
        await owner.execute(apply(owner,candidates[0]))
        for oid,op in before.operations.items():
            if op['status']!='pending': assert owner.state.operations[oid]==op
        assert owner.state.running==before.running
        await owner.tick(600-at_s)
        assert owner.state.trains['T01']['status']=='departed'
    asyncio.run(run())


def test_only_one_client_can_apply_same_version_and_retry_is_durable():
    async def run():
        owner,repo=planning_owner()
        candidate=add_plan(owner,repo)
        first,second=apply(owner,candidate,'first'),apply(owner,candidate,'second')
        await owner.start()
        try:
            replies=await asyncio.gather(owner.submit(first),owner.submit(second),return_exceptions=True)
            assert isinstance(replies[1],SimulationError) and replies[1].code=='STALE_PLAN'
            assert await owner.submit(first)==replies[0]
            assert len([e for e in repo.saved if e['type']=='plan_applied'])==1
            restored=Coordinator(repo,owner.state,owner.initial_plan)
            assert await restored.execute(first)==replies[0]
        finally: await owner.stop()
    asyncio.run(run())


def test_plan_revalidation_checks_time_even_without_version_change():
    async def run():
        owner,repo=planning_owner()
        await owner.execute(cmd(owner,'incident',incident=dict(kind='delay_train',target_id='T01',delay_s=120)))
        candidate=add_plan(owner,repo)
        # A captured plan must be rejected if its start has passed, independently of version.
        owner.state.sim_time_s=121
        with pytest.raises(SimulationError) as error: await owner.execute(apply(owner,candidate))
        assert error.value.code=='STALE_PLAN'
    asyncio.run(run())


def hanging_worker(checkpoint,budget,connection):
    time.sleep(30)


def crashing_worker(checkpoint,budget,connection):
    connection.close()


async def collect(process):
    for _ in range(200):
        result=await process.poll()
        if result: return result
        await asyncio.sleep(.02)
    raise AssertionError('Worker did not finish')


def test_hard_timeout_reaps_process_and_next_worker_succeeds():
    async def run():
        owner,_=prepared()
        checkpoint=encode_checkpoint(owner.state,owner.initial_plan)
        process=PlannerProcess(timeout_s=.15,worker_target=hanging_worker)
        process.start(dict(job_id='hang'),checkpoint)
        child=process.process
        _,result=await collect(process)
        assert result['error']['code']=='PLANNER_TIMEOUT' and process.process is None
        assert child._closed
        normal=PlannerProcess(timeout_s=5)
        normal.start(dict(job_id='normal'),checkpoint)
        _,result=await collect(normal)
        assert len(result['plans'])==2 and all(p['status']=='feasible' for p in result['plans'])
    asyncio.run(run())


def test_worker_crash_has_public_error_without_traceback():
    async def run():
        owner,_=prepared()
        process=PlannerProcess(worker_target=crashing_worker)
        process.start(dict(job_id='crash'),encode_checkpoint(owner.state,owner.initial_plan))
        _,result=await collect(process)
        assert result['error']['code']=='PLANNER_ERROR' and 'traceback' not in str(result).lower()
    asyncio.run(run())


def test_new_requests_coalesce_and_reset_stops_child():
    async def run():
        owner,repo=planning_owner(planner=PlannerProcess(timeout_s=5,worker_target=hanging_worker))
        first=await owner.execute(cmd(owner,'replan',cid='one'))
        await owner.poll_planner()
        child=owner.planner.process
        second=await owner.execute(cmd(owner,'replan',cid='two'))
        third=await owner.execute(cmd(owner,'replan',cid='three'))
        assert repo.jobs[first['job_id']]['status']=='running'
        assert repo.jobs[second['job_id']]['status']=='superseded'
        assert repo.jobs[third['job_id']]['status']=='queued' and len(repo.pending)==1
        await owner.execute(cmd(owner,'reset',cid='reset'))
        assert owner.planner.process is None and child._closed and not repo.pending
    asyncio.run(run())


def test_http_auto_replan_result_and_apply():
    owner,repo=planning_owner(planner=PlannerProcess())
    with signed_in_client(repo,owner) as client:
        body=dict(command_id='delay',run_id=owner.state.run_id,kind='delay_train',target_id='T01',delay_s=120)
        assert client.post('/api/incidents',json=body).status_code==200
        deadline=time.monotonic()+6
        while not repo.jobs or not any(j['status']=='completed' for j in repo.jobs.values()):
            assert time.monotonic()<deadline
            assert client.get('/api/state').status_code==200
            time.sleep(.04)
        job=next(j for j in repo.jobs.values() if j['status']=='completed')
        wire=client.get('/api/replans/'+job['job_id']).json()
        assert wire['identical'] and len(wire['plan_ids'])==2
        plan_id=wire['plan_ids'][0]
        candidate=client.get('/api/plans/'+plan_id).json()
        assert candidate['applicable'] and candidate['plan']['changes']
        reply=client.post('/api/plans/'+plan_id+'/apply',json=dict(command_id='apply',run_id=owner.state.run_id,expected_state_version=owner.state.state_version))
        assert reply.status_code==200 and not reply.json()['replan_required']
        assert not client.get('/api/state').json()['snapshot']['replan_required']


def test_failed_apply_does_not_publish_or_change_plan():
    async def run():
        owner,repo=planning_owner()
        candidate=add_plan(owner,repo)
        before=deepcopy(owner.state)
        await owner.start()
        try:
            repo.fail=True
            with pytest.raises(RuntimeUnavailable): await owner.submit(apply(owner,candidate))
            assert owner.state==before and not repo.saved
        finally: await owner.stop()
    asyncio.run(run())


def test_clock_and_api_keep_working_while_planner_hangs():
    owner,repo=planning_owner(planner=PlannerProcess(timeout_s=1.5,worker_target=hanging_worker))
    with signed_in_client(repo,owner) as client:
        with client.websocket_connect('/ws') as ws:
            assert ws.receive_json()['type']=='snapshot'
            reply=client.post('/api/replans',json=dict(command_id='hang',run_id=owner.state.run_id))
            assert reply.status_code==202
            assert client.get('/api/state').status_code==200
            messages=[]
            while 'replan_failed' not in messages:
                message=ws.receive_json()
                messages.append(message['type'])
            assert 'clock_sync' in messages and 'replan_started' in messages
            assert message['payload']['code']=='PLANNER_TIMEOUT'
            assert repo.get_job(reply.json()['job_id'])['status']=='failed'
            assert owner.planner.process is None


@pytest.mark.parametrize('status',['partial','infeasible','timeout'])
def test_nonfeasible_plan_cannot_be_applied(status):
    async def run():
        owner,repo=planning_owner()
        candidate=add_plan(owner,repo)
        repo.plans[candidate['id']]['status']=status
        with pytest.raises(SimulationError) as error: await owner.execute(apply(owner,candidate))
        assert error.value.code=='INVALID_PLAN' and not repo.saved
    asyncio.run(run())
