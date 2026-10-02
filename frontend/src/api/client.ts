// Единственный HTTP-клиент приложения (владелец — К). Сервер — источник разрешения.
import type { ApiError, IncidentCmd, Operation, Plan, Snapshot, Topology, ClockSync } from './types'
import { transport } from './transport'
import { incidentToServer, normalizePlanResponse, normalizeSnapshot, normalizeState } from './adapt'

export class HttpError extends Error {
  status: number
  body: ApiError
  constructor(status: number, body: ApiError) {
    super(body.message)
    this.status = status
    this.body = body
  }
}

const newId = () =>
  (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`)

// CSRF: backend выдаёт csrf_token в ответах /api/login и /api/me (backend/app/auth/routes.py) и проверяет
// заголовок X-CSRF-Token на каждой изменяющей команде. Токен держим только в памяти вкладки (не localStorage):
// после перезагрузки страницы он заново приходит из /api/me вместе с проверкой сессии.
export const CSRF_HEADER = 'X-CSRF-Token'
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS'])
let csrfToken: string | null = null
export const setCsrfToken = (token: string | null | undefined) => {
  csrfToken = token || null
}
export const getCsrfToken = () => csrfToken

type Raw = { status: number; text: string }
const send = (method: string, path: string, body?: unknown): Promise<Raw> => {
  const headers = !SAFE_METHODS.has(method.toUpperCase()) && csrfToken ? { [CSRF_HEADER]: csrfToken } : undefined
  return transport().request(method, path, body, headers)
}

function parse(r: Raw): unknown {
  try {
    return r.text ? JSON.parse(r.text) : null
  } catch {
    return r.text
  }
}

function toError(r: Raw, data: unknown): HttpError {
  const d = data as Partial<ApiError> | null
  const e: ApiError = d && d.code ? (d as ApiError) : { code: `HTTP_${r.status}`, message: `Ошибка сервера (${r.status})` }
  return new HttpError(r.status, e)
}

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  let r = await send(method, path, body)
  let data = parse(r)
  // Токен мог смениться (повторный вход в другой вкладке): один раз обновляем его через /api/me и повторяем.
  if (r.status === 403 && (data as Partial<ApiError> | null)?.code === 'CSRF_FAILED' && path !== '/api/me') {
    const me = await send('GET', '/api/me')
    if (me.status < 400) {
      setCsrfToken((parse(me) as { csrf_token?: string } | null)?.csrf_token)
      r = await send(method, path, body)
      data = parse(r)
    }
  }
  if (r.status >= 400) throw toError(r, data)
  return data as T
}

export interface User { username: string; role: 'viewer' | 'dispatcher' | 'admin' }
interface Session extends User { csrf_token?: string; permissions?: string[]; expires_at?: string }
export interface HistoryResp { snapshot: Snapshot; requested_at_s: number; available_from_s: number; available_to_s: number }
/** Ответ backend на команду (CommandResult). Мок дополнительно присылает results по каждому сбою пакета. */
export interface CommandResp {
  command_id?: string; run_id: string; state_version: number; replan_required?: boolean
  results?: { status?: number; message: string }[]
}

const session = (s: Session): User => {
  setCsrfToken(s.csrf_token)
  return { username: s.username, role: s.role }
}

export const api = {
  login: async (username: string, password: string) => session(await req<Session>('POST', '/api/login', { username, password })),
  logout: async () => {
    try {
      await req<null>('POST', '/api/logout', {})
    } finally {
      setCsrfToken(null)
    }
  },
  me: async () => session(await req<Session>('GET', '/api/me')),
  history: async (run_id: string, at_s: number): Promise<HistoryResp> => {
    const r = await req<{ snapshot: unknown; at_s?: number; requested_at_s?: number; available_from_s?: number; available_to_s?: number }>(
      'GET', `/api/history?run_id=${encodeURIComponent(run_id)}&at_s=${Math.floor(at_s)}`)
    const snapshot = normalizeSnapshot(r.snapshot)
    const at = r.requested_at_s ?? r.at_s ?? snapshot.sim_time_s
    return { snapshot, requested_at_s: at, available_from_s: r.available_from_s ?? 0, available_to_s: r.available_to_s ?? at }
  },
  exportCsv: async (run_id: string): Promise<string> => {
    const r = await send('GET', `/api/export.csv?run_id=${encodeURIComponent(run_id)}`)
    if (r.status >= 400) throw new HttpError(r.status, { code: `HTTP_${r.status}`, message: 'Не удалось получить отчёт' })
    return r.text
  },
  state: async (): Promise<{ snapshot: Snapshot; topology: Topology; clock: ClockSync }> => {
    const r = await req<{ snapshot: unknown; topology: unknown; clock?: ClockSync }>('GET', '/api/state')
    const n = normalizeState(r)
    return { ...n, clock: r.clock ?? { sim_time_s: n.snapshot.sim_time_s, speed: n.snapshot.speed, paused: n.snapshot.paused } }
  },
  control: (run_id: string, action: 'start' | 'pause' | 'speed' | 'reset', speed?: number) =>
    req<CommandResp>('POST', '/api/simulation/control', speed === undefined
      ? { command_id: newId(), run_id, action }
      : { command_id: newId(), run_id, action, speed }),
  // Контракт backend: один сбой — POST /api/incidents (IncidentCommand), пакет — POST /api/incidents/batch
  // (IncidentBatchCommand, поле incidents, 1–50 элементов; применяется атомарно, один пересчёт на весь пакет).
  incident: (run_id: string, cmd: IncidentCmd | IncidentCmd[]): Promise<CommandResp> => {
    if (Array.isArray(cmd)) {
      if (cmd.length === 1) cmd = cmd[0]
      else return req<CommandResp>('POST', '/api/incidents/batch', { command_id: newId(), run_id, incidents: cmd.map(incidentToServer) })
    }
    return req<CommandResp>('POST', '/api/incidents', { command_id: newId(), run_id, ...incidentToServer(cmd) })
  },
  replan: (run_id: string) => req<{ job_id: string }>('POST', '/api/replans', { command_id: newId(), run_id }),
  // Backend отдаёт PlanResponse {plan, stale, applicable}: сам план — в поле plan.
  plan: async (id: string, ops?: Operation[]): Promise<Plan> => {
    const r = await req<{ plan?: unknown; stale?: boolean; applicable?: boolean }>('GET', `/api/plans/${encodeURIComponent(id)}`)
    return normalizePlanResponse(r, ops)
  },
  apply: (id: string, run_id: string, expected_state_version: number) =>
    req<CommandResp>('POST', `/api/plans/${encodeURIComponent(id)}/apply`, { command_id: newId(), run_id, expected_state_version }),
}
