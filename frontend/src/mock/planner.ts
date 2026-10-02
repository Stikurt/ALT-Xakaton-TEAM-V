// Порт mock_backend/app/planner.py: жадная эвристика с ресурсными календарями + validate_plan + прогноз индекса.
import { GROUPS, ROUTE_BY_ID, stagesFor, type Dict, type Op } from './model'

const INF = 1e9
const HORIZON_EXTRA = 4 * 7200

export interface Asg {
  operation_id: string; train_id: string; kind: string; start_s: number; end_s: number
  track_id: string | null; route_id: string | null; resource_ids: string[]; fixed: boolean
}
type Busy = [number, number, string]

class Calendar {
  busy: Dict<Busy[]> = {}
  closures: Dict<[number, number][]> = {}
  add(res: string, s: number, e: number, owner: string) {
    if (e > s) (this.busy[res] ??= []).push([s, e, owner])
  }
  removeOwner(res: string, owner: string) {
    this.busy[res] = (this.busy[res] ?? []).filter((b) => b[2] !== owner)
  }
  overlaps(res: string, s: number, e: number, owner: string) {
    return (this.busy[res] ?? []).filter((b) => b[2] !== owner && b[0] < e && s < b[1])
  }
  free(res: string, s: number, e: number, owner: string) {
    return this.overlaps(res, s, e, owner).length === 0
  }
  conflictEnd(res: string, s: number, e: number, owner: string) {
    const ov = this.overlaps(res, s, e, owner)
    return ov.length ? Math.max(...ov.map((b) => b[1])) : s
  }
  closureEnd(track: string, s: number) {
    for (const [cs, ce] of this.closures[track] ?? []) if (cs <= s && s < ce) return ce
    return null
  }
}

function findSlot(cal: Calendar, t: number, dur: number, singles: string[], groups: string[], owner: string, trackIn: string | null, limit: number): [number, string[]] | null {
  let s = t
  for (let i = 0; i < 400; i++) {
    if (s > limit) return null
    let nxt = s, ok = true
    const chosen: string[] = []
    for (const r of singles) if (!cal.free(r, s, s + dur, owner)) { ok = false; nxt = Math.max(nxt, cal.conflictEnd(r, s, s + dur, owner)) }
    if (trackIn) {
      const ce = cal.closureEnd(trackIn, s)
      if (ce !== null) { ok = false; nxt = Math.max(nxt, ce) }
      if (!cal.free(trackIn, s, s + dur, owner)) { ok = false; nxt = Math.max(nxt, cal.conflictEnd(trackIn, s, s + dur, owner)) }
    }
    for (const g of groups) {
      const members = GROUPS[g]
      const cand = members.filter((r) => cal.free(r, s, s + dur, owner))
      if (cand.length) chosen.push(cand[0])
      else { ok = false; nxt = Math.max(nxt, Math.min(...members.map((r) => cal.conflictEnd(r, s, s + dur, owner)))) }
    }
    if (ok) return [s, chosen]
    s = nxt > s ? nxt : s + 1
  }
  return null
}

const asg = (op: Op, s: number, track: string | null, route: string | null, res: string[], fixed = false): Asg => ({
  operation_id: op.id, train_id: op.train_id, kind: op.kind, start_s: s, end_s: s + op.duration_s,
  track_id: track, route_id: route, resource_ids: [...res], fixed,
})

class BudgetErr extends Error {}

type Solve = (i: number, t: number, prev: string | null, prevHs: number | null) => [Asg[], number] | null

