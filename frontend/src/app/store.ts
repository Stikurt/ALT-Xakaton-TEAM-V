// Общий store (владелец — К). StationView получает данные только отсюда через props.
import { create } from 'zustand'
import { api, HttpError } from '../api/client'
import { connectWs, type WsStatus } from '../api/ws'
import type { ClockSync, IncidentCmd, Plan, Snapshot, Topology, WsEnvelope } from '../api/types'

export type Selection =
  | { type: 'train'; id: string }
  | { type: 'track'; id: string }
  | { type: 'resource'; id: string }
  | { type: 'conflict'; id: string }
  | null
export type Nav = 'overview' | 'trains' | 'resources' | 'history'
export type Role = 'viewer' | 'dispatcher' | 'admin'

export interface Toast { id: number; kind: 'ok' | 'error' | 'info'; text: string }
export interface ReplanState {
  status: 'idle' | 'running' | 'done' | 'failed'
  job_id: string | null
  plans: Plan[]
  identical: boolean
  calc_ms: number | null
  error: string | null
  finished_at_epoch: number | null
}

interface ClockBase { sim: number; perf: number; speed: number; paused: boolean }

interface State {
  topology: Topology | null
  snapshot: Snapshot | null
  loadError: string | null
  conn: WsStatus
  lastUpdate: number | null // Date.now() последнего подтверждённого состояния
  clock: ClockBase
  selection: Selection
  nav: Nav
  role: Role
  replan: ReplanState
  previewPlanId: string | null
  compareOpen: boolean
  incidentOpen: boolean
  pending: string | null // команда в полёте
  toasts: Toast[]
  latency: { last_ms: number | null }

  init: () => () => void
  select: (s: Selection) => void
  setNav: (n: Nav) => void
  setRole: (r: Role) => void
  control: (action: 'start' | 'pause' | 'speed' | 'reset', speed?: number) => Promise<void>
  incident: (cmd: IncidentCmd) => Promise<boolean>
  requestReplan: () => Promise<void>
  applyPlan: (id: string) => Promise<void>
  setPreview: (id: string | null) => void
  setCompareOpen: (v: boolean) => void
  setIncidentOpen: (v: boolean) => void
  toast: (kind: Toast['kind'], text: string) => void
  dismissToast: (id: number) => void
}

const emptyReplan: ReplanState = {
  status: 'idle', job_id: null, plans: [], identical: false, calc_ms: null, error: null, finished_at_epoch: null,
}
let toastSeq = 0

export const useStore = create<State>((set, get) => {
  const applySnapshot = (snap: Snapshot, topo?: Topology) => {
    const prev = get().snapshot
    const runChanged = prev && prev.run_id !== snap.run_id
    set((s) => ({
      snapshot: snap,
      topology: topo ?? s.topology,
      lastUpdate: Date.now(),
      ...(runChanged ? { replan: emptyReplan, previewPlanId: null, compareOpen: false, selection: null } : {}),
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
      set({ loadError: e instanceof Error ? e.message : String(e) })
    }
  }

  const onWs = (m: WsEnvelope) => {
    const snap = get().snapshot
    if (snap && m.run_id !== snap.run_id && m.type !== 'snapshot') {
      // смена run_id: не применяем сомнительное сообщение, берём полный снимок
      void refetch()
      return
    }
    switch (m.type) {
      case 'snapshot': {
        const p = m.payload as { snapshot: Snapshot; topology: Topology }
        applySnapshot(p.snapshot, p.topology)
        break
      }
      case 'state_updated': {
        const p = m.payload as { snapshot: Snapshot }
        applySnapshot(p.snapshot)
        break
      }
      case 'clock_sync':
        syncClock(m.payload as ClockSync)
        break
      case 'replan_started':
        set((s) => ({ replan: { ...s.replan, status: 'running', job_id: (m.payload as { job_id: string }).job_id, error: null } }))
        break
      case 'replan_finished': {
        const p = m.payload as { job_id: string; plans: Plan[]; identical: boolean; calc_ms: number }
        set({
          replan: {
            status: 'done', job_id: p.job_id, plans: p.plans, identical: p.identical, calc_ms: p.calc_ms,
            error: null, finished_at_epoch: p.plans[0]?.based_on_epoch ?? null,
          },
          compareOpen: true,
        })
        break
      }
      case 'replan_failed':
        set((s) => ({ replan: { ...s.replan, status: 'failed', error: (m.payload as { message: string }).message } }))
        get().toast('error', 'Пересчёт не удался: ' + (m.payload as { message: string }).message)
        break
      case 'simulation_error':
        get().toast('error', 'Ошибка симуляции: ' + JSON.stringify(m.payload))
        break
    }
  }

  const guarded = async <T,>(label: string, fn: (run_id: string) => Promise<T>): Promise<T | null> => {
    const { snapshot, conn, role } = get()
    if (!snapshot || conn !== 'online') {
      get().toast('error', 'Нет связи — команды заблокированы')
      return null
    }
    if (role === 'viewer') {
      get().toast('error', 'Роль «наблюдатель»: изменение состояния запрещено')
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
      } else get().toast('error', 'Сервер недоступен')
      return null
    } finally {
      set({ pending: null })
    }
  }

  return {
    topology: null,
    snapshot: null,
    loadError: null,
    conn: 'connecting',
    lastUpdate: null,
    clock: { sim: 0, perf: performance.now(), speed: 1, paused: true },
    selection: null,
    nav: 'overview',
    role: 'dispatcher',
    replan: emptyReplan,
    previewPlanId: null,
    compareOpen: false,
    incidentOpen: false,
    pending: null,
    toasts: [],
    latency: { last_ms: null },

    init: () => {
      void refetch()
      return connectWs({
        onMessage: onWs,
        onStatus: (s) => {
          if (s === 'offline') {
            // замораживаем часы на последнем подтверждённом состоянии
            const { clock } = get()
            set({ clock: { ...clock, sim: estimateSim(clock, performance.now()), perf: performance.now(), paused: true } })
          }
          set({ conn: s })
        },
        onGap: () => void refetch(),
      })
    },
    select: (selection) => set({ selection }),
    setNav: (nav) => set({ nav }),
    setRole: (role) => set({ role }),
    control: async (action, speed) => {
      await guarded(action, (run) => api.control(run, action, speed))
    },
    incident: async (cmd) => {
      const r = await guarded('incident', (run) => api.incident(run, cmd))
      if (r) get().toast('ok', r.results.map((x) => x.message).join('; ') + '. Запущен пересчёт.')
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
        get().toast('ok', 'План принят — симулятор исполняет новые назначения')
        set({ compareOpen: false, previewPlanId: null, replan: emptyReplan })
      }
    },
    setPreview: (previewPlanId) => set({ previewPlanId }),
    setCompareOpen: (compareOpen) => set(compareOpen ? { compareOpen } : { compareOpen, previewPlanId: null }),
    setIncidentOpen: (incidentOpen) => set({ incidentOpen }),
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
  const elapsed = Math.min((perfNow - c.perf) / 1000, 2.5) // не убегаем далеко без подтверждения
  return c.sim + elapsed * c.speed
}

export const selectPreviewPlan = (s: State): Plan | null =>
  s.previewPlanId ? s.replan.plans.find((p) => p.id === s.previewPlanId) ?? null : null
