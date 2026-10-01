// Общий store (владелец — К). StationView получает данные только отсюда через props.
import { create } from 'zustand'
import { api, HttpError, type User } from '../api/client'
import { connectWs, type WsStatus } from '../api/ws'
import { normalizePlan, normalizeSnapshot, normalizeState } from '../api/adapt'
import type { ClockSync, IncidentCmd, Plan, Snapshot, StationIndex, Topology, WsEnvelope } from '../api/types'

export type Selection =
  | { type: 'train'; id: string }
  | { type: 'track'; id: string }
  | { type: 'resource'; id: string }
  | { type: 'conflict'; id: string }
  | null
export type Nav = 'overview' | 'trains' | 'resources' | 'history'
export type Role = User['role']

export interface Toast { id: number; kind: 'ok' | 'error' | 'info'; text: string }
export interface ReplanState {
  status: 'idle' | 'running' | 'done' | 'failed'
  job_id: string | null
  plans: Plan[]
  identical: boolean
  calc_ms: number | null
  error: string | null
  finished_at_epoch: number | null
  baseline: StationIndex | null
}
export interface HistoryView {
  at_s: number
  snapshot: Snapshot | null
  loading: boolean
  from_s: number
  to_s: number
  error: string | null
}

export interface ApDecision {
  id: number
  at_sim: number
  at_real: number
  kind: 'apply' | 'keep' | 'skip' | 'replan' | 'error' | 'info' | 'advice'
  title: string
  reasons: string[]
  plan_id?: string
}
export interface JEvent { id: number; t: number; kind: 'arrive' | 'enter' | 'depart' | 'incident' | 'plan' | 'conflict' | 'resolved' | 'run'; text: string; train?: string; bad?: boolean }
export type AssistantMode = 'off' | 'advise' | 'auto'
export interface Tip {
  key: string
  severity: 'info' | 'warn' | 'bad'
  text: string
  action?: { kind: 'replan' } | { kind: 'select'; target: Selection }
}
export interface Advice { plan_id: string; job_id: string; epoch: number; title: string; reasons: string[]; at_sim: number }
export interface AutopilotState {
  mode: AssistantMode
  /** true, когда ИИ сам принимает решения (режим «Автопилот») */
  enabled: boolean
  tips: Tip[]
  advice: Advice | null
  status: 'off' | 'watching' | 'replanning' | 'deciding' | 'applying' | 'paused'
  note: string
  log: ApDecision[]
  applied: number
}

interface ClockBase { sim: number; perf: number; speed: number; paused: boolean }

interface State {
  auth: 'checking' | 'anonymous' | 'signed_in'
  user: User | null
  authError: string | null
  noAuthBackend: boolean
  httpPolling: boolean
  topology: Topology | null
  snapshot: Snapshot | null
  loadError: string | null
  conn: WsStatus
  lastUpdate: number | null
  clock: ClockBase
  selection: Selection
  nav: Nav
  replan: ReplanState
  previewPlanId: string | null
  compareOpen: boolean
  incidentOpen: boolean
  csv: { open: boolean; text: string | null; loading: boolean }
  helpOpen: boolean
  history: HistoryView | null
  pending: string | null
  toasts: Toast[]
  latency: { last_ms: number | null }
  autopilot: AutopilotState
  indexTrend: { t: number; v: number }[]
  journal: JEvent[]

  boot: () => () => void
  login: (u: string, p: string) => Promise<void>
  logout: () => Promise<void>
  select: (s: Selection) => void
  setNav: (n: Nav) => void
  control: (action: 'start' | 'pause' | 'speed' | 'reset', speed?: number) => Promise<void>
  incident: (cmd: IncidentCmd | IncidentCmd[]) => Promise<boolean>
  requestReplan: () => Promise<void>
  applyPlan: (id: string) => Promise<void>
  setPreview: (id: string | null) => void
  setCompareOpen: (v: boolean) => void
  setIncidentOpen: (v: boolean) => void
  setHelpOpen: (v: boolean) => void
  openCsv: () => Promise<void>
  closeCsv: () => void
  openHistory: (at_s?: number) => void
  seekHistory: (at_s: number) => void
  closeHistory: () => void
  toast: (kind: Toast['kind'], text: string) => void
  dismissToast: (id: number) => void
  setAutopilot: (enabled: boolean) => void
  setAssistantMode: (m: AssistantMode) => void
  apUpdate: (patch: Partial<AutopilotState>) => void
  apLog: (d: Omit<ApDecision, 'id' | 'at_real' | 'at_sim'>) => void
}