function makeSolver(cal: Calendar, train: TrainLike, ops: Op[], limit: number, counter: { n: number }): Solve {
  const stages = stagesFor(train.kind)
  const owner = train.id
  const byStage: Dict<Op[]> = {}
  for (const op of ops) (byStage[op.stage] ??= []).push(op)
  const n = stages.length
  const solve: Solve = (i, t, prev, prevHs) => {
    if (++counter.n > 6000) throw new BudgetErr()
    if (i === n) {
      const dep = byStage[n][0]
      const t0 = Math.max(t, train.scheduled_departure_s - dep.duration_s)
      const r = findSlot(cal, t0, dep.duration_s, ['GE'], [], owner, null, limit)
      if (!r) return null
      const s = r[0]
      if (prev && !cal.free(prev, prevHs!, s + dep.duration_s, owner)) return null
      return [[asg(dep, s, prev, `R_${prev}_E`, [])], s + dep.duration_s]
    }
    const [mv, ...inner] = byStage[i]
    let best: [number, string, Asg[]] | null = null
    for (const X of stages[i].tracks) {
      if (X === prev || !ROUTE_BY_ID[`R_${prev ?? 'W'}_${X}`]) continue
      let tt = t
      for (let k = 0; k < 25; k++) {
        const r = findSlot(cal, tt, mv.duration_s, ['GW'], mv.groups, owner, X, limit)
        if (!r) break
        const [s, ch] = r
        if (prev && !cal.free(prev, prevHs!, s + mv.duration_s, owner)) break
        const route = `R_${prev ?? 'W'}_${X}`
        const asgs = [asg(mv, s, X, route, ch)]
        let cur = s + mv.duration_s, fail = false
        for (const op of inner) {
          const r2 = findSlot(cal, cur, op.duration_s, [], op.groups, owner, null, limit)
          if (!r2) { fail = true; break }
          asgs.push(asg(op, r2[0], X, null, r2[1]))
          cur = r2[0] + op.duration_s
        }
        if (fail) break
        const sub = solve(i + 1, cur, X, s)
        if (!sub) {
          const later = (cal.busy[X] ?? []).filter((b) => b[2] !== owner && b[0] >= s)
          if (!later.length) break
          tt = later.reduce((a, b) => (b[0] < a[0] ? b : a))[1]
          continue
        }
        const [subAsgs, depEnd] = sub
        const eOut = subAsgs[0].start_s + byStage[i + 1][0].duration_s
        if (!cal.free(X, s, eOut, owner)) { tt = cal.conflictEnd(X, s, eOut, owner); continue }
        if (!best || depEnd < best[0] || (depEnd === best[0] && X < best[1])) best = [depEnd, X, [...asgs, ...subAsgs]]
        break
      }
    }
    return best ? [best[2], best[0]] : null
  }
  return solve
}

export function holds(asgs: Asg[]): [string, number, number][] {
  const out: [string, number, number][] = []
  let cur: [string, number] | null = null
  for (const a of asgs) {
    if (a.kind === 'arrival' || a.kind === 'shunt' || a.kind === 'departure') {
      if (cur) out.push([cur[0], cur[1], a.end_s])
      cur = a.kind !== 'departure' ? [a.track_id!, a.start_s] : null
    }
  }
  if (cur) out.push([cur[0], cur[1], INF])
  return out
}

function book(cal: Calendar, tid: string, asgs: Asg[]) {
  for (const a of asgs) {
    for (const r of a.resource_ids) cal.add(r, a.start_s, a.end_s, tid)
    const route = a.route_id ? ROUTE_BY_ID[a.route_id] : null
    if (route) for (const z of route.conflict_zone_ids) cal.add(z, a.start_s, a.end_s, tid)
  }
  for (const [tr, s, e] of holds(asgs)) cal.add(tr, s, e, tid)
}

export interface TrainLike {
  id: string; kind: string; priority: number; scheduled_departure_s: number; expected_arrival_s: number
  status: string; track_id: string | null; movement: { route_id: string } | null
}
const occupied = (tr: TrainLike) => {
  const out = tr.track_id ? [tr.track_id] : []
  if (tr.movement) {
    const to = ROUTE_BY_ID[tr.movement.route_id].to_id
    if (to.startsWith('P')) out.push(to)
  }
  return out
}
const orderKey = (strategy: string, t: TrainLike): (number | string)[] =>
  strategy === 'passenger_first' ? [-t.priority, t.scheduled_departure_s, t.id] : [t.scheduled_departure_s, -t.priority, t.id]
const cmpKey = (a: (number | string)[], b: (number | string)[]) => {
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i] ? -1 : 1
  return 0
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Snap = any

