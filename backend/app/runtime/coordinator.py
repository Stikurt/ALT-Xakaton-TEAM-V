import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import time

from app.runtime.broadcast import Broadcast
from app.runtime.state import response, restore_initial_plan, encode_checkpoint
from app.runtime.planning import candidate_problem
from app.planner import RULES
from app.simulation import advance_to, apply_command, apply_plan
from app.simulation.engine import Transition, SimulationError

logger = logging.getLogger(__name__)


class RuntimeUnavailable(Exception):
    pass


class Coordinator:
    """One actor applies commands/ticks. State becomes visible only after SQL commit."""
    def __init__(self, repository, state, initial_plan, *, tick_s=.1, queue_size=64, replan_required=False, planner=None):
        self.repository, self.state = repository, state
        self.initial_plan = initial_plan
        self.replan_required = replan_required
        self.planner = planner
        self.tick_s = tick_s
        self.commands = asyncio.Queue(maxsize=queue_size)
        self.broadcast = Broadcast()
        self.fault = None
        self.task = None
        self.stopping = False
        self._monotonic = time.monotonic()
        self._clock_sent = self._monotonic

    def get_state(self):
        result = response(self.state)
        result.snapshot.replan_required = self.replan_required
        if self.fault:
            result.snapshot.paused = True
        return result

    def message(self, kind, payload):
        return dict(schema_version=1, run_id=self.state.run_id,
                    state_version=self.state.state_version, type=kind, payload=payload)

    async def start(self):
        if self.planner:
            await asyncio.to_thread(self.repository.recover_planning,self.state)
        # Recovery is explicit: continue from the checkpoint in pause after restart.
        if not self.state.paused:
            pause = {"command_id": "recovery-" + datetime.now(timezone.utc).isoformat(),
                     "run_id": self.state.run_id, "action": "pause"}
            transition = apply_command(self.state, pause, rules=RULES)
            await self._commit(transition)
        self._monotonic = time.monotonic()
        self.task = asyncio.create_task(self._run(), name="station-owner")

    async def stop(self):
        self.stopping = True
        if self.task:
            # Finish an in-flight database transaction before closing its pool.
            await self.task
        if self.planner: await self.planner.close()
        while not self.commands.empty():
            _, future = self.commands.get_nowait()
            if not future.done():
                future.set_exception(RuntimeUnavailable("Server is stopping"))

    async def submit(self, command):
        if self.fault or self.stopping:
            raise RuntimeUnavailable(self.fault)
        future = asyncio.get_running_loop().create_future()
        try:
            self.commands.put_nowait((command, future))
        except asyncio.QueueFull:
            raise RuntimeUnavailable("Command queue is full") from None
        # A disconnected requester cannot cancel a command that has already been queued.
        return await asyncio.shield(future)

    async def _commit(self, transition, command=None, request_hash=None):
        await asyncio.to_thread(self.repository.save_transition, self.state, transition,
                                self.initial_plan, command, request_hash)
        self.replan_required = (self.replan_required if self.state.run_id == transition.state.run_id else False) or transition.replan_required
        if command and command['action']=='apply_plan': self.replan_required=False
        self.state = transition.state
        if command and command['action'] in ('reset','apply_plan') and self.planner:
            await self.planner.close()
        if transition.events:
            self.broadcast.publish(self.message("state_updated", {
                "snapshot": self.get_state().snapshot.model_dump(mode="json")}))

    async def tick(self, elapsed_s):
        if self.fault or self.state.paused:
            return
        total=self.state.fractional_s+elapsed_s*self.state.speed
        whole=math.floor(total+1e-9)
        target=self.state.sim_time_s+whole
        # Never collapse events from different model instants into one history projection.
        # Persist every minute even when a delayed host tick spans many model minutes.
        while True:
            now=self.state.sim_time_s
            boundary=min(target,(now//60+1)*60)
            if self.state.queue and self.state.queue[0][0]<=boundary:
                boundary=max(now,self.state.queue[0][0])
            transition=advance_to(self.state,boundary,rules=RULES)
            transition.state.fractional_s=max(0.,total-whole) if boundary==target else 0.
            await self._commit(transition)
            if boundary==target: break

    async def execute(self, command):
        """Only called by the actor (or directly by deterministic unit tests)."""
        if self.fault:
            raise RuntimeUnavailable(self.fault)
        fingerprint = hashlib.sha256(json.dumps(command,sort_keys=True,separators=(",",":"))
                                     .encode()).hexdigest()
        cached = await asyncio.to_thread(self.repository.command_result,
                                        command["run_id"], command["command_id"])
        if cached:
            if cached["request_hash"] != fingerprint:
                raise SimulationError("COMMAND_ID_REUSED", "command_id уже использован с другим запросом")
            if cached.get("response_status",200) >= 400:
                error = cached["response"]
                raise SimulationError(error["code"],error["message"],error["details"])
            return cached["response"]
        try:
            if command['action'] in ('replan','apply_plan'):
                if command['run_id']!=self.state.run_id:
                    raise SimulationError('STALE_RUN','Команда другого запуска')
                if command['action']=='replan':
                    reply=await asyncio.to_thread(self.repository.save_replan_command,self.state,command,fingerprint)
                    self.replan_required=True
                    self.broadcast.publish(self.message('state_updated',{'snapshot':self.get_state().snapshot.model_dump(mode='json')}))
                    return reply
                candidate=await asyncio.to_thread(self.repository.get_plan,command['plan_id'])
                if candidate is None: raise SimulationError('PLAN_NOT_FOUND','План не найден')
                problem=candidate_problem(self.state,candidate,command['expected_state_version'])
                if problem: raise problem
                transition=apply_plan(self.state,candidate,rules=RULES)
                transition.result=dict(command_id=command['command_id'],run_id=self.state.run_id,
                                       state_version=transition.state.state_version)
                key=json.dumps([command['run_id'],command['command_id']])
                transition.state.commands[key]=dict(fingerprint=json.dumps(command,sort_keys=True,ensure_ascii=False),result={})
            else:
                transition = apply_command(self.state, command, rules=RULES)
        except SimulationError as exc:
            # Cache domain refusals for known runs too: conditions may change on retry.
            # Structural HTTP 422 responses and unknown run IDs are not persisted.
            await asyncio.to_thread(self.repository.save_command_rejection,command,fingerprint,exc)
            logger.info("command_rejected run_id=%s command_id=%s code=%s",command['run_id'],command['command_id'],exc.code)
            raise
        if command["action"] == "reset":
            planned = restore_initial_plan(transition.state, self.initial_plan)
            result = dict(transition.result, state_version=planned.state.state_version)
            # Update the engine cache too; PostgreSQL remains the durable authority.
            key = json.dumps([command["run_id"],command["command_id"]])
            planned.state.commands[key]["result"] = deepcopy(result)
            transition = Transition(planned.state, transition.events + planned.events, result)
        transition.result["replan_required"] = (
            (self.replan_required if self.state.run_id == transition.state.run_id else False)
            or transition.replan_required
        )
        if command['action']=='apply_plan': transition.result['replan_required']=False
        key = json.dumps([command["run_id"],command["command_id"]])
        transition.state.commands[key]["result"] = deepcopy(transition.result)
        await self._commit(transition, command, fingerprint)
        return transition.result

    async def poll_planner(self):
        if self.fault or not self.planner: return
        completed=await self.planner.poll()
        if completed:
            job,result=completed
            plans=result.get('plans',[])
            result['stale']=job['run_id']!=self.state.run_id or job['based_on_version']!=self.state.state_version or any(
                (problem:=candidate_problem(self.state,p)) is not None and problem.code=='STALE_PLAN' for p in plans)
            result['identical']=len(plans)==2 and plans[0]['assignments']==plans[1]['assignments']
            saved=await asyncio.to_thread(self.repository.finish_replan,job,result)
            if saved and job['run_id']==self.state.run_id:
                payload=dict(job_id=job['job_id'],based_on_version=job['based_on_version'])
                if result.get('error'):
                    payload.update(result['error'])
                    self.broadcast.publish(self.message('replan_failed',payload))
                else:
                    payload.update(plan_ids=[p['id'] for p in plans],stale=result['stale'],identical=result['identical'])
                    self.broadcast.publish(self.message('replan_finished',payload))
        if self.planner.process is None:
            job=await asyncio.to_thread(self.repository.claim_replan,self.state)
            if job:
                try:
                    self.planner.start(job,encode_checkpoint(self.state,self.initial_plan))
                except Exception:
                    error=dict(code='PLANNER_ERROR',message='Не удалось запустить процесс расчёта.')
                    await asyncio.to_thread(self.repository.finish_replan,job,dict(error=error))
                    self.broadcast.publish(self.message('replan_failed',dict(job_id=job['job_id'],**error)))
                else:
                    self.broadcast.publish(self.message('replan_started',dict(job_id=job['job_id'],based_on_version=job['based_on_version'])))

    def _fail(self, exc, command=None):
        self.fault = "Ошибка сохранения или исполнения; симуляция остановлена. Перезапустите backend."
        logger.error("simulation_error run_id=%s command_id=%s error_type=%s", self.state.run_id,
                     (command or {}).get("command_id"), type(exc).__name__)
        self.broadcast.publish(self.message("simulation_error", {
            "code":"SIMULATION_ERROR", "message":self.fault}))

    async def _run(self):
        while not self.stopping:
            item = None
            try:
                try:
                    item = await asyncio.wait_for(self.commands.get(), timeout=self.tick_s)
                except asyncio.TimeoutError:
                    pass
                now = time.monotonic()
                elapsed = now - self._monotonic
                self._monotonic = now
                # Advance using the OLD speed/pause state, then handle the command.
                await self.tick(elapsed)
                if item:
                    command, future = item
                    try:
                        result = await self.execute(command)
                    except (SimulationError, RuntimeUnavailable) as exc:
                        if not future.done(): future.set_exception(exc)
                    else:
                        if not future.done(): future.set_result(result)
                await self.poll_planner()
                if now - self._clock_sent >= 1:
                    self._clock_sent = now
                    self.broadcast.publish(self.message("clock_sync", {
                        "sim_time_s":self.state.sim_time_s, "speed":self.state.speed,
                        "paused": self.state.paused or bool(self.fault)}))
            except asyncio.CancelledError:
                if item and not item[1].done():
                    item[1].set_exception(RuntimeUnavailable("Server is stopping"))
                raise
            except Exception as exc:
                self._fail(exc, item[0] if item else None)
                if self.planner: await self.planner.close()
                if item and not item[1].done():
                    item[1].set_exception(RuntimeUnavailable(self.fault))
