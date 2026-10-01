import type { Point, Topology, Snapshot, PreviewPlan, Train, Route } from '../types';
/** These are component review fixtures, NOT a valid executable station plan. */
const W: Point = [40, 450], GW: Point = [180, 450], GE: Point = [1220, 450], E: Point = [1360, 450];
const tracks: Topology['tracks'] = Array.from({ length: 12 }, (_, i) => ({
    id: `P${String(i + 1).padStart(2, '0')}`, kind: i < 2 ? 'passenger' : i < 6 ? 'freight' : i < 9 ? 'holding' : i < 11 ? 'cargo' : 'depot', usable_length_m: i < 2 ? 500 : i < 9 ? 900 : i < 11 ? 700 : 150, geometry: [[320, 120 + i * 60], [1080, 120 + i * 60]],
}));
const mid = (id: string): Point => [700, tracks.find(t => t.id === id)!.geometry[0][1]];
const routes: Route[] = tracks.slice(0, 6).flatMap(t => [
    { id: `R_W_${t.id}`, from_id: 'W', to_id: t.id, conflict_zone_ids: ['GW'], duration_s: 120, polyline: [W, GW, t.geometry[0], mid(t.id)] },
    { id: `R_${t.id}_E`, from_id: t.id, to_id: 'E', conflict_zone_ids: ['GE'], duration_s: 120, polyline: [mid(t.id), t.geometry[1], GE, E] },
]);
for (const a of tracks)
    for (const b of tracks) {
        const permitted = (a.kind === 'freight' && b.kind === 'cargo') || (a.kind === 'cargo' && b.kind === 'holding') || (a.kind === 'holding' && b.kind === 'freight');
        if (permitted)
            routes.push({ id: `R_${a.id}_${b.id}`, from_id: a.id, to_id: b.id, conflict_zone_ids: ['GW'], duration_s: 180, polyline: [mid(a.id), a.geometry[0], GW, b.geometry[0], mid(b.id)] });
    }
export const topology: Topology = { tracks, routes };
const schedule = [[350, 0, 10], [750, 2, 20], [600, 4, 55], [350, 8, 18], [800, 10, 28], [650, 12, 65], [350, 18, 28], [750, 20, 38], [600, 22, 78], [800, 28, 46], [350, 32, 42], [650, 36, 94], [750, 40, 58], [350, 46, 56], [600, 50, 112]];
export function makeSnapshot(time: number, speed: 1 | 5 | 10, paused: boolean, run = 'demo-1'): Snapshot {
    const parked: Record<string, string> = { T02: 'P03', T03: 'P10', T05: 'P05', T06: 'P08', T08: 'P06', T09: 'P11' };
    const trains: Train[] = schedule.map(([length, arrival, departure], i) => {
        const id = `T${String(i + 1).padStart(2, '0')}`, kind = [0, 3, 6, 10, 13].includes(i) ? 'passenger' : [2, 5, 8, 11, 14].includes(i) ? 'local' : 'transit';
        return { id, kind, length_m: length, priority: kind === 'passenger' ? 3 : kind === 'transit' ? 2 : 1, scheduled_arrival_s: arrival * 60, expected_arrival_s: arrival * 60, scheduled_departure_s: departure * 60, status: parked[id] ? 'on_track' : i === 9 || i === 10 ? 'waiting_entry' : 'scheduled', track_id: parked[id] ?? null, movement: null };
    });
    const t = trains[0];
    if (time < 120) {
        t.status = 'moving';
        t.movement = { route_id: 'R_W_P01', started_at_s: 0, expected_end_at_s: 120 };
    }
    else if (time < 480) {
        t.status = 'on_track';
        t.track_id = 'P01';
    }
    else if (time < 600) {
        t.status = 'moving';
        t.track_id = 'P01';
        t.movement = { route_id: 'R_P01_E', started_at_s: 480, expected_end_at_s: 600 };
    }
    else
        t.status = 'departed';
    return { run_id: run, state_version: time < 120 ? 1 : time < 480 ? 2 : time < 600 ? 3 : 4, last_seq: 1, sim_time_s: time, speed, paused, active_plan_id: 'demo-active', trains,
        tracks: tracks.map(t => ({ id: t.id, availability: t.id === 'P04' ? 'closed' : 'open', closed_until_s: t.id === 'P04' ? 900 : null, occupant_train_id: trains.find(train => train.track_id === t.id)?.id ?? null })),
        operations: [{ id: 'OP_T03_HOLD', train_id: 'T03', kind: 'Ожидание маневрового локомотива', status: 'pending', wait_reason: 'L01 недоступен; ожидается назначение L02' }],
    };
}
export function makePreview(snapshot: Snapshot): PreviewPlan { return { id: 'Вариант B', run_id: snapshot.run_id, based_on_version: snapshot.state_version, status: 'partial', assignments: [{ operation_id: 'OP_T03_HOLD', start_s: 300, end_s: 480, track_id: 'P07', route_id: 'R_P10_P07', resource_ids: ['L02', 'B01'] }] }; }
