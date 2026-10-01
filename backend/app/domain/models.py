"""Shared wire contracts, schema_version=1. Railway rules belong to the planner."""
from datetime import datetime
from uuid import uuid4
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")]
Seconds = Annotated[int, Field(ge=0, strict=True)]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Point = tuple[float, float]


class Track(Contract):
    id: Id
    kind: Literal["passenger", "freight", "storage", "cargo", "locomotive"]
    usable_length_m: PositiveInt
    geometry: Annotated[list[Point], Field(min_length=2)]
    availability: Literal["open", "closed"] = "open"
    closed_until_s: Seconds | None = None
    occupant_train_id: Id | None = None


class Route(Contract):
    id: Id
    from_id: Id
    to_id: Id
    conflict_zone_ids: list[Id]
    duration_s: PositiveInt
    polyline: Annotated[list[Point], Field(min_length=2)]


class Movement(Contract):
    route_id: Id
    started_at_s: Seconds
    expected_end_at_s: Seconds

    @model_validator(mode="after")
    def valid_interval(self):
        if self.expected_end_at_s <= self.started_at_s:
            raise ValueError("Movement end must be after start")
        return self


class Train(Contract):
    id: Id
    kind: Literal["passenger", "transit", "local"]
    length_m: PositiveInt
    priority: Annotated[int, Field(ge=1, le=3, strict=True)]
    scheduled_arrival_s: Seconds
    expected_arrival_s: Seconds
    scheduled_departure_s: Seconds
    status: Literal["scheduled", "waiting_entry", "moving", "on_track", "departed"]
    track_id: Id | None = None
    movement: Movement | None = None

    @model_validator(mode="after")
    def valid_position(self):
        if (self.status == "moving") != (self.movement is not None):
            raise ValueError("Only a moving train must have movement")
        if self.status == "on_track" and self.track_id is None:
            raise ValueError("An on_track train needs track_id")
        if self.status in ("scheduled", "waiting_entry", "departed") and self.track_id:
            raise ValueError("Train outside the station cannot have track_id")
        return self


class Resource(Contract):
    id: Id
    kind: Literal["locomotive", "crew", "cargo_front"]
    capabilities: list[Id]
    availability: Literal["available", "unavailable"] = "available"
    unavailable_until_s: Seconds | None = None
    active_operation_id: Id | None = None


class WaitReason(Contract):
    code: Id
    message: str


class Operation(Contract):
    id: Id
    train_id: Id
    kind: Literal["arrival", "dwell", "inspection", "shunt_to_cargo", "shunt_to_storage", "shunt_to_departure", "cargo", "formation", "preparation", "departure"]
    duration_s: PositiveInt
    predecessor_ids: list[Id]
    status: Literal["pending", "running", "completed", "cancelled"] = "pending"
    actual_start_s: Seconds | None = None
    actual_end_s: Seconds | None = None
    wait_reason: WaitReason | None = None

    @model_validator(mode="after")
    def valid_actuals(self):
        if self.status in ("running", "completed") and self.actual_start_s is None:
            raise ValueError("Started operation needs actual_start_s")
        if self.status == "completed" and self.actual_end_s is None:
            raise ValueError("Completed operation needs actual_end_s")
        if self.actual_end_s is not None:
            if self.actual_start_s is None or self.actual_end_s <= self.actual_start_s:
                raise ValueError("Actual end must be after actual start")
        if self.status == "pending" and self.actual_start_s is not None:
            raise ValueError("Pending operation cannot have actual times")
        if self.status == "running" and self.actual_end_s is not None:
            raise ValueError("Running operation cannot have actual_end_s")
        return self


class Assignment(Contract):
    operation_id: Id
    start_s: Seconds
    end_s: Seconds
    track_id: Id | None = None
    route_id: Id | None = None
    resource_ids: list[Id] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_s <= self.start_s:
            raise ValueError("Assignment interval must be nonempty: [start_s, end_s)")
        if len(self.resource_ids) != len(set(self.resource_ids)):
            raise ValueError("Duplicate resource assignment")
        return self


