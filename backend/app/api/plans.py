import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from app.api.live import check_origin, runtime
from app.domain.models import ApplyPlanCommand, CommandResult, PlanResponse, ReplanAccepted, ReplanCommand, ReplanJob
from app.planner import RULES
from app.runtime.planning import candidate_problem
from app.simulation.engine import rules_context

router=APIRouter()


@router.post('/api/replans',response_model=ReplanAccepted,status_code=202)
async def request_replan(command: ReplanCommand,request: Request):
    check_origin(request)
    return await runtime(request).submit(dict(action='replan',**command.model_dump()))


@router.get('/api/replans/{job_id}',response_model=ReplanJob)
async def get_job(job_id: str,request: Request):
    owner=runtime(request)
    job=await asyncio.to_thread(owner.repository.get_job,job_id)
    if not job: raise HTTPException(404,'Расчёт не найден')
    job['stale']=job['run_id']!=owner.state.run_id or job['based_on_version']!=owner.state.state_version
    for plan_id in job.get('plan_ids',[]):
        candidate=await asyncio.to_thread(owner.repository.get_plan,plan_id)
        problem=candidate_problem(owner.state,candidate)
        job['stale'] |= problem is not None and problem.code=='STALE_PLAN'
    return job


@router.get('/api/replans',response_model=list[ReplanJob])
async def list_jobs(request: Request,run_id: str=Query(min_length=1,max_length=128),limit: int=Query(default=20,ge=1,le=50)):
    owner=runtime(request)
    jobs=await asyncio.to_thread(owner.repository.list_jobs,run_id,limit)
    for job in jobs:
        job['stale']=job['run_id']!=owner.state.run_id or job['based_on_version']!=owner.state.state_version
        for plan_id in job.get('plan_ids',[]):
            candidate=await asyncio.to_thread(owner.repository.get_plan,plan_id)
            problem=candidate_problem(owner.state,candidate)
            job['stale'] |= problem is not None and problem.code=='STALE_PLAN'
    return jobs


@router.get('/api/plans/{plan_id}',response_model=PlanResponse)
async def get_plan(plan_id: str,request: Request):
    owner=runtime(request)
    candidate=await asyncio.to_thread(owner.repository.get_plan,plan_id)
    if not candidate: raise HTTPException(404,'План не найден')
    problem=candidate_problem(owner.state,candidate)
    return dict(plan=candidate,stale=bool(problem and problem.code=='STALE_PLAN'),
                applicable=problem is None and not RULES.validate_plan(rules_context(owner.state),candidate))


@router.post('/api/plans/{plan_id}/apply',response_model=CommandResult)
async def accept_plan(plan_id: str,command: ApplyPlanCommand,request: Request):
    check_origin(request)
    return await runtime(request).submit(dict(action='apply_plan',plan_id=plan_id,**command.model_dump()))
