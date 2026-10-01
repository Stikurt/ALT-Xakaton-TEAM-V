// Приведение ответов backend к внутренним типам интерфейса.
// Источник истины — контракт И (ветка backend: backend/app/domain/models.py, docs/contracts.md).
// Адаптер принимает и контракт backend, и расширенный формат мок-сервера, и ничего не вычисляет «за сервер»:
// только переименовывает поля, выводит очевидное (тип маршрута по концам) и подставляет пустые значения.
import type {
  Assignment, Conflict, Explanation, Operation, OpKind, Plan, PlanMetrics, Pt, Resource, Route, Snapshot, Topology, Track, Train,
} from './types'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Raw = any

const OP_KIND: Record<string, OpKind> = {
  dwell: 'stop', stop: 'stop', shunt_to_cargo: 'shunt', shunt_to_storage: 'shunt', shunt_to_departure: 'shunt', shunt: 'shunt',
  arrival: 'arrival', departure: 'departure', inspection: 'inspection', preparation: 'preparation', cargo: 'cargo', formation: 'formation',
}
const TRACK_KIND: Record<string, string> = { storage: 'staging', locomotive: 'loco' }

export const INCIDENT_TO_SERVER = { delay: 'delay_train', close_track: 'close_track', loco_unavailable: 'locomotive_unavailable' } as const

function resKind(r: Raw): string {
  if (r.kind === 'locomotive') return 'shunting_loco'
  if (r.kind === 'crew') {
    const caps: string[] = r.capabilities ?? []
    return caps.includes('inspection') || caps.includes('preparation') ? 'inspection_crew' : 'shunting_crew'
  }
  return r.kind
}

const waitText = (w: Raw): string | null => (w == null ? null : typeof w === 'string' ? w : w.message ?? w.code ?? null)

export function normalizeOperations(raw: Raw[]): Operation[] {
  const stageByTrain: Record<string, number> = {}
  return raw.map((o) => {
    const kind = OP_KIND[o.kind] ?? o.kind
    const isMove = o.is_move ?? (kind === 'arrival' ? 'arrival' : kind === 'departure' ? 'departure' : kind === 'shunt' ? 'shunt' : null)
    let stage = o.stage
    if (stage === undefined) {
      const cur = stageByTrain[o.train_id] ?? -1
      stage = kind === 'arrival' ? 0 : isMove ? cur + 1 : Math.max(cur, 0)
      stageByTrain[o.train_id] = stage
    }
    return {
      id: o.id, train_id: o.train_id, kind, duration_s: o.duration_s, predecessor_ids: o.predecessor_ids ?? [],
      status: o.status, actual_start_s: o.actual_start_s ?? null, actual_end_s: o.actual_end_s ?? null,
      wait_reason: waitText(o.wait_reason), stage, is_move: isMove,
    }
  })
}

export function normalizePlan(raw: Raw, ops?: Operation[]): Plan {
  const byOp = Object.fromEntries((ops ?? []).map((o) => [o.id, o]))
  const assignments: Assignment[] = (raw.assignments ?? []).map((a: Raw) => {
    const op = byOp[a.operation_id]
    return {
      operation_id: a.operation_id, train_id: a.train_id ?? op?.train_id ?? a.operation_id.split(/[-_]/)[0],
      kind: OP_KIND[a.kind] ?? a.kind ?? op?.kind ?? 'stop', start_s: a.start_s, end_s: a.end_s, track_id: a.track_id ?? null,
      route_id: a.route_id ?? null, resource_ids: a.resource_ids ?? [], fixed: a.fixed ?? (op ? op.status !== 'pending' : false),
    }
  })
  const trainOf = (opId: string) => byOp[opId]?.train_id ?? opId.split(/[-_]/)[0]
  const unassigned = (raw.unassigned ?? []).map((u: Raw) =>
    typeof u === 'string'
      ? { train_id: u, code: 'NO_FEASIBLE_SLOT', message: `${u}: не размещён` }
      : { ...u, train_id: u.train_id ?? (u.operation_id ? trainOf(u.operation_id) : '—') })
  const m = raw.metrics ?? {}
  const metrics: PlanMetrics = {
    total_delay_s: m.total_delay_s ?? null, max_delay_s: m.max_delay_s ?? null, unassigned_count: m.unassigned_count ?? new Set(unassigned.map((u: Raw) => u.train_id)).size,
    changed_count: m.changed_count ?? null, delayed_trains: m.delayed_trains ?? null, forecast_departures: m.forecast_departures ?? {}, delays: m.delays ?? {},
  }
  const explanations: Explanation[] = (raw.explanations ?? []).map((e: Raw, i: number) =>
    typeof e === 'string' ? { train_id: `#${i + 1}`, code: 'NOTE', operation_ids: [], message: e } : e)
  return {
    id: raw.id, run_id: raw.run_id, based_on_version: raw.based_on_version ?? 0, based_on_epoch: raw.based_on_epoch ?? -1,
    based_on_time_s: raw.based_on_time_s ?? 0, strategy: raw.strategy, status: raw.status, timed_out: !!raw.timed_out,
    assignments,
    unassigned,
    metrics, explanations, violations: raw.violations ?? [], calc_ms: raw.calc_ms ?? null,
    identical_to_other: raw.identical_to_other, index_forecast: raw.index_forecast ?? null,
  }
}