export function plan(snapshot: Snap, strategy = 'passenger_first', budgetS = 2.0) {
  const t0 = performance.now()
  const now: number = snapshot.sim_time_s
  const limit = now + HORIZON_EXTRA
  const cal = new Calendar()
  for (const tr of snapshot.tracks) if (tr.closed_until_s && tr.closed_until_s > now) (cal.closures[tr.id] ??= []).push([now, tr.closed_until_s])
  for (const r of snapshot.resources) if (r.unavailable_until_s && r.unavailable_until_s > now) cal.add(r.id, now, r.unavailable_until_s, 'INCIDENT')
  const active: Dict<Asg> = Object.fromEntries(((snapshot.active_plan?.assignments ?? []) as Asg[]).map((a) => [a.operation_id, a]))
  const opsByTrain: Dict<Op[]> = {}
  for (const op of snapshot.operations as Op[]) (opsByTrain[op.train_id] ??= []).push(op)

  const fixedAsgs: Dict<Asg[]> = {}
  for (const tr of snapshot.trains as TrainLike[]) {
    const fx: Asg[] = []
    for (const op of opsByTrain[tr.id]) {
      if (op.status === 'running' || op.status === 'completed') {
        const a = { ...(active[op.id] ?? asg(op, op.actual_start_s!, tr.track_id, null, [])) }
        a.start_s = op.actual_start_s!
        a.end_s = op.actual_start_s! + op.duration_s
        a.fixed = true
        fx.push(a)
      }
    }
    fixedAsgs[tr.id] = fx
    for (const a of fx) {
      for (const r of a.resource_ids) cal.add(r, a.start_s, a.end_s, tr.id)
      const route = a.route_id ? ROUTE_BY_ID[a.route_id] : null
      if (route) for (const z of route.conflict_zone_ids) cal.add(z, a.start_s, a.end_s, tr.id)
    }
    if (tr.status !== 'departed') for (const x of occupied(tr)) cal.add(x, now, INF, tr.id)
  }
  const trains = snapshot.trains as TrainLike[]
  const sortBy = (l: TrainLike[]) => [...l].sort((a, b) => cmpKey(orderKey(strategy, a), orderKey(strategy, b)))
  const order = [...sortBy(trains.filter((t) => t.status === 'on_track' || t.status === 'moving')),
    ...sortBy(trains.filter((t) => t.status === 'scheduled' || t.status === 'waiting_entry'))]

  let assignments: Asg[] = []
  const unassigned: { train_id: string; code: string; message: string }[] = []
  let timedOut = false
  for (const tr of trains) if (tr.status === 'departed' || opsByTrain[tr.id].every((o) => o.status !== 'pending')) assignments.push(...fixedAsgs[tr.id])
  for (const tr of order) {
    const ops = opsByTrain[tr.id]
    const pending = ops.filter((o) => o.status === 'pending')
    if (!pending.length) continue
    if ((performance.now() - t0) / 1000 > budgetS) {
      timedOut = true
      unassigned.push({ train_id: tr.id, code: 'NO_FEASIBLE_SLOT', message: `${tr.id}: не хватило бюджета расчёта` })
      continue
    }
    const counter = { n: 0 }
    let res: [Asg[], number] | null = null
    try {
      const solve = makeSolver(cal, tr, ops, limit, counter)
      if (tr.status === 'scheduled' || tr.status === 'waiting_entry') {
        res = solve(0, Math.max(now, tr.expected_arrival_s), null, null)
      } else {
        const started = ops.filter((o) => o.status !== 'pending')
        const last = started[started.length - 1]
        const X = tr.status === 'on_track' ? tr.track_id! : ROUTE_BY_ID[active[last.id].route_id!].to_id
        const cs = last.stage
        const moveIn = ops.find((o) => o.stage === cs && o.is_move)!
        const hs = moveIn.actual_start_s!
        let cur = Math.max(now, last.actual_start_s! + last.duration_s)
        const asgs: Asg[] = []
        let ok = true
        for (const op of pending.filter((o) => o.stage === cs)) {
          const r2 = findSlot(cal, cur, op.duration_s, [], op.groups, tr.id, null, limit)
          if (!r2) { ok = false; break }
          asgs.push(asg(op, r2[0], X, null, r2[1]))
          cur = r2[0] + op.duration_s
        }
        if (ok) {
          const sub = solve(cs + 1, cur, X, hs)
          if (sub) res = [[...asgs, ...sub[0]], sub[1]]
        }
      }
    } catch (e) {
      if (!(e instanceof BudgetErr)) throw e
      res = null
    }
    if (!res) {
      unassigned.push({ train_id: tr.id, code: 'NO_FEASIBLE_SLOT', message: `${tr.id}: нет допустимой цепочки путей и ресурсов в горизонте` })
      assignments.push(...fixedAsgs[tr.id], ...pending.filter((o) => active[o.id]).map((o) => active[o.id]))
      continue
    }
    const full = [...fixedAsgs[tr.id], ...res[0]]
    for (const x of occupied(tr)) cal.removeOwner(x, tr.id)
    book(cal, tr.id, full)
    assignments.push(...full)
  }
  assignments = assignments.sort((a, b) => a.start_s - b.start_s || (a.operation_id < b.operation_id ? -1 : 1))
  const p: Snap = {
    id: 'PL-' + Math.random().toString(16).slice(2, 10), run_id: snapshot.run_id, based_on_version: snapshot.state_version,
    based_on_epoch: snapshot.epoch, based_on_time_s: now, strategy, timed_out: timedOut, assignments, unassigned, calc_ms: 0,
  }
  const violations = validatePlan(snapshot, p)
  p.violations = violations
  p.status = violations.length ? 'infeasible' : unassigned.length ? 'partial' : 'feasible'
  const [m, e] = metricsAndExplanations(snapshot, p, active)
  p.metrics = m
  p.explanations = e
  p.index_forecast = forecastIndex(snapshot, assignments, violations.length + unassigned.length)
  p.calc_ms = Math.round(performance.now() - t0)
  return p
}

