import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import time

from app.runtime.broadcast import Broadcast
from app.runtime.state import response, restore_initial_plan
from app.planner import RULES
from app.simulation import advance_elapsed, apply_command
from app.simulation.engine import Transition, SimulationError

logger = logging.getLogger(__name__)


class RuntimeUnavailable(Exception):
    pass


class Coordinator:
    """One actor applies commands/ticks. State becomes visible only after SQL commit."""
    def __init__(self, repository, state, initial_plan, *, tick_s=.1, queue_size=64, replan_required=False):
        self.repository, self.state = repository, state
        self.initial_plan = initial_plan
        self.replan_required = replan_required
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
        self.state = transition.state
        if transition.events:
            self.broadcast.publish(self.message("state_updated", {
                "snapshot": self.get_state().snapshot.model_dump(mode="json")}))

    async def tick(self, elapsed_s):
        if self.fault or self.state.paused:
            return
        transition = advance_elapsed(self.state, elapsed_s, rules=RULES)
        await self._commit(transition)

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
        key = json.dumps([command["run_id"],command["command_id"]])
        transition.state.commands[key]["result"] = deepcopy(transition.result)
        await self._commit(transition, command, fingerprint)
        return transition.result

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
                if item and not item[1].done():
                    item[1].set_exception(RuntimeUnavailable(self.fault))
