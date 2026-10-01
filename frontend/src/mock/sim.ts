// Порт mock_backend/app/sim.py: пошаговое исполнение принятого плана с проверкой ресурсов.
import { CONFIG, ROUTE_BY_ID, buildOperations, type Dict, type Op } from './model'
import { indexFromPenalties, type Asg, type Snap } from './planner'

const WAIT: Dict<(x: string) => string> = {
  TRACK_CLOSED: (x) => `путь ${x} закрыт`,
  TRACK_OCCUPIED: (x) => `путь ${x} занят`,
  ROUTE_BUSY: (x) => `горловина ${x} занята`,
  RESOURCE_UNAVAILABLE: (x) => `ресурс ${x} недоступен`,
  RESOURCE_BUSY: (x) => `ресурс ${x} занят другой операцией`,
  NO_FEASIBLE_SLOT: () => 'нет назначения в принятом плане',
}
const OPL: Dict<string> = { arrival: 'приём', departure: 'отправление', shunt: 'маневр', stop: 'стоянка', inspection: 'осмотр', preparation: 'подготовка', cargo: 'грузовая обработка', formation: 'формирование' }
const INDEX_W = 900
const clone = <T,>(x: T): T => structuredClone(x)

/* eslint-disable @typescript-eslint/no-explicit-any */
type Train = any
type Track = any
type Res = any

export class Station {
  run_id = ''
  sim_time_s = 0
  speed = 1
  paused = true
  state_version = 1
  epoch = 1
  seq = 0
  trains: Train[] = []
  tracks: Track[] = []
  resources: Res[] = []
  zones: Dict<string | null> = { GW: null, GE: null }
  operations: Op[] = []
  active_plan: (Snap & { _by: Dict<Asg> }) | null = null
  plans: Dict<Snap> = {}
  exec_conflicts: Dict<Snap> = {}
  plan_conflicts: Dict<Snap> = {}
  samples: [number, number, number, number][] = []
  incidents: { kind: string; target_id: string; at_s: number; message: string }[] = []

  constructor() { this.reset() }

  reset() {
    this.run_id = 'RUN-' + Math.random().toString(16).slice(2, 8)
    this.sim_time_s = 0; this.speed = 1; this.paused = true; this.state_version = 1; this.epoch = 1; this.seq = 0
    this.trains = CONFIG.trains.map((t) => ({ ...t, expected_arrival_s: t.scheduled_arrival_s, status: 'scheduled', track_id: null, movement: null,
      current_operation_id: null, wait_reason: null, forecast_departure_s: null, delay_s: 0, actual_departure_s: null }))
    this.tracks = CONFIG.tracks.map((t) => ({ id: t.id, kind: t.kind, usable_length_m: t.usable_length_m, availability: 'open', closed_until_s: null, occupant_train_id: null }))
    this.resources = CONFIG.resources.map((r) => ({ id: r.id, kind: r.kind, capabilities: r.capabilities, availability: 'available', unavailable_until_s: null, active_operation_id: null }))
    this.zones = { GW: null, GE: null }
    this.operations = this.trains.flatMap((t) => buildOperations(t))
    this.active_plan = null; this.plans = {}; this.exec_conflicts = {}; this.plan_conflicts = {}; this.samples = []; this.incidents = []
  }

  T(id: string) { return this.trains.find((t) => t.id === id) }
  TR(id: string) { return this.tracks.find((t) => t.id === id) }
  R(id: string) { return this.resources.find((r) => r.id === id) }
  opsOf(id: string) { return this.operations.filter((o) => o.train_id === id) }
  asg(opId: string): Asg | undefined { return this.active_plan?._by[opId] }

  planConflictsWithStarted(plan: Snap) {
    if (!this.active_plan) return []
    const nw: Dict<Asg> = Object.fromEntries(plan.assignments.map((a: Asg) => [a.operation_id, a]))
    const bad: string[] = []
    for (const op of this.operations) {
      if (op.status === 'pending') continue
      const o = this.asg(op.id), n = nw[op.id]
      if (o && n && (o.track_id !== n.track_id || o.route_id !== n.route_id || [...o.resource_ids].sort().join() !== [...n.resource_ids].sort().join())) bad.push(op.id)
    }
    return bad
  }

  applyPlan(plan: Snap) {
    const p = clone(plan)
    if (this.active_plan) {
      p.assignments = p.assignments.map((a: Asg) => {
        const op = this.operations.find((o) => o.id === a.operation_id)!
        const old = this.asg(op.id)
        return op.status !== 'pending' && old ? clone(old) : a
      })
    }
    p._by = Object.fromEntries(p.assignments.map((a: Asg) => [a.operation_id, a]))
    this.active_plan = p
    this.epoch++; this.state_version++
    this.recompute()
  }

