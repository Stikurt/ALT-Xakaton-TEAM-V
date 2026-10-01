"""Integration boundaries aligned with feat/simulation at d4d2b62.

The engine's private State includes queues/reservations and cannot be replaced
by the public Snapshot. The backend validates only the snapshot projection.
"""
from typing import Any, Protocol

from app.domain.models import Assignment, Conflict, Plan, Snapshot

Json = dict[str, Any]


class RulesPort(Protocol):
    """Adapter for Н's rules, injected into the existing engine."""
    def can_start(self, context: Json, operation: Json, assignment: Json) -> list[Json]: ...
    def validate_plan(self, context: Json, plan: Json) -> list[Json]: ...


class EngineTransition(Protocol):
    state: Any
    events: list[Json]
    result: Json
    replan_required: bool


class SimulationPort(Protocol):
    """В's existing functions; private State intentionally opaque.

    Engine proposes run_id/state_version/seq/event_id. Backend saves those exact
    values atomically before installing transition.state. ws_seq belongs to backend.
    """
    def create_initial_state(self, config: Json, *, run_id: str | None = None) -> Any: ...
    def advance_to(self, state: Any, target_s: int, *, rules: RulesPort) -> EngineTransition: ...
    def advance_elapsed(self, state: Any, elapsed_s: float, *, rules: RulesPort) -> EngineTransition: ...
    def apply_command(self, state: Any, command: Json, *, rules: RulesPort) -> EngineTransition: ...
    def apply_plan(self, state: Any, plan: Json, *, rules: RulesPort) -> EngineTransition: ...
    def snapshot(self, state: Any) -> Json: ...
    def rules_context(self, state: Any) -> Json: ...


class PlannerPort(Protocol):
    """Proposed planner facade. The RulesPort adapter is implemented in stage 2."""
    def can_start(self, state: Snapshot, assignment: Assignment) -> list[Conflict]: ...
    def validate_plan(self, state: Snapshot, plan: Plan) -> list[Conflict]: ...
    def plan(self, state: Snapshot, config: dict, strategy: str, budget_s: float) -> Plan: ...
