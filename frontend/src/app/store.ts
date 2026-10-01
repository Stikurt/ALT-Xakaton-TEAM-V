// Общий store (владелец — К). StationView получает данные только отсюда через props.
import { create } from 'zustand'
import { api, HttpError, type User } from '../api/client'
import { connectWs, type WsStatus } from '../api/ws'
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

interface ClockBase { sim: number; perf: number; speed: number; paused: boolean }

interface State {
  auth: 'checking' | 'anonymous' | 'signed_in'
  user: User | null
  authError: string | null
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

  boot: () => () => void
  login: (u: string, p: string) => Promise<void>
  logout: () => Promise<void>
  select: (s: Selection) => void
  setNav: (n: Nav) => void
  control: (action: 'start' | 'pause' | 'speed' | 'reset', speed?: number) => Promise<void>
  incident: (cmd: IncidentCmd) => Promise<boolean>
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
}

const emptyReplan: ReplanState = {
  status: 'idle', job_id: null, plans: [], identical: false, calc_ms: null, error: null, finished_at_epoch: null, baseline: null,
}
const HISTORY_SPAN = 15 * 60
let toastSeq = 0
let stopWs: (() => void) | null = null
let historySeq = 0
let historyTimer: number | undefined

export const useStore = create<State>((set, get) => {
  const applySnapshot = (snap: Snapshot, topo?: Topology) => {
    const prev = get().snapshot
    const runChanged = prev && prev.run_id !== snap.run_id
    set((s) => ({
      snapshot: snap,
      topology: topo ?? s.topology,
      lastUpdate: Date.now(),
      ...(runChanged
        ? { replan: emptyReplan, previewPlanId: null, compareOpen: false, selection: null, history: null, nav: s.nav === 'history' ? 'overview' : s.nav }
        : {}),
    }))
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
    stopWs = connectWs({
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
  }

  const onWs = (m: WsEnvelope) => {
    const snap = get().snapshot
    if (snap && m.run_id !== snap.run_id && m.type !== 'snapshot') {
      void refetch()
      return
    }
    switch (m.type) {
      case 'snapshot': {
        const p = m.payload as { snapshot: Snapshot; topology: Topology }
        applySnapshot(p.snapshot, p.topology)
        break
      }
      case 'state_updated':
        applySnapshot((m.payload as { snapshot: Snapshot }).snapshot)
        break
      case 'clock_sync':
        syncClock(m.payload as ClockSync)
        break
      case 'replan_started':
        set((s) => ({ replan: { ...s.replan, status: 'running', job_id: (m.payload as { job_id: string }).job_id, error: null } }))
        break
      case 'replan_finished': {
        const p = m.payload as { job_id: string; plans: Plan[]; identical: boolean; calc_ms: number; baseline_index: StationIndex | null }
        set({
          replan: {
            status: 'done', job_id: p.job_id, plans: p.plans, identical: p.identical, calc_ms: p.calc_ms,
            error: null, finished_at_epoch: p.plans[0]?.based_on_epoch ?? null, baseline: p.baseline_index ?? null,
          },
          compareOpen: get().history ? get().compareOpen : true,
        })
        break
      }
      case 'replan_failed':
        set((s) => ({ replan: { ...s.replan, status: 'failed', error: (m.payload as { message: string }).message } }))
        get().toast('error', 'Пересчёт не удался: ' + (m.payload as { message: string }).message)
        break
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
        set((s) => ({ history: s.history && { ...s.history, snapshot: r.snapshot, loading: false, error: null, from_s: r.available_from_s, to_s: r.available_to_s } }))
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

    boot: () => {
      api.me().then(
        (user) => {
          set({ auth: 'signed_in', user })
          startSession()
        },
        () => set({ auth: 'anonymous' }),
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
      if (r) get().toast('ok', r.results.map((x) => x.message).join('; ') + '. Идёт пересчёт плана.')
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
        set({ compareOpen: false, previewPlanId: null, replan: emptyReplan })
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
