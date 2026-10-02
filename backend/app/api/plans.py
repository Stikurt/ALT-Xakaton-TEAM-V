import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.live import DISPATCHER, runtime
from app.auth import require_role
from app.domain.models import ApplyPlanCommand, CommandResult, PlanResponse, ReplanAccepted, ReplanCommand, ReplanJob
from app.planner import RULES
from app.runtime.planning import candidate_problem
from app.simulation.engine import rules_context

router=APIRouter()
VIEWER=[Depends(require_role('viewer'))]


def _plans(repository,plan_ids):
    """One query for all variants of the listed jobs (no N+1)."""
    batch=getattr(repository,'get_plans',None)
    if batch is not None: return batch(plan_ids)
    return {pid:repository.get_plan(pid) for pid in plan_ids}


def _mark_stale(state,job,plans):
    job['stale']=job['run_id']!=state.run_id or job['based_on_version']!=state.state_version
    for plan_id in job.get('plan_ids',[]):
        candidate=plans.get(plan_id)
        if candidate is None: continue
        problem=candidate_problem(state,candidate)
        job['stale'] |= problem is not None and problem.code=='STALE_PLAN'


@router.post('/api/replans',response_model=ReplanAccepted,status_code=202,dependencies=DISPATCHER)
async def request_replan(command: ReplanCommand,request: Request):
    return await runtime(request).submit(dict(action='replan',**command.model_dump()))


@router.get('/api/replans/{job_id}',response_model=ReplanJob,dependencies=VIEWER)
async def get_job(job_id: str,request: Request):
    owner=runtime(request)
    job=await asyncio.to_thread(owner.repository.get_job,job_id)
    if not job: raise HTTPException(404,'Расчёт не найден')
    plans=await asyncio.to_thread(_plans,owner.repository,job.get('plan_ids',[]))
    _mark_stale(owner.state,job,plans)
    return job


@router.get('/api/replans',response_model=list[ReplanJob],dependencies=VIEWER)
async def list_jobs(request: Request,run_id: str=Query(min_length=1,max_length=128),limit: int=Query(default=20,ge=1,le=50)):
    owner=runtime(request)
    jobs=await asyncio.to_thread(owner.repository.list_jobs,run_id,limit)
    plans=await asyncio.to_thread(_plans,owner.repository,[p for job in jobs for p in job.get('plan_ids',[])])
    for job in jobs:
        _mark_stale(owner.state,job,plans)
    return jobs


@router.get('/api/plans/{plan_id}',response_model=PlanResponse,dependencies=VIEWER)
async def get_plan(plan_id: str,request: Request):
    owner=runtime(request)
    candidate=await asyncio.to_thread(owner.repository.get_plan,plan_id)
    if not candidate: raise HTTPException(404,'План не найден')
    problem=candidate_problem(owner.state,candidate)
    return dict(plan=candidate,stale=bool(problem and problem.code=='STALE_PLAN'),
                applicable=problem is None and not RULES.validate_plan(rules_context(owner.state),candidate))


@router.post('/api/plans/{plan_id}/apply',response_model=CommandResult,dependencies=DISPATCHER)
async def accept_plan(plan_id: str,command: ApplyPlanCommand,request: Request):
    return await runtime(request).submit(dict(action='apply_plan',plan_id=plan_id,**command.model_dump()))