export function validatePlan(snapshot: Snap, p: { assignments: Asg[] }) {
  const now: number = snapshot.sim_time_s
  const v: { code: string; operation_ids: string[]; message: string }[] = []
  const uses: Dict<[number, number, string, string][]> = {}
  const byTrain: Dict<Asg[]> = {}
  for (const a of p.assignments) {
    (byTrain[a.train_id] ??= []).push(a)
    if (!a.fixed && a.start_s < now) v.push({ code: 'PAST_START', operation_ids: [a.operation_id], message: 'Начало в прошлом' })
    for (const r of a.resource_ids) (uses[r] ??= []).push([a.start_s, a.end_s, a.train_id, a.operation_id])
    const route = a.route_id ? ROUTE_BY_ID[a.route_id] : null
    if (route) for (const z of route.conflict_zone_ids) (uses[z] ??= []).push([a.start_s, a.end_s, a.train_id, a.operation_id])
  }
  for (const [tid, lst] of Object.entries(byTrain)) {
    lst.sort((a, b) => a.start_s - b.start_s)
    for (let i = 1; i < lst.length; i++) if (lst[i].start_s < lst[i - 1].end_s)
      v.push({ code: 'PREDECESSOR_INCOMPLETE', operation_ids: [lst[i].operation_id], message: `${lst[i].operation_id} начинается до завершения предшественника` })
    for (const [tr, s, e] of holds(lst)) (uses['track:' + tr] ??= []).push([s, e, tid, tid])
  }
  const closed: Dict<number> = {}
  for (const t of snapshot.tracks) if (t.closed_until_s && t.closed_until_s > now) closed[t.id] = t.closed_until_s
  for (const a of p.assignments)
    if ((a.kind === 'arrival' || a.kind === 'shunt') && !a.fixed && a.track_id && closed[a.track_id] && a.start_s < closed[a.track_id])
      v.push({ code: 'TRACK_CLOSED', operation_ids: [a.operation_id], message: `Вход на закрытый ${a.track_id}` })
  const unav: Dict<number> = {}
  for (const r of snapshot.resources) if (r.unavailable_until_s && r.unavailable_until_s > now) unav[r.id] = r.unavailable_until_s
  for (const a of p.assignments) for (const r of a.resource_ids)
    if (!a.fixed && unav[r] && a.start_s < unav[r]) v.push({ code: 'RESOURCE_UNAVAILABLE', operation_ids: [a.operation_id], message: `${r} недоступен до ${unav[r]} с` })
  for (const [res, lst] of Object.entries(uses)) {
    lst.sort((a, b) => a[0] - b[0] || a[1] - b[1])
    for (let i = 1; i < lst.length; i++) {
      const x = lst[i - 1], y = lst[i]
      if (y[0] < x[1] && x[2] !== y[2]) {
        const code = res.startsWith('track:') ? 'TRACK_OCCUPIED' : res === 'GW' || res === 'GE' ? 'ROUTE_BUSY' : 'RESOURCE_UNAVAILABLE'
        v.push({ code, operation_ids: [x[3], y[3]], message: `Пересечение на ${res.replace('track:', '')}: ${x[2]} и ${y[2]}` })
      }
    }
  }
  return v
}