const emptyReplan: ReplanState = {
  status: 'idle', job_id: null, plans: [], identical: false, calc_ms: null, error: null, finished_at_epoch: null, baseline: null,
}
const HISTORY_SPAN = 15 * 60
let toastSeq = 0
let stopWs: (() => void) | null = null
let historySeq = 0
let historyTimer: number | undefined
let activePlanCache: Plan | null = null
let pendingPlanId: string | null = null

const samePlan = (a: Plan, b: Plan) => {
  const k = (p: Plan) => p.assignments.map((x) => `${x.operation_id}|${x.start_s}|${x.track_id}|${x.resource_ids.join()}`).sort().join(';')
  return k(a) === k(b)
}

export const useStore = create<State>((set, get) => {
  const applySnapshot = (snap: Snapshot, topo?: Topology) => {
    const prev = get().snapshot
    if (!snap.active_plan && snap.active_plan_id) {
      // backend И отдаёт только active_plan_id — подставляем ранее загруженный план или загружаем его
      const cached = activePlanCache?.id === snap.active_plan_id ? activePlanCache : null
      if (cached) snap = { ...snap, active_plan: cached }
      else if (pendingPlanId !== snap.active_plan_id) {
        pendingPlanId = snap.active_plan_id
        api.plan(snap.active_plan_id, snap.operations).then((p) => {
          activePlanCache = p
          const cur = get().snapshot
          if (cur && cur.active_plan_id === p.id) set({ snapshot: { ...cur, active_plan: p } })
        }, () => { pendingPlanId = null })
      }
    }
    const runChanged = prev && prev.run_id !== snap.run_id
    set((s) => {
      const trend = runChanged ? [] : s.indexTrend
      const last = trend[trend.length - 1]
      const nextTrend = snap.index && (!last || snap.sim_time_s - last.t >= 5)
        ? [...trend.filter((p) => p.t >= snap.sim_time_s - 900), { t: snap.sim_time_s, v: snap.index.value }]
        : trend
      return {
      indexTrend: nextTrend,
      journal: runChanged || !prev ? (prev ? [{ id: ++toastSeq, t: snap.sim_time_s, kind: 'run' as const, text: `Новый запуск ${snap.run_id}` }] : []) : [...diffEvents(prev, snap), ...s.journal].slice(0, 300),
      snapshot: snap,
      topology: topo ?? s.topology,
      lastUpdate: Date.now(),
      ...(runChanged
        ? { replan: emptyReplan, previewPlanId: null, compareOpen: false, selection: null, history: null, nav: s.nav === 'history' ? 'overview' : s.nav }
        : {}),
      }
    })
    syncClock({ sim_time_s: snap.sim_time_s, speed: snap.speed, paused: snap.paused })
  }

  const syncClock = (c: ClockSync) => {
    const now = performance.now()
    const { clock } = get()
    const est = estimateSim(clock, now)
    const exact = c.sim_time_exact
    let sim: number
    if (exact !== undefined) sim = exact
    else if (c.paused || Math.abs(est - c.sim_time_s) > 1.5 || est < c.sim_time_s) sim = c.sim_time_s
    else sim = est
    set({ clock: { sim, perf: now, speed: c.speed, paused: c.paused } })
  }

  const refetch = async () => {
    try {
      const r = await api.state()
      applySnapshot(r.snapshot, r.topology)
      syncClock(r.clock)
      set({ loadError: null })
    } catch (e) {
      if (e instanceof HttpError && e.status === 401) {
        set({ auth: 'anonymous', user: null })
        return
      }
      set({ loadError: e instanceof Error ? e.message : String(e) })
    }
  }

  const startSession = () => {
    stopWs?.()
    void refetch()
    let offlineSince: number | null = null
    // Если WebSocket недоступен (у backend ещё нет /ws), раз в 2 с читаем снимок по HTTP. Команды остаются заблокированы.
    const tick = window.setInterval(() => {
      const st = get()
      if (st.conn === 'online') {
        offlineSince = null
        if (st.httpPolling) set({ httpPolling: false })
        return
      }
      offlineSince ??= Date.now()
      if (Date.now() - offlineSince > 3000) {
        if (!st.httpPolling) set({ httpPolling: true })
        void refetch()
      }
    }, 2000)
    const stopInner = connectWs({
      onMessage: onWs,
      onStatus: (s) => {
        if (s === 'offline') {
          const { clock } = get()
          set({ clock: { ...clock, sim: estimateSim(clock, performance.now()), perf: performance.now(), paused: true } })
        }
        set({ conn: s })
      },
      onGap: () => void refetch(),
    })
    stopWs = () => {
      stopInner()
      window.clearInterval(tick)
      set({ httpPolling: false })
    }
  }

  const onWs = (m: WsEnvelope) => {
    const snap = get().snapshot
    if (snap && m.run_id !== snap.run_id && m.type !== 'snapshot') {
      void refetch()
      return
    }
    switch (m.type) {
      case 'snapshot': {
        const n = normalizeState(m.payload)
        applySnapshot(n.snapshot, n.topology)
        break
      }
      case 'state_updated':
        applySnapshot(normalizeSnapshot((m.payload as { snapshot: unknown }).snapshot, get().topology))
        break
      case 'clock_sync':
        syncClock(m.payload as ClockSync)
        break
      case 'replan_started':
        set((s) => ({ replan: { ...s.replan, status: 'running', job_id: (m.payload as { job_id: string }).job_id, error: null } }))
        break
      case 'replan_finished': {
        const p = m.payload as {
          job_id: string; plan_ids: string[]; plans?: unknown[]; identical?: boolean; calc_ms?: number
          baseline_index?: StationIndex | null; stale?: boolean
        }
        void (async () => {
          const ops = get().snapshot?.operations
          // backend И присылает только plan_ids — варианты читаются через GET /api/plans/{id}
          let plans: Plan[]
          try {
            plans = p.plans ? p.plans.map((x) => normalizePlan(x, ops)) : await Promise.all(p.plan_ids.map((id) => api.plan(id, ops)))
            if (!plans.length || plans.some((x) => !x.id)) throw new Error('empty plan variants')
          } catch {
            set((s) => ({ replan: { ...s.replan, status: 'failed', error: 'Не удалось получить варианты плана' } }))
            get().toast('error', 'Расчёт завершён, но варианты не загрузились. Нажмите «Пересчитать».')
            return
          }
          const identical = p.identical ?? (plans.length === 2 && samePlan(plans[0], plans[1]))
          set({
            replan: {
              status: 'done', job_id: p.job_id, plans, identical, calc_ms: p.calc_ms ?? null, error: null,
              finished_at_epoch: p.stale ? -999 : plans[0]?.based_on_epoch ?? null, baseline: p.baseline_index ?? null,
            },
            compareOpen: get().history || get().autopilot.mode !== 'off' ? get().compareOpen : true,
          })
        })()
        break
      }
      case 'replan_failed': {
        const msg = (m.payload as { message?: string }).message ?? 'причина не указана'
        set((s) => ({ replan: { ...s.replan, status: 'failed', error: msg } }))
        get().toast('error', 'Пересчёт не удался: ' + msg)
        break
      }
      case 'simulation_error':
        get().toast('error', 'Симуляция остановлена: ' + ((m.payload as { message?: string }).message ?? 'ошибка сервера'))
        break
    }
  }

  const guarded = async <T,>(label: string, fn: (run_id: string) => Promise<T>): Promise<T | null> => {
    const { snapshot, conn, user, history } = get()
    if (history) {
      get().toast('error', 'Вы смотрите историю. Вернитесь в онлайн, чтобы управлять станцией.')
      return null
    }
    if (!snapshot || conn !== 'online') {
      get().toast('error', 'Нет связи с сервером. Команды недоступны, пока связь не восстановится.')
      return null
    }
    if (user?.role === 'viewer') {
      get().toast('error', 'Роль «Наблюдатель» только просматривает станцию.')
      return null
    }
    set({ pending: label })
    const t0 = performance.now()
    try {
      const r = await fn(snapshot.run_id)
      set({ latency: { last_ms: Math.round(performance.now() - t0) } })
      return r
    } catch (e) {
      if (e instanceof HttpError) {
        get().toast('error', e.body.message)
        if (e.body.code === 'STALE_RUN') void refetch()
        if (e.status === 401) set({ auth: 'anonymous', user: null })
      } else get().toast('error', 'Сервер не отвечает. Проверьте, что backend запущен.')
      return null
    } finally {
      set({ pending: null })
    }
  }

  const loadHistory = (at_s: number) => {
    const snap = get().snapshot
    if (!snap) return
    const seq = ++historySeq
    window.clearTimeout(historyTimer)
    historyTimer = window.setTimeout(async () => {
      try {
        const r = await api.history(snap.run_id, at_s)
        if (seq !== historySeq || !get().history) return
        set((s) => ({ history: s.history && { ...s.history, at_s: r.snapshot.sim_time_s, snapshot: r.snapshot, loading: false, error: null, from_s: r.available_from_s, to_s: r.available_to_s } }))
      } catch (e) {
        if (seq !== historySeq) return
        set((s) => ({ history: s.history && { ...s.history, loading: false, error: e instanceof HttpError ? e.body.message : 'Нет связи' } }))
      }
    }, 120)
  }

  return {
    auth: 'checking',
    user: null,
    authError: null,
    noAuthBackend: false,
    httpPolling: false,
    topology: null,
    snapshot: null,
    loadError: null,
    conn: 'connecting',
    lastUpdate: null,
    clock: { sim: 0, perf: performance.now(), speed: 1, paused: true },
    selection: null,
    nav: 'overview',
    replan: emptyReplan,
    previewPlanId: null,
    compareOpen: false,
    incidentOpen: false,
    csv: { open: false, text: null, loading: false },
    helpOpen: false,
    history: null,
    pending: null,
    toasts: [],
    latency: { last_ms: null },
    autopilot: { mode: 'advise', enabled: false, tips: [], advice: null, status: 'watching', note: 'Слежу за станцией и даю советы', log: [], applied: 0 },
    indexTrend: [],
    journal: [],

    boot: () => {
      api.me().then(
        (user) => {
          set({ auth: 'signed_in', user })
          startSession()
        },
        async (e) => {
          // Backend без входа (этап 1 у И): /api/me ещё нет, но чтение открыто — работаем как наблюдатель.
          if (e instanceof HttpError && (e.status === 404 || e.status === 405)) {
            try {
              await api.state()
              set({ auth: 'signed_in', user: { username: 'без входа', role: 'viewer' }, noAuthBackend: true })
              startSession()
              return
            } catch {
              /* падаем на экран входа */
            }
          }
          set({ auth: 'anonymous' })
        },
      )
      return () => {
        stopWs?.()
        stopWs = null
      }
    },
    login: async (u, p) => {
      set({ authError: null })
      try {
        const user = await api.login(u, p)
        set({ auth: 'signed_in', user })
        startSession()
      } catch (e) {
        set({ authError: e instanceof HttpError ? e.body.message : 'Сервер не отвечает. Проверьте, что backend запущен.' })
      }
    },
    logout: async () => {
      try {
        await api.logout()
      } catch {
        /* сессия всё равно сбрасывается локально */
      }
      stopWs?.()
      stopWs = null
      set({ auth: 'anonymous', user: null, snapshot: null, history: null, nav: 'overview', selection: null, replan: emptyReplan })
    },
    select: (selection) => set({ selection }),
    setNav: (nav) => {
      if (nav === 'history') get().openHistory()
      else if (get().history) get().closeHistory()
      set({ nav })
    },
    control: async (action, speed) => {
      await guarded(action, (run) => api.control(run, action, speed))
    },
    incident: async (cmd) => {
      const r = await guarded('incident', (run) => api.incident(run, cmd))
      if (r) {
        // backend возвращает CommandResult (без results); мок дополнительно присылает итог по каждому сбою
        const n = Array.isArray(cmd) ? cmd.length : 1
        const bad = (r.results ?? []).filter((x) => x.status && x.status !== 200)
        const text = r.results?.length ? r.results.map((x) => x.message).join('; ') : n > 1 ? `Пакет из ${n} сбоев принят` : 'Сбой принят'
        get().toast(bad.length ? 'info' : 'ok', text + (r.replan_required === false ? '.' : '. Идёт пересчёт плана.'))
      }
      return !!r
    },
    requestReplan: async () => {
      const r = await guarded('replan', (run) => api.replan(run))
      if (r) set((s) => ({ replan: { ...s.replan, status: 'running', job_id: r.job_id } }))
    },
    applyPlan: async (id) => {
      const snap = get().snapshot
      if (!snap) return
      const r = await guarded('apply', (run) => api.apply(id, run, snap.state_version))
      if (r) {
        get().toast('ok', 'План принят. Новые назначения уже исполняются.')
        set((s) => ({ compareOpen: false, previewPlanId: null, replan: emptyReplan, autopilot: { ...s.autopilot, advice: null } }))
      }
    },
    setPreview: (previewPlanId) => set({ previewPlanId }),
    setCompareOpen: (compareOpen) => set(compareOpen ? { compareOpen } : { compareOpen, previewPlanId: null }),
    setIncidentOpen: (incidentOpen) => set({ incidentOpen }),
    setHelpOpen: (helpOpen) => set({ helpOpen }),
    openCsv: async () => {
      const snap = get().snapshot
      if (!snap) return
      set({ csv: { open: true, text: null, loading: true } })
      try {
        const text = await api.exportCsv(snap.run_id)
        set({ csv: { open: true, text, loading: false } })
      } catch (e) {
        set({ csv: { open: false, text: null, loading: false } })
        get().toast('error', e instanceof HttpError ? e.body.message : 'Не удалось получить отчёт')
      }
    },
    closeCsv: () => set({ csv: { open: false, text: null, loading: false } }),
    openHistory: (at_s) => {
      const snap = get().snapshot
      if (!snap) return
      const to = snap.sim_time_s
      const from = Math.max(0, to - HISTORY_SPAN)
      const at = Math.max(from, Math.min(to, at_s ?? to - 60))
      set({ history: { at_s: at, snapshot: null, loading: true, from_s: from, to_s: to, error: null }, compareOpen: false, previewPlanId: null, nav: 'history' })
      loadHistory(at)
    },
    seekHistory: (at_s) => {
      const h = get().history
      if (!h) return
      const at = Math.max(h.from_s, Math.min(get().snapshot?.sim_time_s ?? h.to_s, at_s))
      set({ history: { ...h, at_s: at, loading: true } })
      loadHistory(at)
    },
    closeHistory: () => {
      historySeq++
      set({ history: null, nav: get().nav === 'history' ? 'overview' : get().nav })
    },
    toast: (kind, text) => {
      const id = ++toastSeq
      set((s) => ({ toasts: [...s.toasts.slice(-3), { id, kind, text }] }))
      window.setTimeout(() => get().dismissToast(id), kind === 'error' ? 7000 : 4000)
    },
    dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
    setAutopilot: (enabled) => get().setAssistantMode(enabled ? 'auto' : 'advise'),
    setAssistantMode: (mode) => {
      const { user, autopilot } = get()
      if (mode === 'auto' && user?.role === 'viewer') {
        get().toast('error', 'Автопилот может включить только диспетчер или администратор. Советы доступны.')
        return
      }
      if (mode === autopilot.mode) return
      const note = mode === 'off' ? '' : mode === 'advise' ? 'Слежу за станцией и даю советы' : 'Сам принимаю лучший допустимый план'
      set({ autopilot: { ...autopilot, mode, enabled: mode === 'auto', status: mode === 'off' ? 'off' : 'watching', note, advice: mode === 'off' ? null : autopilot.advice, tips: mode === 'off' ? [] : autopilot.tips } })
      const title = mode === 'off' ? 'ИИ-помощник выключен' : mode === 'advise' ? 'Режим «Советы»' : 'Режим «Автопилот»'
      const reasons = mode === 'off' ? ['Решения и анализ — только диспетчер']
        : mode === 'advise' ? ['При конфликте или сбое сам считает варианты и рекомендует лучший; принимает диспетчер']
          : ['При конфликте или сбое сам пересчитывает и принимает лучший допустимый вариант']
      get().apLog({ kind: 'info', title, reasons })
    },
    apUpdate: (patch) => set((s) => ({ autopilot: { ...s.autopilot, ...patch } })),
    apLog: (d) => set((s) => ({
      autopilot: {
        ...s.autopilot,
        log: [{ ...d, id: ++toastSeq, at_real: Date.now(), at_sim: s.snapshot?.sim_time_s ?? 0 }, ...s.autopilot.log].slice(0, 40),
      },
    })),
  }
})

