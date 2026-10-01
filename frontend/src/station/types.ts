/** Temporary view contract from specification v1.0. Replace with shared imports when backend publishes schemas. */
export type Point = readonly [
    number,
    number
];
export type Selection = {
    kind: 'train' | 'track';
    id: string;
} | null;
export interface TrackGeometry {
    id: string;
    kind: 'passenger' | 'freight' | 'holding' | 'cargo' | 'depot';
    usable_length_m: number;
    geometry: readonly Point[];
}
export interface Route {
    id: string;
    from_id: string;
    to_id: string;
    conflict_zone_ids: string[];
    duration_s: number;
    polyline: readonly Point[];
}
export interface Topology {
    tracks: readonly TrackGeometry[];
    routes: readonly Route[];
}
export interface Movement {
    route_id: string;
    started_at_s: number;
    expected_end_at_s: number;
}
export interface Train {
    id: string;
    kind: 'passenger' | 'transit' | 'local';
    length_m: number;
    priority: number;
    scheduled_arrival_s: number;
    expected_arrival_s: number;
    scheduled_departure_s: number;
    status: 'scheduled' | 'waiting_entry' | 'moving' | 'on_track' | 'departed';
    track_id: string | null;
    movement: Movement | null;
}
export interface TrackState {
    id: string;
    availability: 'open' | 'closed';
    closed_until_s: number | null;
    occupant_train_id: string | null;
}
export interface Operation {
    id: string;
    train_id: string;
    kind: string;
    status: 'pending' | 'running' | 'completed' | 'cancelled';
    wait_reason: string | null;
}
export interface Snapshot {
    run_id: string;
    state_version: number;
    last_seq: number;
    sim_time_s: number;
    speed: 1 | 5 | 10;
    paused: boolean;
    trains: readonly Train[];
    tracks: readonly TrackState[];
    operations: readonly Operation[];
    active_plan_id: string | null;
}
export interface PreviewPlan {
    id: string;
    run_id: string;
    based_on_version: number;
    status: 'feasible' | 'partial' | 'infeasible' | 'timeout';
    assignments: readonly {
        operation_id: string;
        start_s: number;
        end_s: number;
        track_id: string | null;
        route_id: string | null;
        resource_ids: string[];
    }[];
}
export interface Conflict {
    id: string;
    message: string;
    entity_ids: readonly string[];
}
export interface StationViewProps {
    snapshot: Snapshot;
    topology: Topology;
    selection: Selection;
    onSelect: (selection: Selection) => void;
    previewPlan?: PreviewPlan | null;
    viewMode: 'live' | 'history';
    /** Time of receipt of the latest snapshot/clock_sync in performance.now() milliseconds. Update for EVERY clock sync. */
    clockReceivedAtMs: number;
    connection: 'connected' | 'disconnected';
    conflicts?: readonly Conflict[];
    highlightedEntityIds?: readonly string[];
    className?: string;
}