export function normalizeSnapshot(raw: Raw, topo?: Topology | null): Snapshot {
  const operations = normalizeOperations(raw.operations ?? [])
  const trains: Train[] = (raw.trains ?? []).map((t: Raw) => ({
    ...t, movement: t.movement ?? null, track_id: t.track_id ?? null, current_operation_id: t.current_operation_id ?? null,
    wait_reason: waitText(t.wait_reason) ?? waitText(operations.find((o) => o.train_id === t.id && o.status === 'pending' && o.wait_reason)?.wait_reason),
    forecast_departure_s: t.forecast_departure_s ?? null, delay_s: t.delay_s ?? 0, actual_departure_s: t.actual_departure_s ?? null,
  }))
  const tracks: Track[] = (raw.tracks ?? []).map((t: Raw) => ({
    id: t.id, kind: TRACK_KIND[t.kind] ?? t.kind, usable_length_m: t.usable_length_m, availability: t.availability ?? 'open',
    closed_until_s: t.closed_until_s ?? null, occupant_train_id: t.occupant_train_id ?? null,
  }))
  const resources: Resource[] = (raw.resources ?? []).map((r: Raw) => ({
    id: r.id, kind: resKind(r), capabilities: r.capabilities ?? [], availability: r.availability ?? 'available',
    unavailable_until_s: r.unavailable_until_s ?? null, active_operation_id: r.active_operation_id ?? null,
  }))
  // Горловины: из снимка мок-сервера или по движущимся составам (маршрут → conflict_zone_ids).
  let zones = raw.zones
  if (!zones) {
    const routes = Object.fromEntries((topo?.routes ?? []).map((r) => [r.id, r]))
    const busy: Record<string, string | null> = {}
    for (const z of topo?.zones ?? [{ id: 'GW' }, { id: 'GE' }]) busy[z.id] = null
    for (const t of trains) {
      if (!t.movement) continue
      const r = routes[t.movement.route_id]
      const op = operations.find((o) => o.train_id === t.id && o.status === 'running')
      for (const z of r?.conflict_zone_ids ?? []) busy[z] = op?.id ?? t.id
    }
    zones = Object.entries(busy).map(([id, v]) => ({ id, active_operation_id: v }))
  }
  const conflicts: Conflict[] = (raw.conflicts ?? []).map((c: Raw) => ({
    ...c, severity: c.severity === 'error' ? 'high' : c.severity === 'warning' ? 'medium' : c.severity ?? 'medium',
    kind: c.kind ?? (c.end_s == null ? 'execution' : 'plan'), entity_ids: c.entity_ids ?? [], operation_ids: c.operation_ids ?? [],
  }))
  return {
    schema_version: raw.schema_version ?? 1, run_id: raw.run_id, state_version: raw.state_version, epoch: raw.epoch ?? -1,
    last_seq: raw.last_seq ?? 0, sim_time_s: raw.sim_time_s, speed: raw.speed, paused: raw.paused,
    trains, tracks, resources, zones, operations, active_plan_id: raw.active_plan_id ?? null,
    active_plan: raw.active_plan ? normalizePlan(raw.active_plan, operations) : null,
    conflicts, queue: raw.queue ?? trains.filter((t) => t.status === 'waiting_entry').map((t) => t.id),
    index: raw.index ?? null, incidents: raw.incidents ?? [],
  }
}

const asPt = (p: Raw): Pt => (Array.isArray(p) ? [Number(p[0]), Number(p[1])] : [Number(p.x), Number(p.y)])

export function normalizeTopology(raw: Raw, rawSnapshot?: Raw): Topology {
  if (raw.tracks && raw.nodes) return raw as Topology // формат мок-сервера
  const tracks = (rawSnapshot?.tracks ?? []).map((t: Raw) => {
    const g = t.geometry
    const [a, b] = Array.isArray(g) ? [asPt(g[0]), asPt(g[g.length - 1])] : [[g.x1, g.y1], [g.x2, g.y2]]
    return { id: t.id, kind: TRACK_KIND[t.kind] ?? t.kind, usable_length_m: t.usable_length_m, geometry: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] } }
  })
  const bn = raw.boundary_nodes ?? {}
  const cz = raw.conflict_zones ?? {}
  const zoneNames: Record<string, string> = { GW: 'Западная горловина', GE: 'Восточная горловина' }
  const routes: Route[] = (raw.routes ?? []).map((r: Raw) => ({
    ...r, polyline: (r.polyline ?? []).map(asPt),
    kind: r.kind ?? (r.from_id === 'W' ? 'arrival' : r.to_id === 'E' ? 'departure' : 'shunt'),
  }))
  const byKind = (k: string) => tracks.filter((t: Raw) => t.kind === k).map((t: Raw) => t.id)
  return {
    schema_version: 1, name: raw.name ?? 'Узел 12', viewbox: raw.view_box ?? [0, 0, 1400, 900],
    nodes: { W: asPt(bn.W ?? [40, 450]), GW: asPt(cz.GW ?? [180, 450]), GE: asPt(cz.GE ?? [1220, 450]), E: asPt(bn.E ?? [1360, 450]) },
    zones: Object.keys(cz).length ? Object.keys(cz).map((id) => ({ id, name: zoneNames[id] ?? id })) : [{ id: 'GW', name: zoneNames.GW }, { id: 'GE', name: zoneNames.GE }],
    areas: [
      { id: 'platform', label: 'Платформы', track_ids: byKind('passenger') },
      { id: 'cargo', label: 'Грузовой фронт', track_ids: byKind('cargo') },
    ],
    tracks, routes, horizon_s: raw.horizon_s ?? 7200,
    resources: (rawSnapshot?.resources ?? []).map((r: Raw) => ({ id: r.id, kind: resKind(r), capabilities: r.capabilities ?? [] })),
  }
}

export function normalizeState(raw: Raw): { snapshot: Snapshot; topology: Topology } {
  const topology = normalizeTopology(raw.topology ?? {}, raw.snapshot)
  return { snapshot: normalizeSnapshot(raw.snapshot, topology), topology }
}
