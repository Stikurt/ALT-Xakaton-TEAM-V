// Демо-сервер в браузере: те же маршруты и WS-конверты, что у mock_backend/app/main.py.
// Подключается только в сборке VITE_MOCK=1 (страница-демо без backend).
import type { SocketLike, Transport } from '../api/transport'
import { topology, type Dict } from './model'
import { baselineForecast, plan as runPlanner, type Snap } from './planner'
import { Station } from './sim'

const STRATEGIES = ['passenger_first', 'earliest_departure']
const TICK = 50
const USERS: Dict<{ role: string; pw: string }> = {
  dispatcher: { role: 'dispatcher', pw: 'dispatcher' }, admin: { role: 'admin', pw: 'admin' }, viewer: { role: 'viewer', pw: 'viewer' },
}
const RANK: Dict<number> = { viewer: 0, dispatcher: 1, admin: 2 }

class Hub {
  st = new Station()
  clients = new Map<FakeSocket, number>()
  cmd = new Map<string, [number, unknown]>()
  acc = 0
  lastPub = -1
  history: Snap[] = []
  user: { username: string; role: string } | null = null
  jobRunning = false
  jobPending = false
  lastTick = performance.now()

  constructor() {
    this.initialPlan()
    this.remember()
    setInterval(() => this.loop(), TICK)
    setInterval(() => this.broadcast('clock_sync', this.clock()), 1000)
  }
  initialPlan() {
    const snap = this.st.snapshot()
    let best: [number[], Snap] | null = null
    for (const s of STRATEGIES) {
      const p = runPlanner(snap, s)
      const key = [p.status !== 'feasible' ? 1 : 0, p.metrics.unassigned_count, p.metrics.total_delay_s]
      const less = (a: number[], b: number[]) => { for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return a[i] < b[i]; return false }
      if (!best || less(key, best[0])) best = [key, p]
    }
    this.st.plans[best![1].id] = best![1]
    this.st.applyPlan(best![1])
  }
  remember() {
    const s = this.st.snapshot()
    this.history.push(s)
    while (this.history.length && (this.history[0].run_id !== s.run_id || this.history[0].sim_time_s < s.sim_time_s - 960)) this.history.shift()
  }
  clock() {
    return { sim_time_s: this.st.sim_time_s, sim_time_exact: this.st.sim_time_s + this.acc, speed: this.st.speed, paused: this.st.paused }
  }
  send(ws: FakeSocket, type: string, payload: unknown) {
    const seq = (this.clients.get(ws) ?? 0) + 1
    this.clients.set(ws, seq)
    ws.deliver(JSON.stringify({ schema_version: 1, run_id: this.st.run_id, ws_seq: seq, state_version: this.st.state_version, type, payload }))
  }
  broadcast(type: string, payload: unknown) {
    for (const ws of this.clients.keys()) this.send(ws, type, payload)
  }
  publish() {
    this.lastPub = this.st.state_version
    if (this.clients.size) this.broadcast('state_updated', { snapshot: this.st.snapshot() })
  }
  loop() {
    const now = performance.now()
    const dt = Math.min((now - this.lastTick) / 1000, 0.5)
    this.lastTick = now
    if (!this.st.paused) {
      this.acc += dt * this.st.speed
      while (this.acc >= 1) {
        this.acc -= 1
        this.st.step()
        if (this.st.sim_time_s % 5 === 0) this.remember()
      }
    }
    if (this.st.state_version !== this.lastPub) this.publish()
  }
  requestReplan() {
    const id = 'JOB-' + Math.random().toString(16).slice(2, 8)
    if (this.jobRunning) { this.jobPending = true; return id }
    this.runJob(id)
    return id
  }
  runJob(id: string) {
    this.jobRunning = true
    this.broadcast('replan_started', { job_id: id })
    const snap = this.st.snapshot()
    setTimeout(() => {
      try {
        const t0 = performance.now()
        const baseline = baselineForecast(snap)
        const plans = STRATEGIES.map((s) => runPlanner(snap, s, 2))
        if (snap.run_id === this.st.run_id) {
          const key = (p: Snap) => p.assignments.map((a: Snap) => `${a.operation_id}|${a.start_s}|${a.track_id}|${a.resource_ids}`).sort().join()
          const same = key(plans[0]) === key(plans[1])
          for (const p of plans) { p.identical_to_other = same; this.st.plans[p.id] = p }
          this.broadcast('replan_finished', { job_id: id, plan_ids: plans.map((p) => p.id), identical: same,
            calc_ms: Math.round(performance.now() - t0), plans, baseline_index: baseline, based_on_time_s: snap.sim_time_s })
        }
      } catch (e) {
        this.broadcast('replan_failed', { job_id: id, message: String(e) })
      } finally {
        this.jobRunning = false
        if (this.jobPending) { this.jobPending = false; this.runJob('JOB-' + Math.random().toString(16).slice(2, 8)) }
      }
    }, 30)
  }
}

