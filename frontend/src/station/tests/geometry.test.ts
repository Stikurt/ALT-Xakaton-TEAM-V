import { test } from 'node:test';
import assert from 'node:assert/strict';
import { modelTime, pointOnPolyline, trainPosition } from '../geometry';
import { makeSnapshot, topology } from '../demo/fixtures';
test('interpolation follows segment length, not endpoint shortcut', () => {
    assert.deepEqual(pointOnPolyline([[0, 0], [30, 0], [30, 90]], .5), { x: 30, y: 30, angle: 90 });
    assert.equal(pointOnPolyline([[0, 0], [0, 0], [100, 0]], .5)?.x, 50);
});
test('progress clamps and degenerate paths are safe', () => {
    assert.equal(pointOnPolyline([[0, 0], [100, 0]], -1)?.x, 0);
    assert.equal(pointOnPolyline([[0, 0], [100, 0]], 3)?.x, 100);
    assert.equal(pointOnPolyline([], .5), null);
    assert.deepEqual(pointOnPolyline([[5, 6], [5, 6]], .8), { x: 5, y: 6, angle: 0 });
});
test('server anchor, pause, disconnection and historical snapshots control clock', () => {
    const clock = { sim_time_s: 60, speed: 5 as const, paused: false };
    assert.equal(modelTime(clock, 1000, 3000, true, true), 70);
    assert.equal(modelTime({ ...clock, speed: 10 }, 3000, 4000, true, true), 70);
    assert.equal(modelTime({ ...clock, paused: true }, 1000, 3000, true, true), 60);
    assert.equal(modelTime(clock, 1000, 3000, false, true), 60);
    assert.equal(modelTime(clock, 1000, 3000, true, false), 60);
    assert.equal(modelTime(clock, 4000, 3000, true, true), 60);
});
test('new run/time supersedes previous movement and departure hides train', () => {
    assert.deepEqual(trainPosition(makeSnapshot(0, 5, true, 'reset').trains[0], topology, 0), { x: 40, y: 450, angle: 0 });
    assert.equal(trainPosition(makeSnapshot(600, 5, false).trains[0], topology, 600), null);
    assert.equal(trainPosition(makeSnapshot(120, 5, true).trains[0], topology, 120)?.x, 700);
});
test('unknown routes and invalid movement durations never invent a position', () => {
    const train = makeSnapshot(60, 5, false).trains[0];
    assert.equal(trainPosition({ ...train, movement: { route_id: 'missing', started_at_s: 0, expected_end_at_s: 120 } }, topology, 60), null);
    assert.equal(trainPosition({ ...train, movement: { route_id: 'R_W_P01', started_at_s: 0, expected_end_at_s: 0 } }, topology, 60), null);
});
test('geometry includes all 12 paths and defined route endpoints', () => {
    assert.equal(topology.tracks.length, 12);
    for (const route of topology.routes) {
        const a = pointOnPolyline(route.polyline, 0)!, b = pointOnPolyline(route.polyline, 1)!;
        assert.deepEqual([a.x, a.y], route.polyline[0]);
        assert.deepEqual([b.x, b.y], route.polyline.at(-1));
    }
});
