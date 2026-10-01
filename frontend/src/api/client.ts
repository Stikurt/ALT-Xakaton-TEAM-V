// Единственный HTTP-клиент приложения (владелец — К). Сервер — источник разрешения.
import type { ApiError, IncidentCmd, Plan, Snapshot, Topology, ClockSync } from './types'
import { transport } from './transport'

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

async function req<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await transport().request(method, path, body)
  let data: unknown = null
  try {
    data = r.text ? JSON.parse(r.text) : null
  } catch {
    data = r.text
  }
  if (r.status >= 400) {
    const d = data as Partial<ApiError> | null
    const e: ApiError = d && d.code ? (d as ApiError) : { code: `HTTP_${r.status}`, message: `Ошибка сервера (${r.status})` }
    throw new HttpError(r.status, e)
  }
  return data as T
}

export interface User { username: string; role: 'viewer' | 'dispatcher' | 'admin' }
export interface HistoryResp { snapshot: Snapshot; requested_at_s: number; available_from_s: number; available_to_s: number }

export const api = {
  login: (username: string, password: string) => req<User>('POST', '/api/login', { username, password }),
  logout: () => req<{ ok: boolean }>('POST', '/api/logout', {}),
  me: () => req<User>('GET', '/api/me'),
  history: (run_id: string, at_s: number) =>
    req<HistoryResp>('GET', `/api/history?run_id=${encodeURIComponent(run_id)}&at_s=${Math.floor(at_s)}`),
  exportCsv: async (run_id: string): Promise<string> => {
    const r = await transport().request('GET', `/api/export.csv?run_id=${encodeURIComponent(run_id)}`)
    if (r.status >= 400) throw new HttpError(r.status, { code: `HTTP_${r.status}`, message: 'Не удалось получить отчёт' })
    return r.text
  },
  state: () => req<{ snapshot: Snapshot; topology: Topology; clock: ClockSync }>('GET', '/api/state'),
  control: (run_id: string, action: 'start' | 'pause' | 'speed' | 'reset', speed?: number) =>
    req<{ ok: boolean; run_id: string }>('POST', '/api/simulation/control', { command_id: newId(), run_id, action, speed }),
  incident: (run_id: string, cmd: IncidentCmd) =>
    req<{ ok: boolean; job_id: string; results: { message: string }[] }>('POST', '/api/incidents', {
      command_id: newId(), run_id, ...cmd,
    }),
  replan: (run_id: string) => req<{ job_id: string }>('POST', '/api/replans', { run_id }),
  plan: (id: string) => req<Plan>('GET', `/api/plans/${id}`),
  apply: (id: string, run_id: string, expected_state_version: number) =>
    req<{ ok: boolean }>('POST', `/api/plans/${id}/apply`, { command_id: newId(), run_id, expected_state_version }),
}
