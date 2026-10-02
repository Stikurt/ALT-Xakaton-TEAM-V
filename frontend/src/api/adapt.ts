// Приведение ответов backend к внутренним типам интерфейса.
// Источник истины — контракт И (ветка backend: backend/app/domain/models.py, docs/contracts.md).
// Адаптер принимает и контракт backend, и расширенный формат мок-сервера, и ничего не вычисляет «за сервер»:
// только переименовывает поля, выводит очевидное (тип маршрута по концам) и подставляет пустые значения.
import type {
  Assignment, Conflict, Explanation, IncidentCmd, Operation, OpKind, Plan, PlanMetrics, Pt, Resource, Route, Snapshot, Topology, Track, Train,
} from './types'

/* eslint-disable @typescript-eslint/no-explicit-any */
type Raw = any

const OP_KIND: Record<string, OpKind> = {
  dwell: 'stop', stop: 'stop', shunt_to_cargo: 'shunt', shunt_to_storage: 'shunt', shunt_to_departure: 'shunt', shunt: 'shunt',
  arrival: 'arrival', departure: 'departure', inspection: 'inspection', preparation: 'preparation', cargo: 'cargo', formation: 'formation',
}
const TRACK_KIND: Record<string, string> = { storage: 'staging', locomotive: 'loco' }

export const INCIDENT_TO_SERVER = { delay: 'delay_train', close_track: 'close_track', loco_unavailable: 'locomotive_unavailable' } as const

/** IncidentSpec backend: delay_train несёт только delay_s, сбои ресурсов — только duration_s (лишнее поле → 422). */
export function incidentToServer(c: IncidentCmd): { kind: string; target_id: string; delay_s?: number; duration_s?: number } {
  const kind = INCIDENT_TO_SERVER[c.kind]
  return kind === 'delay_train'
    ? { kind, target_id: c.target_id, delay_s: Math.round(c.delay_s ?? c.duration_s ?? 0) }
    : { kind, target_id: c.target_id, duration_s: Math.round(c.duration_s ?? c.delay_s ?? 0) }
}

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
    // backend: total_positive_delay_s / changed_future_assignments (PlanMetrics в domain/models.py); мок: total_delay_s / changed_count
    total_delay_s: m.total_delay_s ?? m.total_positive_delay_s ?? null, max_delay_s: m.max_delay_s ?? null,
    unassigned_count: m.unassigned_count ?? new Set(unassigned.map((u: Raw) => u.train_id)).size,
    changed_count: m.changed_count ?? m.changed_future_assignments ?? null, delayed_trains: m.delayed_trains ?? null, forecast_departures: m.forecast_departures ?? {}, delays: m.delays ?? {},
  }
  const explanations: Explanation[] = (raw.explanations ?? []).map((e: Raw, i: number) =>
    typeof e === 'string' ? { train_id: `#${i + 1}`, code: 'NOTE', operation_ids: [], message: e } : e)
  return {
    id: raw.id, run_id: raw.run_id, based_on_version: raw.based_on_version ?? 0, based_on_epoch: raw.based_on_epoch ?? -1,
    based_on_time_s: raw.based_on_time_s ?? 0, strategy: raw.strategy, status: raw.status, timed_out: !!raw.timed_out,
    assignments,
    unassigned,
    metrics, explanations, violations: raw.violations ?? [], calc_ms: raw.calc_ms ?? raw.calculation_time_ms ?? null,
    identical_to_other: raw.identical_to_other, index_forecast: raw.index_forecast ?? null,
    stale: raw.stale, applicable: raw.applicable,
  }
}

/** GET /api/plans/{id}: backend отвечает PlanResponse {plan, stale, applicable}; мок — тем же конвертом. */
export function normalizePlanResponse(raw: Raw, ops?: Operation[]): Plan {
  if (raw && typeof raw === 'object' && raw.plan && typeof raw.plan === 'object') {
    return normalizePlan({ ...raw.plan, stale: raw.stale, applicable: raw.applicable }, ops)
  }
  return normalizePlan(raw ?? {}, ops)
}