  step() { this.sim_time_s++; return this.process() }

  process() {
    const t = this.sim_time_s
    let changed = false
    for (const op of this.operations) if (op.status === 'running' && op.actual_start_s! + op.duration_s <= t) { this.complete(op); changed = true }
    for (const tr of this.tracks) if (tr.closed_until_s && tr.closed_until_s <= t) { tr.availability = 'open'; tr.closed_until_s = null; changed = true }
    for (const r of this.resources) if (r.unavailable_until_s && r.unavailable_until_s <= t) { r.availability = 'available'; r.unavailable_until_s = null; changed = true }
    for (const tr of this.trains) if (tr.status === 'scheduled' && tr.expected_arrival_s <= t) { tr.status = 'waiting_entry'; changed = true }
    const cands: [number, string, Op, Asg | undefined][] = []
    for (const tr of this.trains) {
      if (tr.status === 'scheduled' || tr.status === 'departed') continue
      const ops = this.opsOf(tr.id)
      if (ops.some((o) => o.status === 'running')) continue
      const nxt = ops.find((o) => o.status === 'pending')
      if (!nxt) continue
      const a = this.asg(nxt.id)
      let at = a ? a.start_s : 0
      if (nxt.kind === 'departure') at = Math.max(at, tr.scheduled_departure_s - nxt.duration_s)
      if (t >= at) cands.push([at, tr.id, nxt, a])
    }
    cands.sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : 1))
    const nc: Dict<Snap> = {}
    for (const [, tid, op, a] of cands) {
      const [ok, code, x] = this.canStart(this.T(tid), op, a)
      if (ok) { this.start(this.T(tid), op, a!); changed = true }
      else {
        const txt = WAIT[code!](x ?? '')
        if (op.wait_reason !== txt) { op.wait_reason = txt; changed = true }
        const cid = `${code}:${op.id}`
        nc[cid] = { id: cid, code, severity: 'high', kind: 'execution', entity_ids: [tid, ...(x ? [x] : [])], operation_ids: [op.id],
          start_s: this.exec_conflicts[cid]?.start_s ?? t, end_s: null, message: `${tid}: ${OPL[op.kind]} ждёт — ${txt}` }
      }
    }
    if (Object.keys(nc).sort().join() !== Object.keys(this.exec_conflicts).sort().join()) changed = true
    this.exec_conflicts = nc
    this.sample()
    if (changed) { this.state_version++; this.recompute() }
    return changed
  }

  canStart(tr: Train, op: Op, a: Asg | undefined): [boolean, string | null, string | null] {
    if (!a) return [false, 'NO_FEASIBLE_SLOT', null]
    for (const rid of a.resource_ids) {
      const r = this.R(rid)
      if (r.availability !== 'available') return [false, 'RESOURCE_UNAVAILABLE', rid]
      if (r.active_operation_id) return [false, 'RESOURCE_BUSY', rid]
    }
    if (op.is_move) {
      const route = ROUTE_BY_ID[a.route_id!]
      for (const z of route.conflict_zone_ids) if (this.zones[z]) return [false, 'ROUTE_BUSY', z]
      if (op.is_move !== 'departure') {
        const dst = this.TR(route.to_id)
        if (dst.availability === 'closed') return [false, 'TRACK_CLOSED', dst.id]
        if (dst.occupant_train_id && dst.occupant_train_id !== tr.id) return [false, 'TRACK_OCCUPIED', dst.id]
      }
    }
    return [true, null, null]
  }

  start(tr: Train, op: Op, a: Asg) {
    const t = this.sim_time_s
    op.status = 'running'; op.actual_start_s = t; op.wait_reason = null
    for (const rid of a.resource_ids) this.R(rid).active_operation_id = op.id
    tr.current_operation_id = op.id
    if (op.is_move) {
      const route = ROUTE_BY_ID[a.route_id!]
      for (const z of route.conflict_zone_ids) this.zones[z] = op.id
      if (op.is_move !== 'departure') this.TR(route.to_id).occupant_train_id = tr.id
      tr.movement = { route_id: route.id, started_at_s: t, expected_end_at_s: t + op.duration_s }
      tr.status = 'moving'
    }
    this.seq++
  }

  complete(op: Op) {
    const t = op.actual_start_s! + op.duration_s
    op.status = 'completed'; op.actual_end_s = t
    const tr = this.T(op.train_id)
    const a = this.asg(op.id)
    for (const rid of a?.resource_ids ?? []) if (this.R(rid).active_operation_id === op.id) this.R(rid).active_operation_id = null
    tr.current_operation_id = null
    if (op.is_move && a) {
      const route = ROUTE_BY_ID[a.route_id!]
      for (const z of route.conflict_zone_ids) if (this.zones[z] === op.id) this.zones[z] = null
      if (tr.track_id) { const src = this.TR(tr.track_id); if (src.occupant_train_id === tr.id) src.occupant_train_id = null }
      tr.movement = null
      if (op.is_move === 'departure') { tr.status = 'departed'; tr.track_id = null; tr.actual_departure_s = t }
      else { tr.status = 'on_track'; tr.track_id = route.to_id }
    }
    this.seq++
  }

  incident(kind: string, target: string, duration = 600, delay = 300): [number, string, string] {
    kind = ({ delay_train: 'delay', locomotive_unavailable: 'loco_unavailable' } as Record<string, string>)[kind] ?? kind // имена контракта И
    const t = this.sim_time_s
    let msg = ''
    if (kind === 'delay') {
      const tr = this.T(target)
      if (!tr) return [422, 'NOT_FOUND', 'Поезд не найден']
      if (tr.status !== 'scheduled') return [409, 'INCOMPATIBLE', `${target} уже прибыл к станции — опоздание вносится только для scheduled`]
      tr.expected_arrival_s += delay
      msg = `Опоздание ${target} на ${delay} с`
    } else if (kind === 'close_track') {
      const tr = this.TR(target)
      if (!tr) return [422, 'NOT_FOUND', 'Путь не найден']
      for (const o of this.operations) {
        const a = this.asg(o.id)
        if (o.status === 'running' && o.is_move && a && ROUTE_BY_ID[a.route_id!].to_id === target)
          return [409, 'INCOMPATIBLE', `На ${target} уже идёт движение — закрытие в этот момент отклоняется`]
      }
      tr.availability = 'closed'; tr.closed_until_s = t + duration
      msg = `Закрыт ${target} до ${t + duration} с`
    } else if (kind === 'loco_unavailable') {
      const r = this.R(target)
      if (!r || r.kind !== 'shunting_loco') return [422, 'NOT_FOUND', 'Локомотив не найден']
      if (r.active_operation_id) return [409, 'INCOMPATIBLE', `${target} занят операцией — поломка в движении вне первой версии`]
      r.availability = 'unavailable'; r.unavailable_until_s = t + duration
      msg = `${target} недоступен до ${t + duration} с`
    } else return [422, 'BAD_KIND', 'Неизвестный вид сбоя']
    this.incidents.push({ kind, target_id: target, at_s: t, message: msg })
    this.epoch++; this.state_version++
    this.recompute()
    return [200, 'OK', msg]
  }

  recompute() {
    const t = this.sim_time_s
    for (const tr of this.trains) {
      let est = t
      let fc: number | null = null
      for (const op of this.opsOf(tr.id)) {
        if (op.status === 'completed') est = op.actual_end_s!
        else if (op.status === 'running') est = op.actual_start_s! + op.duration_s
        else {
          const a = this.asg(op.id)
          let s = Math.max(est, t, a ? a.start_s : t)
          if (op.kind === 'arrival') s = Math.max(s, tr.expected_arrival_s)
          if (op.kind === 'departure') s = Math.max(s, tr.scheduled_departure_s - op.duration_s)
          est = s + op.duration_s
        }
        if (op.kind === 'departure') fc = op.status === 'completed' ? op.actual_end_s : est
      }
      tr.forecast_departure_s = fc
      tr.delay_s = Math.max(0, (fc ?? 0) - tr.scheduled_departure_s)
      const nxt = this.opsOf(tr.id).find((o) => o.status !== 'completed')
      tr.wait_reason = nxt && nxt.status === 'pending' ? nxt.wait_reason : null
    }
    this.plan_conflicts = {}
    const closed: Dict<number> = {}
    for (const x of this.tracks) if (x.closed_until_s) closed[x.id] = x.closed_until_s
    const unav: Dict<number> = {}
    for (const x of this.resources) if (x.unavailable_until_s) unav[x.id] = x.unavailable_until_s
    for (const op of this.operations) {
      if (op.status !== 'pending') continue
      const a = this.asg(op.id)
      if (!a) continue
      if ((op.is_move === 'arrival' || op.is_move === 'shunt') && a.track_id && closed[a.track_id] && a.start_s < closed[a.track_id]) {
        const cid = `TRACK_CLOSED:${op.id}`
        if (!this.exec_conflicts[cid]) this.plan_conflicts[cid] = { id: cid, code: 'TRACK_CLOSED', severity: 'medium', kind: 'plan', entity_ids: [op.train_id, a.track_id], operation_ids: [op.id],
          start_s: a.start_s, end_s: closed[a.track_id], message: `${op.train_id}: по плану ${OPL[op.kind]} на ${a.track_id} в ${a.start_s} с, но путь закрыт до ${closed[a.track_id]} с` }
      }
      for (const rid of a.resource_ids) if (unav[rid] && a.start_s < unav[rid]) {
        const cid = `RESOURCE_UNAVAILABLE:${op.id}`
        if (!this.exec_conflicts[cid]) this.plan_conflicts[cid] = { id: cid, code: 'RESOURCE_UNAVAILABLE', severity: 'medium', kind: 'plan', entity_ids: [op.train_id, rid], operation_ids: [op.id],
          start_s: a.start_s, end_s: unav[rid], message: `${op.train_id}: по плану ${OPL[op.kind]} с ${rid} в ${a.start_s} с, но ${rid} недоступен до ${unav[rid]} с` }
      }
    }
  }

  sample() {
    const main = this.tracks.filter((x) => x.id !== 'P12')
    const open = main.filter((x) => x.availability === 'open')
    const occ = open.filter((x) => x.occupant_train_id).length
    const active = this.trains.filter((x) => ['waiting_entry', 'moving', 'on_track'].includes(x.status))
    const blocked = active.filter((x) => this.opsOf(x.id).some((o) => o.status === 'pending' && o.wait_reason)).length
    this.samples.push([occ, open.length, blocked, active.length])
    if (this.samples.length > INDEX_W) this.samples.shift()
  }

  index() {
    const t = this.sim_time_s
    const w0 = Math.max(0, t - INDEX_W)
    const p: Dict<number | null> = {}
    const delays: number[] = []
    for (const tr of this.trains) {
      if (tr.status === 'departed' && tr.actual_departure_s >= w0) delays.push(Math.max(0, tr.actual_departure_s - tr.scheduled_departure_s))
      else if (tr.status !== 'departed' && tr.scheduled_departure_s < t) delays.push(t - tr.scheduled_departure_s)
    }
    p.delay = delays.length ? Math.min(delays.reduce((a, b) => a + b, 0) / delays.length / 600, 1) : null
    const due = this.trains.filter((tr) => w0 <= tr.scheduled_departure_s && tr.scheduled_departure_s <= t)
    p.on_time = due.length ? 1 - due.filter((tr) => tr.status === 'departed' && tr.actual_departure_s <= tr.scheduled_departure_s).length / due.length : null
    if (this.samples.length) {
      const us = this.samples.map(([o, n]) => (n ? o / n : 1))
      const U = us.reduce((a, b) => a + b, 0) / us.length
      p.utilization = Math.min(Math.max((U - 0.75) / 0.25, 0), 1)
      const act = this.samples.reduce((a, s) => a + s[3], 0)
      p.idle = act ? Math.min(this.samples.reduce((a, s) => a + s[2], 0) / act, 1) : null
    } else { p.utilization = null; p.idle = null }
    p.conflicts = Math.min((Object.keys(this.exec_conflicts).length + Object.keys(this.plan_conflicts).length) / 5, 1)
    return indexFromPenalties(p, Math.min(INDEX_W, t), 'fact')
  }

  snapshot(): Snap {
    let ap = null
    if (this.active_plan) { const { _by, ...rest } = this.active_plan; void _by; ap = clone(rest) }
    return {
      schema_version: 1, run_id: this.run_id, state_version: this.state_version, epoch: this.epoch, last_seq: this.seq,
      sim_time_s: this.sim_time_s, speed: this.speed, paused: this.paused,
      trains: clone(this.trains), tracks: clone(this.tracks), resources: clone(this.resources),
      zones: Object.entries(this.zones).map(([id, v]) => ({ id, active_operation_id: v })),
      operations: clone(this.operations), active_plan_id: this.active_plan?.id ?? null, active_plan: ap,
      conflicts: [...Object.values(this.exec_conflicts), ...Object.values(this.plan_conflicts)],
      queue: this.trains.filter((t) => t.status === 'waiting_entry').map((t) => t.id),
      index: this.index(), incidents: this.incidents.slice(-10),
    }
  }
}
