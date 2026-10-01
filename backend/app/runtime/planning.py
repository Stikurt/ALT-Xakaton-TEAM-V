"""Immutable planner input and a disposable spawn worker with a hard deadline."""
import asyncio
from copy import deepcopy
import multiprocessing
import time

from app.domain.models import Plan
from app.planner import RULES, plan
from app.runtime.state import decode_checkpoint
from app.simulation.engine import rules_context, SimulationError


def calculate_variants(checkpoint, budget_s):
    state,_ = decode_checkpoint(checkpoint)
    context = rules_context(state)
    snapshot = deepcopy(context['snapshot'])
    for key in ('running_assignments','reservations','busy_zones'):
        snapshot[key] = context[key]
    variants = []
    for strategy in ('passenger_first','earliest_departure'):
        raw = plan(deepcopy(snapshot),deepcopy(state.config),strategy,budget_s)
        # Normalize the participant's train-level failures to the public operation contract.
        assigned = {a['operation_id'] for a in raw['assignments']}
        reasons = {x['train_id']:x['reason'] for x in raw['unassigned']}
        missing = [dict(operation_id=o['id'],code='NO_FEASIBLE_SLOT',
                        message=reasons.get(o['train_id'],'Операция не размещена'))
                   for o in state.operations.values() if o['status']=='pending' and o['id'] not in assigned]
        violations = RULES.validate_plan(context,raw,require_complete=False)
        changes = []
        future = {a['operation_id']:a for a in raw['assignments']}
        for oid,o in sorted(state.operations.items()):
            if o['status']=='pending' and state.assignments.get(oid)!=future.get(oid):
                changes.append(dict(operation_id=oid,before=state.assignments.get(oid),after=future.get(oid)))
        explanations = [f"{item['operation_id']}: {item['message']}" for item in missing]
        if changes:
            explanations.append(f"Изменены будущие назначения: {len(changes)}.")
        raw.update(unassigned=missing,explanations=explanations,violations=violations,
                   based_on_time_s=state.sim_time_s,changes=changes)
        if violations:
            raw['status']='infeasible'
        elif missing:
            raw['status']='partial'
        else:
            raw['status']='feasible'
        raw['metrics']['changed_future_assignments']=len(changes)
        variants.append(Plan.model_validate(raw).model_dump(mode='json'))
    return variants


def _worker(checkpoint,budget_s,connection):
    try:
        connection.send(dict(plans=calculate_variants(checkpoint,budget_s)))
    except Exception:
        # No traceback, configuration or secrets cross the API boundary.
        connection.send(dict(error=dict(code='PLANNER_ERROR',message='Ошибка расчёта плана.')))
    finally:
        connection.close()


def candidate_problem(state,candidate,expected_version=None):
    if candidate['run_id']!=state.run_id or candidate['based_on_version']!=state.state_version:
        return SimulationError('STALE_PLAN','План другого запуска или устаревшей версии')
    if expected_version is not None and expected_version!=state.state_version:
        return SimulationError('STALE_PLAN','Текущая версия изменилась')
    if any(state.operations.get(a['operation_id'],{}).get('status')=='pending'
           and a['start_s']<state.sim_time_s for a in candidate['assignments']):
        return SimulationError('STALE_PLAN','Назначение начинается в прошлом')
    if candidate['status']!='feasible' or candidate['unassigned'] or candidate.get('violations'):
        return SimulationError('INVALID_PLAN','Принимается только полный допустимый план')
    return None


class PlannerProcess:
    """One child, no executor backlog. Results are consumed only by the actor."""
    def __init__(self,timeout_s=5,budget_s=2,worker_target=_worker):
        self.timeout_s,self.budget_s=timeout_s,budget_s
        self.worker_target=worker_target
        self.job=None
        self.process=None
        self.connection=None

    def start(self,job,checkpoint):
        if self.process is not None:
            raise RuntimeError('Planner is already running')
        context=multiprocessing.get_context('spawn')
        self.connection,writer=context.Pipe(duplex=False)
        self.process=context.Process(target=self.worker_target,args=(checkpoint,self.budget_s,writer),daemon=True)
        self.job=job
        self.started=time.monotonic()
        try:
            self.process.start()
        except Exception:
            self.connection.close()
            self.process=None
            self.job=None
            raise
        finally:
            writer.close()

    async def close(self):
        process,self.process=self.process,None
        if process:
            if process.is_alive(): process.terminate()
            await asyncio.to_thread(process.join,1)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join,1)
            if process.is_alive():
                raise RuntimeError('Planner process could not be stopped')
            process.close()
        if self.connection:
            self.connection.close()
            self.connection=None
        self.job=None

    async def poll(self):
        if not self.process:
            return None
        result=None
        try:
            remaining=self.timeout_s-(time.monotonic()-self.started)
            if remaining<=0:
                raise asyncio.TimeoutError
            if self.connection.poll():
                result=await asyncio.wait_for(asyncio.to_thread(self.connection.recv),timeout=remaining)
            elif not self.process.is_alive():
                raise EOFError
        except asyncio.TimeoutError:
            result=dict(error=dict(code='PLANNER_TIMEOUT',message='Превышено время расчёта.'))
        except (EOFError,OSError):
            result=dict(error=dict(code='PLANNER_ERROR',message='Процесс расчёта завершился без результата.'))
        if result is not None:
            job=self.job
            await self.close()
            return job,result
        return None