/** План устарел относительно текущего снимка. Мок ведёт epoch, backend — state_version (runtime/planning.candidate_problem). */
export function planIsStale(p: Pick<Plan, 'run_id' | 'based_on_epoch' | 'based_on_version'>, snap: Pick<Snapshot, 'run_id' | 'epoch' | 'state_version'>): boolean {
  if (p.run_id !== snap.run_id) return true
  return snap.epoch >= 0 ? p.based_on_epoch !== snap.epoch : p.based_on_version !== snap.state_version
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
    for (const z of topo?.zones ?? []) busy[z.id] = null
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
  // Узлы и горловины берутся из данных станции, без зашитых id: вход — граничный узел, с которого
  // начинаются маршруты, выход — на котором заканчиваются; горловины — по маршрутам приёма/отправления.
  const bn: Record<string, Raw> = raw.boundary_nodes ?? {}
  const cz: Record<string, Raw> = raw.conflict_zones ?? {}
  const trackIds = new Set(tracks.map((t: Raw) => t.id))
  const rawRoutes: Raw[] = raw.routes ?? []
  const entryId = Object.keys(bn).find((id) => rawRoutes.some((r) => r.from_id === id)) ?? rawRoutes.find((r) => !trackIds.has(r.from_id))?.from_id
  const exitId = Object.keys(bn).find((id) => rawRoutes.some((r) => r.to_id === id)) ?? rawRoutes.find((r) => !trackIds.has(r.to_id))?.to_id
  const zoneOf = (pred: (r: Raw) => boolean) => rawRoutes.find((r) => pred(r) && (r.conflict_zone_ids ?? []).length)?.conflict_zone_ids[0]
  const entryZone = zoneOf((r) => r.from_id === entryId) ?? Object.keys(cz)[0]
  const exitZone = zoneOf((r) => r.to_id === exitId) ?? Object.keys(cz).at(-1)
  const zoneName = (id: string) => (id === entryZone ? `Горловина входа (${id})` : id === exitZone ? `Горловина выхода (${id})` : `Горловина ${id}`)
  const routes: Route[] = rawRoutes.map((r: Raw) => ({
    ...r, polyline: (r.polyline ?? []).map(asPt),
    kind: r.kind ?? (r.from_id === entryId ? 'arrival' : r.to_id === exitId ? 'departure' : 'shunt'),
  }))
  const firstPt = (id: string | undefined, end: boolean) => {
    const r = rawRoutes.find((x) => (end ? x.to_id : x.from_id) === id && (x.polyline ?? []).length)
    return r ? asPt(end ? r.polyline.at(-1) : r.polyline[0]) : null
  }
  const pt = (v: Raw, fallback: Pt | null, dflt: Pt): Pt => (v ? asPt(v) : fallback ?? dflt)
  const byKind = (k: string) => tracks.filter((t: Raw) => t.kind === k).map((t: Raw) => t.id)
  return {
    schema_version: 1, name: raw.name ?? 'Узел 12', viewbox: raw.view_box ?? [0, 0, 1400, 900],
    nodes: {
      W: pt(entryId && bn[entryId], firstPt(entryId, false), [40, 450]),
      GW: pt(entryZone && cz[entryZone], null, [180, 450]),
      GE: pt(exitZone && cz[exitZone], null, [1220, 450]),
      E: pt(exitId && bn[exitId], firstPt(exitId, true), [1360, 450]),
    },
    node_ids: { W: entryId, GW: entryZone, GE: exitZone, E: exitId },
    zones: Object.keys(cz).map((id) => ({ id, name: zoneName(id) })),
    areas: [
      { id: 'platform', label: 'Платформы', track_ids: byKind('passenger') },
      { id: 'cargo', label: 'Грузовой фронт', track_ids: byKind('cargo') },
    ],
    tracks, routes, horizon_s: raw.horizon_s ?? 0, // 0 — сервер не сообщил окно планирования
    resources: (rawSnapshot?.resources ?? []).map((r: Raw) => ({ id: r.id, kind: resKind(r), capabilities: r.capabilities ?? [] })),
  }
}

export function normalizeState(raw: Raw): { snapshot: Snapshot; topology: Topology } {
  const topology = normalizeTopology(raw.topology ?? {}, raw.snapshot)
  return { snapshot: normalizeSnapshot(raw.snapshot, topology), topology }
}

/** Задержки текущего плана по поездам, которые ещё не ушли (для сравнения с вариантами).
 *  Мок присылает прогноз в trains[].delay_s; backend — нет, тогда прогноз берётся из принятого плана:
 *  конец операции отправления минус плановое отправление (как planner.calculate_plan_metrics). */
export function currentDelays(snap: Snapshot): { total: number; max: number } {
  const live = snap.trains.filter((t) => t.status !== 'departed')
  let values = live.map((t) => t.delay_s ?? 0)
  const hasForecast = live.some((t) => t.forecast_departure_s !== null && t.forecast_departure_s !== undefined)
  if (!hasForecast && snap.active_plan) {
    const ops = Object.fromEntries(snap.operations.map((o) => [o.id, o]))
    const end: Record<string, number> = {}
    for (const a of snap.active_plan.assignments) if ((ops[a.operation_id]?.kind ?? a.kind) === 'departure') end[a.train_id] = a.end_s
    values = live.map((t) => (end[t.id] === undefined ? 0 : Math.max(0, end[t.id] - t.scheduled_departure_s)))
  }
  return { total: values.reduce((a, b) => a + b, 0), max: values.length ? Math.max(0, ...values) : 0 }
}