let hub: Hub | null = null
const H = () => (hub ??= new Hub())

type Res = { status: number; text: string }
const json = (status: number, body: unknown): Res => ({ status, text: JSON.stringify(body) })
const err = (status: number, code: string, message: string, details: unknown = {}) => json(status, { code, message, details })

function need(role: string): Res | null {
  const u = H().user
  if (!u) return err(401, 'UNAUTHORIZED', 'Нужен вход в систему')
  if (RANK[u.role] < RANK[role]) return err(403, 'FORBIDDEN', `Недостаточно прав: нужна роль «${role}»`)
  return null
}

function route(method: string, path: string, body: Dict<any>): Res { // eslint-disable-line @typescript-eslint/no-explicit-any
  const hub = H(), st = hub.st
  const [p, qs] = path.split('?')
  const q = Object.fromEntries(new URLSearchParams(qs ?? ''))
  const idem = body?.command_id ? hub.cmd.get(body.command_id) : undefined
  if (idem) return json(idem[0], idem[1])
  const remember = (status: number, content: unknown) => {
    if (body?.command_id) hub.cmd.set(body.command_id, [status, content])
    return json(status, content)
  }
  const checkRun = () => (body?.run_id !== st.run_id ? err(409, 'STALE_RUN', 'Команда относится к другому запуску', { current_run_id: st.run_id }) : null)

  if (p === '/api/login' && method === 'POST') {
    const u = USERS[String(body.username ?? '')]
    if (!u || u.pw !== String(body.password ?? '')) return err(401, 'BAD_CREDENTIALS', 'Неверное имя пользователя или пароль')
    hub.user = { username: body.username, role: u.role }
    return json(200, hub.user)
  }
  if (p === '/api/logout') { hub.user = null; return json(200, { ok: true }) }
  if (p === '/api/me') return hub.user ? json(200, hub.user) : err(401, 'UNAUTHORIZED', 'Нужен вход в систему')
  if (p === '/health') return json(200, { status: 'ok', db: 'browser demo', run_id: st.run_id })
  let e: Res | null
  if (p === '/api/state') {
    if ((e = need('viewer'))) return e
    return json(200, { schema_version: 1, snapshot: st.snapshot(), topology: topology(), clock: hub.clock() })
  }
  if (p === '/api/history') {
    if ((e = need('viewer'))) return e
    if (q.run_id !== st.run_id) return err(409, 'STALE_RUN', 'История доступна только для текущего запуска')
    const at = Number(q.at_s)
    const c = hub.history.filter((h) => h.sim_time_s <= at)
    if (!c.length) return err(404, 'NO_HISTORY', 'Нет сохранённого состояния на этот момент')
    return json(200, { schema_version: 1, snapshot: c[c.length - 1], requested_at_s: at, available_from_s: hub.history[0].sim_time_s, available_to_s: st.sim_time_s })
  }
  if (p === '/api/export.csv') {
    if ((e = need('viewer'))) return e
    const safe = (v: unknown) => { const s = v === null || v === undefined ? '' : String(v); return /^[=+\-@\t\r]/.test(s) ? "'" + s : s }
    const rows = [['run_id', 'sim_time_s', 'train_id', 'kind', 'status', 'scheduled_departure_s', 'actual_departure_s', 'forecast_departure_s', 'delay_s', 'completed_operations', 'track_id']]
    for (const t of st.trains) rows.push([st.run_id, st.sim_time_s, t.id, t.kind, t.status, t.scheduled_departure_s, t.actual_departure_s, t.forecast_departure_s, t.delay_s,
      st.opsOf(t.id).filter((o) => o.status === 'completed').map((o) => o.kind).join(' '), t.track_id].map(safe))
    const dep = st.trains.filter((t) => t.status === 'departed').map((t) => t.delay_s as number)
    rows.push([], ['departed', String(dep.length)], ['avg_delay_s', String(dep.length ? Math.round(dep.reduce((a, b) => a + b, 0) / dep.length) : 0)],
      ['max_delay_s', String(dep.length ? Math.max(...dep) : 0)], ['conflicts_now', String(Object.keys(st.exec_conflicts).length + Object.keys(st.plan_conflicts).length)],
      ['index', String(st.index()?.value ?? '')])
    return { status: 200, text: '﻿' + rows.map((r) => r.join(';')).join('\n') + '\n' }
  }
  if (p === '/api/simulation/control') {
    if ((e = need('dispatcher'))) return e
    if (body.action !== 'reset' && (e = checkRun())) return e
    if (body.action === 'start') {
      if (st.sim_time_s === 0 && st.paused) { st.paused = false; st.process() }
      st.paused = false
      hub.lastTick = performance.now()
    } else if (body.action === 'pause') st.paused = true
    else if (body.action === 'speed') {
      if (![1, 5, 10].includes(body.speed)) return err(422, 'BAD_SPEED', 'Скорость: 1, 5 или 10')
      st.speed = body.speed
    } else if (body.action === 'reset') {
      st.reset(); hub.acc = 0; hub.initialPlan(); hub.history = []; hub.remember()
      hub.broadcast('snapshot', { snapshot: st.snapshot(), topology: topology() })
    } else return err(422, 'BAD_ACTION', 'Неизвестное действие')
    hub.broadcast('clock_sync', hub.clock())
    hub.publish()
    return remember(200, { ok: true, run_id: st.run_id, state_version: st.state_version })
  }
  if (p === '/api/incidents') {
    if ((e = need('dispatcher')) || (e = checkRun())) return e
    const [status, code, msg] = st.incident(body.kind, body.target_id, Number(body.duration_s || 600), Number(body.delay_s || 300))
    if (status !== 200) return remember(status, { code, message: msg, details: {} })
    hub.remember()
    const job = hub.requestReplan()
    hub.publish()
    return remember(200, { ok: true, results: [{ status, code, message: msg, target_id: body.target_id }], job_id: job, state_version: st.state_version })
  }
  if (p === '/api/replans') {
    if ((e = need('dispatcher')) || (e = checkRun())) return e
    return json(202, { job_id: hub.requestReplan() })
  }
  const mApply = p.match(/^\/api\/plans\/([^/]+)\/apply$/)
  if (mApply) {
    if ((e = need('dispatcher')) || (e = checkRun())) return e
    const pl = st.plans[mApply[1]]
    if (!pl) return err(404, 'NOT_FOUND', 'План не найден')
    if (pl.run_id !== st.run_id) return err(409, 'STALE_PLAN', 'План относится к старому запуску')
    if (pl.based_on_epoch !== st.epoch) return err(409, 'STALE_PLAN', 'После расчёта изменилась обстановка (сбой или другой план). Нужен пересчёт.')
    if (pl.status !== 'feasible') return err(409, 'PLAN_NOT_FEASIBLE', 'Неполный или недопустимый план нельзя принять обычной кнопкой')
    const bad = st.planConflictsWithStarted(pl)
    if (bad.length) return err(409, 'STALE_PLAN', 'Пока план рассматривался, операции начались иначе. Нужен пересчёт.', { operation_ids: bad })
    st.applyPlan(pl)
    hub.remember()
    hub.publish()
    return remember(200, { ok: true, active_plan_id: pl.id, state_version: st.state_version })
  }
  const mPlan = p.match(/^\/api\/plans\/([^/]+)$/)
  if (mPlan) return st.plans[mPlan[1]] ? json(200, st.plans[mPlan[1]]) : err(404, 'NOT_FOUND', 'План не найден')
  return err(404, 'NOT_FOUND', 'Нет такого маршрута')
}

class FakeSocket implements SocketLike {
  onopen: (() => void) | null = null
  onmessage: ((ev: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  constructor() {
    setTimeout(() => {
      const hub = H()
      if (!hub.user) { this.close(); return }
      this.onopen?.()
      hub.clients.set(this, 0)
      hub.send(this, 'snapshot', { snapshot: hub.st.snapshot(), topology: topology() })
      hub.send(this, 'clock_sync', hub.clock())
    }, 20)
  }
  deliver(data: string) {
    if (!this.closed) queueMicrotask(() => !this.closed && this.onmessage?.({ data }))
  }
  close() {
    if (this.closed) return
    this.closed = true
    H().clients.delete(this)
    setTimeout(() => this.onclose?.(), 0)
  }
}

export const demoTransport: Transport = {
  demo: true,
  request: async (method, path, body) => {
    await new Promise((r) => setTimeout(r, 15))
    return route(method, path, (body ?? {}) as Dict)
  },
  socket: () => new FakeSocket(),
}