/** Оценка модельного времени между clock_sync. В паузе и без связи не экстраполируется. */
export function estimateSim(c: ClockBase, perfNow: number): number {
  if (c.paused) return c.sim
  const elapsed = Math.min((perfNow - c.perf) / 1000, 2.5)
  return c.sim + elapsed * c.speed
}

export const selectPreviewPlan = (s: State): Plan | null =>
  s.previewPlanId && !s.history ? s.replan.plans.find((p) => p.id === s.previewPlanId) ?? null : null

/** Снимок, который сейчас показывается: исторический (если выбран момент прошлого) или онлайн. */
export const selectView = (s: State): Snapshot | null => (s.history ? s.history.snapshot ?? s.snapshot : s.snapshot)
export const selectRole = (s: State): Role => s.user?.role ?? 'viewer'

// Для отладки и e2e-проверок в dev-сборке
if (import.meta.env.DEV) (window as unknown as { __store: typeof useStore }).__store = useStore

/** События для журнала — сравнение двух подряд пришедших снимков сервера (только факты, без прогнозов). */
function diffEvents(a: Snapshot, b: Snapshot): JEvent[] {
  if (a.run_id !== b.run_id || b.state_version === a.state_version) return []
  const out: JEvent[] = []
  const ev = (kind: JEvent['kind'], text: string, train?: string, bad?: boolean) => out.push({ id: ++toastSeq, t: b.sim_time_s, kind, text, train, bad })
  const at = Object.fromEntries(a.trains.map((t) => [t.id, t]))
  for (const t of b.trains) {
    const p = at[t.id]
    if (!p || p.status === t.status) continue
    if (t.status === 'waiting_entry') ev('arrive', `${t.id} прибыл к границе W`, t.id)
    else if (t.status === 'on_track' && p.status === 'moving' && p.track_id === null) ev('enter', `${t.id} принят на ${t.track_id}`, t.id)
    else if (t.status === 'departed') {
      const late = (t.actual_departure_s ?? b.sim_time_s) - t.scheduled_departure_s
      ev('depart', `${t.id} отправлен${late > 0 ? `, опоздание ${Math.round(late / 60)} мин` : ' по графику'}`, t.id, late > 0)
    }
  }
  const tr = Object.fromEntries(a.tracks.map((t) => [t.id, t]))
  for (const t of b.tracks) {
    if (tr[t.id]?.availability === 'open' && t.availability === 'closed') ev('incident', `${t.id} закрыт до ${fmtTime(t.closed_until_s)}`, undefined, true)
    if (tr[t.id]?.availability === 'closed' && t.availability === 'open') ev('resolved', `${t.id} снова открыт`)
  }
  const rs = Object.fromEntries(a.resources.map((r) => [r.id, r]))
  for (const r of b.resources) {
    if (rs[r.id]?.availability === 'available' && r.availability === 'unavailable') ev('incident', `${r.id} недоступен до ${fmtTime(r.unavailable_until_s)}`, undefined, true)
    if (rs[r.id]?.availability === 'unavailable' && r.availability === 'available') ev('resolved', `${r.id} снова доступен`)
  }
  for (const t of b.trains) {
    const p = at[t.id]
    if (p && t.status === 'scheduled' && t.expected_arrival_s > p.expected_arrival_s)
      ev('incident', `${t.id}: прибытие перенесено на ${fmtTime(t.expected_arrival_s)}`, t.id, true)
  }
  if (b.active_plan_id && b.active_plan_id !== a.active_plan_id) ev('plan', `Принят план ${b.active_plan_id}`)
  // кратковременные ожидания горловины не засоряют журнал: только нарушения плана и закрытия/недоступность
  const notable = (c: Snapshot['conflicts'][number]) => c.kind === 'plan' || c.code === 'TRACK_CLOSED' || c.code === 'RESOURCE_UNAVAILABLE'
  const ca = new Set(a.conflicts.filter(notable).map((c) => c.id))
  const cb = new Set(b.conflicts.filter(notable).map((c) => c.id))
  for (const c of b.conflicts.filter(notable)) if (!ca.has(c.id)) ev('conflict', c.message, c.entity_ids[0], true)
  const gone = a.conflicts.filter(notable).filter((c) => !cb.has(c.id)).length
  if (gone) ev('resolved', gone === 1 ? 'Конфликт снят' : `Снято конфликтов: ${gone}`)
  return out.reverse()
}
function fmtTime(s: number | null) {
  if (s === null) return '—'
  const m = Math.floor(s / 60), sec = s % 60
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
}
