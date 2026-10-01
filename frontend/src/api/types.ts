// Контракты обмена (ТЗ v1.0, разд. 6–7). Источник истины — shared/examples и схемы И.
// Любое изменение согласуется с И; ручные расхождения запрещены.

export type Pt = [number, number]

export type TrainKind = 'passenger' | 'transit' | 'local'
export type TrainStatus = 'scheduled' | 'waiting_entry' | 'moving' | 'on_track' | 'departed'
export type OpStatus = 'pending' | 'running' | 'completed' | 'cancelled'
export type OpKind =
  | 'arrival' | 'departure' | 'shunt' | 'stop' | 'inspection' | 'preparation' | 'cargo' | 'formation'
export type ConflictCode =
  | 'TRACK_CLOSED' | 'TRACK_OCCUPIED' | 'ROUTE_BUSY' | 'RESOURCE_UNAVAILABLE' | 'RESOURCE_BUSY'
  | 'PREDECESSOR_INCOMPLETE' | 'NO_FEASIBLE_SLOT' | 'STALE_PLAN'

export interface TrackGeom { x1: number; y1: number; x2: number; y2: number }
export interface TopoTrack { id: string; kind: string; usable_length_m: number; geometry: TrackGeom }
export interface Route {
  id: string; from_id: string; to_id: string; kind: 'arrival' | 'departure' | 'shunt'
  conflict_zone_ids: string[]; duration_s: number; polyline: Pt[]
}
export interface Topology {
  schema_version: number; name: string; viewbox: [number, number, number, number]
  nodes: Record<'W' | 'GW' | 'GE' | 'E', Pt>
  zones: { id: string; name: string }[]
  areas: { id: string; label: string; track_ids: string[] }[]
  tracks: TopoTrack[]; routes: Route[]; horizon_s: number
  resources: { id: string; kind: string; capabilities: string[] }[]
}

export interface Movement { route_id: string; started_at_s: number; expected_end_at_s: number }
export interface Train {
  id: string; kind: TrainKind; length_m: number; priority: number
  scheduled_arrival_s: number; expected_arrival_s: number; scheduled_departure_s: number
  status: TrainStatus; track_id: string | null; movement: Movement | null
  current_operation_id: string | null; wait_reason: string | null
  forecast_departure_s: number | null; delay_s: number; actual_departure_s: number | null
}
export interface Track {
  id: string; kind: string; usable_length_m: number
  availability: 'open' | 'closed'; closed_until_s: number | null; occupant_train_id: string | null
}
export interface Resource {
  id: string; kind: string; capabilities: string[]
  availability: 'available' | 'unavailable'; unavailable_until_s: number | null; active_operation_id: string | null
}
export interface Operation {
  id: string; train_id: string; kind: OpKind; duration_s: number; predecessor_ids: string[]
  status: OpStatus; actual_start_s: number | null; actual_end_s: number | null; wait_reason: string | null
  stage: number; is_move: 'arrival' | 'shunt' | 'departure' | null
}
export interface Assignment {
  operation_id: string; train_id: string; kind: OpKind; start_s: number; end_s: number
  track_id: string | null; route_id: string | null; resource_ids: string[]; fixed: boolean
}
export interface Explanation { train_id: string; code: string; operation_ids: string[]; message: string }
export interface PlanMetrics {
  total_delay_s: number | null; max_delay_s: number | null; unassigned_count: number; changed_count: number | null
  delayed_trains: number | null; forecast_departures: Record<string, number>; delays: Record<string, number>
}
export type PlanStatus = 'feasible' | 'partial' | 'infeasible' | 'timeout'
export interface Plan {
  id: string; run_id: string; based_on_version: number; based_on_epoch: number; based_on_time_s: number
  strategy: 'passenger_first' | 'earliest_departure'; status: PlanStatus; timed_out: boolean
  assignments: Assignment[]; unassigned: { train_id: string; code: string; message: string }[]
  metrics: PlanMetrics; explanations: Explanation[]; violations: { code: string; message: string }[]
  calc_ms: number | null; identical_to_other?: boolean; index_forecast: StationIndex | null
}
export interface Conflict {
  id: string; code: ConflictCode; severity: 'high' | 'medium' | 'low'; kind: 'execution' | 'plan'
  entity_ids: string[]; operation_ids: string[]; start_s: number; end_s: number | null; message: string
}
export interface IndexFactor {
  id: string; label: string; penalty: number | null; weight: number; contribution: number | null; no_data?: boolean
}
export interface StationIndex {
  value: number; category: 'norm' | 'attention' | 'critical'; window_s: number; factors: IndexFactor[]; kind: 'fact' | 'forecast'
}
export interface Snapshot {
  schema_version: number; run_id: string; state_version: number; epoch: number; last_seq: number
  sim_time_s: number; speed: 1 | 5 | 10; paused: boolean
  trains: Train[]; tracks: Track[]; resources: Resource[]
  zones: { id: string; active_operation_id: string | null }[]
  operations: Operation[]; active_plan_id: string | null; active_plan: Plan | null
  conflicts: Conflict[]; queue: string[]; index: StationIndex | null
  incidents: { kind: string; target_id: string; at_s: number; message: string }[]
}
export interface ClockSync { sim_time_s: number; sim_time_exact?: number; speed: number; paused: boolean }

export type WsType =
  | 'snapshot' | 'state_updated' | 'clock_sync' | 'replan_started' | 'replan_finished' | 'replan_failed' | 'simulation_error'
export interface WsEnvelope<T = unknown> {
  schema_version: number; run_id: string; ws_seq: number; state_version: number; type: WsType; payload: T
}
export interface ApiError { code: string; message: string; details?: Record<string, unknown> }

export type IncidentKind = 'delay' | 'close_track' | 'loco_unavailable'
export interface IncidentCmd { kind: IncidentKind; target_id: string; duration_s?: number; delay_s?: number }