const depForecast = (asgs: Iterable<Asg>) => {
  const out: Dict<number> = {}
  for (const a of asgs) if (a.kind === 'departure') out[a.train_id] = a.end_s
  return out
}

function metricsAndExplanations(snapshot: Snap, p: Snap, active: Dict<Asg>) {
  const trains: Dict<TrainLike & { scheduled_departure_s: number }> = Object.fromEntries(snapshot.trains.map((t: TrainLike) => [t.id, t]))
  const dep = depForecast(p.assignments)
  const delays: Dict<number> = {}
  for (const [tid, end] of Object.entries(dep)) delays[tid] = Math.max(0, end - trains[tid].scheduled_departure_s)
  const oldDep = depForecast(Object.values(active))
  const now = snapshot.sim_time_s
  const closed: Dict<number> = {}
  for (const t of snapshot.tracks) if (t.closed_until_s && t.closed_until_s > now) closed[t.id] = t.closed_until_s
  const unav: Dict<number> = {}
  for (const r of snapshot.resources) if (r.unavailable_until_s && r.unavailable_until_s > now) unav[r.id] = r.unavailable_until_s
  const perTrain: Dict<[Asg, Asg][]> = {}
  let changed = 0
  for (const a of p.assignments as Asg[]) {
    const o = active[a.operation_id]
    if (a.fixed || !o) continue
    if (o.start_s !== a.start_s || o.track_id !== a.track_id || o.resource_ids.join() !== a.resource_ids.join()) {
      changed++
      ;(perTrain[a.train_id] ??= []).push([o, a])
    }
  }
  const expl = []
  for (const tid of Object.keys(perTrain).sort()) {
    const pairs = perTrain[tid]
    const parts: string[] = []
    let code = 'RESCHEDULED', reason = ''
    const moved = pairs.filter(([o, a]) => o.track_id !== a.track_id && (a.kind === 'arrival' || a.kind === 'shunt'))
    if (moved.length) {
      const [o, a] = moved[0]
      parts.push(`${tid} перенесён с ${o.track_id} на ${a.track_id}`)
      if (o.track_id && closed[o.track_id]) { code = 'TRACK_CLOSED'; reason = `, потому что ${o.track_id} закрыт до ${closed[o.track_id]} с` }
    }
    const resCh = pairs.filter(([o, a]) => [...o.resource_ids].sort().join() !== [...a.resource_ids].sort().join())
    if (resCh.length) {
      const [o, a] = resCh[0]
      parts.push(`${a.kind} ${tid}: ${o.resource_ids.join(',') || '—'} → ${a.resource_ids.join(',') || '—'}`)
      const bad = o.resource_ids.filter((r) => unav[r])
      if (bad.length && !reason) { code = 'RESOURCE_UNAVAILABLE'; reason = `, потому что ${bad[0]} недоступен до ${unav[bad[0]]} с` }
    }
    if (!parts.length) parts.push(`${tid}: сдвиг операций по времени`)
    const dOld = oldDep[tid], dNew = dep[tid]
    const tail = dOld !== undefined && dNew !== undefined && dNew !== dOld ? `; прогноз отправления ${dNew > dOld ? 'позже' : 'раньше'} на ${Math.abs(dNew - dOld)} с` : ''
    expl.push({ train_id: tid, code, operation_ids: pairs.map(([, a]) => a.operation_id), message: parts.join('; ') + reason + tail })
  }
  const vals = Object.values(delays)
  return [{
    total_delay_s: vals.reduce((a, b) => a + b, 0), max_delay_s: vals.length ? Math.max(...vals) : 0,
    unassigned_count: p.unassigned.length, changed_count: changed, delayed_trains: vals.filter((x) => x > 0).length,
    forecast_departures: dep, delays,
  }, expl] as const
}

// ---------- индекс ----------
export const INDEX_W = 900
const W: Dict<number> = { delay: 0.35, on_time: 0.25, utilization: 0.15, conflicts: 0.15, idle: 0.1 }
const LBL: Dict<string> = { delay: 'Задержка отправления', on_time: 'Выполнение отправлений', utilization: 'Перегрузка путей', conflicts: 'Конфликты плана', idle: 'Простой из-за ожидания' }
const r1 = (x: number) => Math.round(x * 10) / 10