ConflictCode = Literal["TRACK_CLOSED", "TRACK_OCCUPIED", "ROUTE_BUSY", "RESOURCE_UNAVAILABLE", "PREDECESSOR_INCOMPLETE", "NO_FEASIBLE_SLOT", "STALE_PLAN"]


class Conflict(Contract):
    id: Id
    code: ConflictCode
    severity: Literal["warning", "error"]
    entity_ids: list[Id]
    operation_ids: list[Id]
    start_s: Seconds
    end_s: Seconds | None
    message: str

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end_s is not None and self.end_s <= self.start_s:
            raise ValueError("Conflict interval must be nonempty")
        return self


class Unassigned(Contract):
    operation_id: Id
    code: ConflictCode
    message: str


class PlanViolation(Contract):
    code: str
    message: str
    severity: str = "error"
    entity_ids: list[str] = Field(default_factory=list)
    operation_ids: list[str] = Field(default_factory=list)
    start_s: Seconds | None = None
    end_s: Seconds | None = None


class AssignmentChange(Contract):
    operation_id: Id
    before: Assignment | None
    after: Assignment | None


class Plan(Contract):
    id: Id
    run_id: Id
    based_on_version: Seconds
    strategy: Literal["passenger_first", "earliest_departure"]
    status: Literal["feasible", "partial", "infeasible", "timeout"]
    assignments: list[Assignment]
    unassigned: list[Unassigned]
    # Metric names/units must be agreed with the planner in stage 2.
    metrics: dict[str, float | None] = Field(default_factory=dict)
    explanations: list[str] = Field(default_factory=list)
    timed_out: bool = False
    based_on_time_s: Seconds = 0
    calculation_time_ms: Annotated[float, Field(ge=0)] = 0
    violations: list[PlanViolation] = Field(default_factory=list)
    changes: list[AssignmentChange] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_assignments(self):
        assigned = [a.operation_id for a in self.assignments]
        missing = [a.operation_id for a in self.unassigned]
        if len(assigned) != len(set(assigned)) or len(missing) != len(set(missing)):
            raise ValueError("Duplicate operation in plan")
        if set(assigned) & set(missing):
            raise ValueError("Operation cannot be both assigned and unassigned")
        if self.status == "feasible" and (self.unassigned or self.violations):
            raise ValueError("Feasible plan cannot have unassigned operations")
        return self


class Snapshot(Contract):
    schema_version: Literal[1] = 1
    run_id: Id
    state_version: Seconds
    last_seq: Seconds
    sim_time_s: Seconds
    speed: Literal[1, 5, 10]
    paused: bool
    trains: list[Train]
    tracks: list[Track]
    resources: list[Resource]
    operations: list[Operation]
    active_plan_id: Id | None = None
    conflicts: list[Conflict] = Field(default_factory=list)
    replan_required: bool = False

    @model_validator(mode="after")
    def validate_references(self):
        for name in ("trains", "tracks", "resources", "operations"):
            ids = [x.id for x in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"Duplicate ID in {name}")
        trains = {t.id for t in self.trains}
        tracks = {t.id for t in self.tracks}
        operations = {o.id for o in self.operations}
        for t in self.trains:
            if t.track_id is not None and t.track_id not in tracks:
                raise ValueError(f"Unknown track for {t.id}")
        for t in self.tracks:
            if t.occupant_train_id is not None and t.occupant_train_id not in trains:
                raise ValueError(f"Unknown occupant for {t.id}")
        for r in self.resources:
            if r.active_operation_id is not None and r.active_operation_id not in operations:
                raise ValueError(f"Unknown operation for {r.id}")
        for op in self.operations:
            if op.train_id not in trains or not set(op.predecessor_ids) <= operations:
                raise ValueError(f"Broken operation reference: {op.id}")
            if op.id in op.predecessor_ids:
                raise ValueError("Operation cannot depend on itself")
        return self


