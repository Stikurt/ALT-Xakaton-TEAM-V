// Единственный HTTP-клиент приложения (владелец — К). Сервер — источник разрешения.
import type { ApiError, IncidentCmd, Plan, Snapshot, Topology, ClockSync } from './types'

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
  const r = await fetch(path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    credentials: 'include',
  })
  const text = await r.text()
  const data = text ? JSON.parse(text) : null
  if (!r.ok) {
    const e: ApiError = data && data.code ? data : { code: `HTTP_${r.status}`, message: data?.detail ?? r.statusText }
    throw new HttpError(r.status, e)
  }
  return data as T
}

export const api = {
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