export function indexFromPenalties(p: Dict<number | null>, windowS: number, kind: string) {
  const have = Object.entries(p).filter(([, v]) => v !== null) as [string, number][]
  if (!have.length) return null
  const wsum = have.reduce((a, [k]) => a + W[k], 0)
  const hv = Object.fromEntries(have)
  const factors = Object.keys(W).map((k) => k in hv
    ? { id: k, label: LBL[k], penalty: Math.round(hv[k] * 1000) / 1000, weight: W[k], contribution: r1((100 * W[k] / wsum) * hv[k]) }
    : { id: k, label: LBL[k], penalty: null, weight: W[k], contribution: null, no_data: true })
  const value = Math.round(100 - factors.reduce((a, f) => a + (f.contribution ?? 0), 0))
  return { value, category: value >= 80 ? 'norm' : value >= 50 ? 'attention' : 'critical', window_s: windowS, factors, kind }
}

export function forecastIndex(snapshot: Snap, assignments: Asg[], conflicts: number) {
  const now: number = snapshot.sim_time_s
  const w1 = now + INDEX_W
  const dep = depForecast(assignments)
  const arr: Dict<number> = {}
  for (const a of assignments) if (a.kind === 'arrival') arr[a.train_id] = a.start_s
  const delays: number[] = []
  let due = 0, ok = 0
  for (const t of snapshot.trains) {
    if (t.status === 'departed') continue
    const d = dep[t.id], sched = t.scheduled_departure_s
    if (d === undefined) { if (sched < w1) { delays.push(w1 - sched); due++ } continue }
    if ((now <= d && d < w1) || sched < w1) delays.push(Math.max(0, Math.min(d, w1) - sched))
    if (now <= sched && sched < w1) { due++; if (d <= sched) ok++ }
  }
  const p: Dict<number | null> = {
    delay: delays.length ? Math.min(delays.reduce((a, b) => a + b, 0) / delays.length / 600, 1) : null,
    on_time: due ? 1 - ok / due : null,
  }
  const byTrain: Dict<Asg[]> = {}
  for (const a of assignments) (byTrain[a.train_id] ??= []).push(a)
  const hs: [string, number, number][] = []
  for (const l of Object.values(byTrain)) hs.push(...holds([...l].sort((a, b) => a.start_s - b.start_s)))
  const closed: Dict<number> = {}
  for (const t of snapshot.tracks) if (t.closed_until_s) closed[t.id] = t.closed_until_s
  const main = snapshot.tracks.map((t: { id: string }) => t.id).filter((x: string) => x !== 'P12')
  const us: number[] = []
  let blocked = 0, activeN = 0
  for (let ts = now; ts < w1; ts += 30) {
    const open = main.filter((x: string) => !(closed[x] && ts < closed[x]))
    const occ = new Set(hs.filter(([tr, s, e]) => s <= ts && ts < e && open.includes(tr)).map(([tr]) => tr))
    us.push(open.length ? occ.size / open.length : 1)
    for (const t of snapshot.trains) {
      if (t.status === 'departed') continue
      const d = dep[t.id]
      if (t.expected_arrival_s <= ts && (d === undefined || ts < d)) {
        activeN++
        if ((t.status === 'scheduled' || t.status === 'waiting_entry') && (arr[t.id] === undefined || ts < arr[t.id])) blocked++
      }
    }
  }
  const U = us.length ? us.reduce((a, b) => a + b, 0) / us.length : 0
  p.utilization = Math.min(Math.max((U - 0.75) / 0.25, 0), 1)
  p.idle = activeN ? Math.min(blocked / activeN, 1) : null
  p.conflicts = Math.min(conflicts / 5, 1)
  return indexFromPenalties(p, INDEX_W, 'forecast')
}

export function baselineForecast(snapshot: Snap) {
  const ap = snapshot.active_plan
  if (!ap) return null
  const v = validatePlan(snapshot, { assignments: ap.assignments }).filter((x) => x.code !== 'PAST_START')
  return forecastIndex(snapshot, ap.assignments, (snapshot.conflicts?.length ?? 0) + v.length)
}
