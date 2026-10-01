import type { Point, Snapshot, Train, Topology } from './types';
export const clamp = (v: number, min: number, max: number) => Math.min(max, Math.max(min, v));
export function pointOnPolyline(points: readonly Point[], progress: number) {
    if (!points.length)
        return null;
    const lengths = points.slice(1).map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]));
    let remaining = lengths.reduce((a, b) => a + b, 0) * clamp(progress, 0, 1);
    for (let i = 0; i < lengths.length; i++) {
        if (lengths[i] === 0)
            continue;
        if (remaining <= lengths[i] || i === lengths.length - 1) {
            const t = clamp(remaining / lengths[i], 0, 1), a = points[i], b = points[i + 1];
            return { x: a[0] + (b[0] - a[0]) * t, y: a[1] + (b[1] - a[1]) * t, angle: Math.atan2(b[1] - a[1], b[0] - a[0]) * 180 / Math.PI };
        }
        remaining -= lengths[i];
    }
    return { x: points[0][0], y: points[0][1], angle: 0 };
}
export function modelTime(snapshot: Pick<Snapshot, 'sim_time_s' | 'speed' | 'paused'>, receivedAt: number, now: number, live: boolean, connected: boolean) {
    return snapshot.sim_time_s + (live && connected && !snapshot.paused ? Math.max(0, now - receivedAt) / 1000 * snapshot.speed : 0);
}
export function trainPosition(train: Train, topology: Topology, time: number) {
    if (train.status === 'moving') {
        const movement = train.movement;
        const route = topology.routes.find(r => r.id === movement?.route_id);
        if (!movement || !route || movement.expected_end_at_s <= movement.started_at_s)
            return null;
        return pointOnPolyline(route.polyline, (time - movement.started_at_s) / (movement.expected_end_at_s - movement.started_at_s));
    }
    if (train.status !== 'on_track')
        return null;
    const track = topology.tracks.find(t => t.id === train.track_id);
    return track ? pointOnPolyline(track.geometry, 0.5) : null;
}
export const polylinePoints = (points: readonly Point[]) => points.map(p => p.join(',')).join(' ');
export function formatTime(seconds: number) {
    const s = Math.max(0, Math.floor(seconds));
    return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(n => String(n).padStart(2, '0')).join(':');
}