class Topology(Contract):
    station_id: Id
    name: str
    view_box: tuple[int, int, int, int]
    boundary_nodes: dict[Id, Point]
    conflict_zones: dict[Id, Point]
    routes: list[Route]


class StateResponse(Contract):
    schema_version: Literal[1] = 1
    snapshot: Snapshot
    topology: Topology

    @model_validator(mode="after")
    def topology_references(self):
        route_ids = [r.id for r in self.topology.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError("Duplicate route ID")
        nodes = {t.id for t in self.snapshot.tracks} | set(self.topology.boundary_nodes)
        for r in self.topology.routes:
            if r.from_id not in nodes or r.to_id not in nodes:
                raise ValueError(f"Unknown endpoint for {r.id}")
            if not set(r.conflict_zone_ids) <= self.topology.conflict_zones.keys():
                raise ValueError(f"Unknown conflict zone for {r.id}")
        for t in self.snapshot.trains:
            if t.movement and t.movement.route_id not in route_ids:
                raise ValueError(f"Unknown movement route for {t.id}")
        return self


class DomainEvent(Contract):
    sim_time_s: Seconds
    type: Id
    entity_id: Id | None = None
    payload: dict[str, Any]


class Event(DomainEvent):
    event_id: Id
    run_id: Id
    seq: PositiveInt
    recorded_at: datetime


class ControlCommand(Contract):
    command_id: Id
    run_id: Id
    action: Literal["start", "pause", "speed", "reset"]
    speed: Literal[1, 5, 10] | None = None

    @model_validator(mode="after")
    def valid_speed(self):
        if (self.action == "speed") != (self.speed is not None):
            raise ValueError("speed is required only for action=speed")
        return self


class IncidentSpec(Contract):
    kind: Literal["delay_train", "close_track", "locomotive_unavailable"]
    target_id: Id
    duration_s: PositiveInt | None = None
    delay_s: PositiveInt | None = None

    @model_validator(mode="after")
    def valid_arguments(self):
        if self.kind == "delay_train":
            if self.delay_s is None or self.duration_s is not None:
                raise ValueError("delay_train requires only delay_s")
        elif self.duration_s is None or self.delay_s is not None:
            raise ValueError("Resource incident requires only duration_s")
        return self


class IncidentCommand(IncidentSpec):
    command_id: Id
    run_id: Id


class IncidentBatchCommand(Contract):
    command_id: Id
    run_id: Id
    incidents: Annotated[list[IncidentSpec], Field(min_length=1, max_length=50)]


class CommandResult(Contract):
    command_id: Id
    run_id: Id
    state_version: Seconds
    replan_required: bool = False


class ApplyPlanCommand(Contract):
    command_id: Id
    run_id: Id
    expected_state_version: Seconds


class ReplanCommand(Contract):
    command_id: Id = Field(default_factory=lambda: str(uuid4()))
    run_id: Id


class ReplanAccepted(Contract):
    command_id: Id
    run_id: Id
    job_id: Id


class ReplanJob(Contract):
    job_id: Id
    run_id: Id
    based_on_version: Seconds
    based_on_time_s: Seconds
    status: Literal["queued", "running", "completed", "failed", "superseded"]
    plan_ids: list[Id] = Field(default_factory=list)
    stale: bool = False
    identical: bool = False
    error: dict[str, str] | None = None


class PlanResponse(Contract):
    plan: Plan
    stale: bool
    applicable: bool


class HistoricalState(StateResponse):
    view: Literal["history"] = "history"
    read_only: Literal[True] = True
    at_s: Seconds


class WsEnvelope(Contract):
    schema_version: Literal[1] = 1
    run_id: Id
    ws_seq: PositiveInt
    state_version: Seconds
    type: Literal["snapshot", "state_updated", "clock_sync", "replan_started", "replan_finished", "replan_failed", "simulation_error"]
    payload: dict[str, Any]


class ApiError(Contract):
    code: str
    message: str
    details: list[dict[str, Any]] = Field(default_factory=list)
