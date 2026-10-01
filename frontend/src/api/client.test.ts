// Контракт frontend → backend на уровне HTTP-клиента: CSRF, варианты плана, пакет сбоев, история.
// Ответы backend берутся из shared/examples — их генерирует scripts/generate_contracts.py из Pydantic-моделей
// backend (CI проверяет, что файлы актуальны), поэтому расхождение с backend ломает этот тест.
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { api, CSRF_HEADER, getCsrfToken, HttpError, setCsrfToken } from './client'
import { setTransport, type Transport } from './transport'
import planResponse from '../../../shared/examples/plan.response.json'
import planFeasible from '../../../shared/examples/plan.feasible.json'
import incidentBatch from '../../../shared/examples/incident.batch.json'
import incidentSingle from '../../../shared/examples/incident.close_track.json'
import history from '../../../shared/examples/history.json'

type Call = { method: string; path: string; body?: unknown; headers?: Record<string, string> }
type Reply = { status: number; body?: unknown }

function fakeBackend(handler: (c: Call) => Reply) {
  const calls: Call[] = []
  const t: Transport = {
    demo: false,
    request: async (method, path, body, headers) => {
      const c = { method, path, body, headers }
      calls.push(c)
      const r = handler(c)
      return { status: r.status, text: r.body === undefined ? '' : JSON.stringify(r.body) }
    },
    socket: () => { throw new Error('no ws in unit tests') },
  }
  setTransport(t)
  return calls
}

const SESSION = { username: 'dispatcher', role: 'dispatcher', permissions: ['state:read', 'simulation:control'], expires_at: '2026-10-01T20:00:00Z', csrf_token: 'tok-1' }
const OK = { command_id: 'c', run_id: 'example-run', state_version: 3, replan_required: true }

beforeEach(() => setCsrfToken(null))
afterEach(() => setCsrfToken(null))

describe('CSRF', () => {
  it('берёт токен из /api/login и шлёт X-CSRF-Token только на изменяющих запросах', async () => {
    const calls = fakeBackend((c) => (c.path === '/api/login' ? { status: 200, body: SESSION } : { status: 200, body: OK }))
    const user = await api.login('dispatcher', 'pw')
    expect(user).toEqual({ username: 'dispatcher', role: 'dispatcher' })
    expect(getCsrfToken()).toBe('tok-1')
    expect(calls[0].headers?.[CSRF_HEADER]).toBeUndefined() // до входа токена нет

    await api.control('example-run', 'start')
    await api.replan('example-run')
    await api.apply('P1', 'example-run', 3)
    await api.incident('example-run', { kind: 'delay', target_id: 'T05', delay_s: 300 })
    for (const c of calls.slice(1)) {
      expect(c.method).toBe('POST')
      expect(c.headers?.[CSRF_HEADER]).toBe('tok-1')
    }
    await api.exportCsv('example-run')
    expect(calls.at(-1)!.method).toBe('GET')
    expect(calls.at(-1)!.headers).toBeUndefined()
  })

  it('восстанавливает токен из /api/me после перезагрузки страницы', async () => {
    fakeBackend(() => ({ status: 200, body: { ...SESSION, csrf_token: 'tok-me' } }))
    await api.me()
    expect(getCsrfToken()).toBe('tok-me')
  })

  it('при CSRF_FAILED один раз обновляет токен через /api/me и повторяет команду', async () => {
    let n = 0
    const calls = fakeBackend((c) => {
      if (c.path === '/api/me') return { status: 200, body: { ...SESSION, csrf_token: 'tok-2' } }
      n++
      return c.headers?.[CSRF_HEADER] === 'tok-2'
        ? { status: 200, body: OK }
        : { status: 403, body: { code: 'CSRF_FAILED', message: 'Отсутствует или неверен заголовок X-CSRF-Token.', details: [] } }
    })
    setCsrfToken('old')
    const r = await api.control('example-run', 'pause')
    expect(r.state_version).toBe(3)
    expect(n).toBe(2)
    expect(calls.map((c) => c.path)).toEqual(['/api/simulation/control', '/api/me', '/api/simulation/control'])
  })

  it('не зацикливается: 403 FORBIDDEN (роль) отдаётся вызывающему без повтора', async () => {
    const calls = fakeBackend(() => ({ status: 403, body: { code: 'FORBIDDEN', message: 'Недостаточно прав для этого действия.', details: [] } }))
    setCsrfToken('tok')
    await expect(api.control('example-run', 'start')).rejects.toMatchObject({ status: 403, body: { code: 'FORBIDDEN' } })
    expect(calls).toHaveLength(1)
  })

  it('logout сбрасывает токен даже при ответе 204 без тела', async () => {
    fakeBackend(() => ({ status: 204 }))
    setCsrfToken('tok')
    await api.logout()
    expect(getCsrfToken()).toBeNull()
  })
})

describe('варианты плана', () => {
  it('разворачивает PlanResponse {plan, stale, applicable} backend — варианты не пустые', async () => {
    fakeBackend(() => ({ status: 200, body: planResponse }))
    const p = await api.plan(planResponse.plan.id)
    expect(p.id).toBe(planResponse.plan.id)
    expect(p.assignments.length).toBe(planResponse.plan.assignments.length)
    expect(p.assignments.length).toBeGreaterThan(0)
    expect(p.status).toBe('feasible')
    expect(p.applicable).toBe(true)
    expect(p.stale).toBe(false)
  })

  it('читает метрики backend (total_positive_delay_s, changed_future_assignments)', async () => {
    // ровно те ключи, что пишут planner.calculate_plan_metrics и runtime/planning.calculate_variants
    const metrics = { total_positive_delay_s: 7920, max_delay_s: 2100, unassigned_count: 0, changed_future_assignments: 47 }
    fakeBackend(() => ({ status: 200, body: { plan: { ...planFeasible, metrics }, stale: false, applicable: true } }))
    const p = await api.plan(planFeasible.id)
    expect(p.metrics.total_delay_s).toBe(7920)
    expect(p.metrics.max_delay_s).toBe(2100)
    expect(p.metrics.changed_count).toBe(47)
  })
})

describe('сбои', () => {
  it('пакет уходит в POST /api/incidents/batch с полем incidents — ровно как IncidentBatchCommand', async () => {
    const calls = fakeBackend(() => ({ status: 200, body: OK }))
    await api.incident('example-run', [
      { kind: 'close_track', target_id: 'P04', duration_s: 600 },
      { kind: 'loco_unavailable', target_id: 'L01', duration_s: 300 },
    ])
    const c = calls[0]
    expect(c.path).toBe('/api/incidents/batch')
    const body = c.body as typeof incidentBatch
    expect(Object.keys(body).sort()).toEqual(Object.keys(incidentBatch).sort())
    expect(body.incidents).toEqual(incidentBatch.incidents)
  })

  it('одиночный сбой — POST /api/incidents в формате IncidentCommand, без чужих полей', async () => {
    const calls = fakeBackend(() => ({ status: 200, body: OK }))
    await api.incident('example-run', { kind: 'close_track', target_id: incidentSingle.target_id, duration_s: incidentSingle.duration_s, delay_s: 999 })
    const body = calls[0].body as Record<string, unknown>
    expect(calls[0].path).toBe('/api/incidents')
    const expected = Object.entries(incidentSingle).filter(([, v]) => v !== null).map(([k]) => k).sort()
    expect(Object.keys(body).sort()).toEqual(expected)
    expect(body.kind).toBe('close_track')
    expect(body.delay_s).toBeUndefined()

    await api.incident('example-run', { kind: 'delay', target_id: 'T05', delay_s: 300 })
    expect(calls[1].body).toMatchObject({ kind: 'delay_train', target_id: 'T05', delay_s: 300 })
    expect((calls[1].body as Record<string, unknown>).duration_s).toBeUndefined()
  })

  it('пакет из одного сбоя отправляется как одиночный сбой', async () => {
    const calls = fakeBackend(() => ({ status: 200, body: OK }))
    await api.incident('example-run', [{ kind: 'delay', target_id: 'T05', delay_s: 120 }])
    expect(calls[0].path).toBe('/api/incidents')
  })

  it('422 backend превращается в HttpError с кодом и сообщением', async () => {
    fakeBackend(() => ({ status: 422, body: { code: 'VALIDATION_ERROR', message: 'Проверьте поля запроса.', details: [] } }))
    const e = await api.incident('example-run', [{ kind: 'delay', target_id: 'T05', delay_s: 1 }, { kind: 'delay', target_id: 'T06', delay_s: 1 }]).catch((x) => x)
    expect(e).toBeInstanceOf(HttpError)
    expect(e.status).toBe(422)
    expect(e.body.code).toBe('VALIDATION_ERROR')
  })
})

describe('история', () => {
  it('принимает HistoricalState backend и границы доступного интервала', async () => {
    fakeBackend(() => ({ status: 200, body: history }))
    const r = await api.history('example-run', 60)
    expect(r.snapshot.sim_time_s).toBe(history.snapshot.sim_time_s)
    expect(r.requested_at_s).toBe(history.at_s)
    expect(r.available_from_s).toBe(history.available_from_s)
    expect(r.available_to_s).toBe(history.available_to_s)
    expect(Number.isFinite(r.available_from_s) && Number.isFinite(r.available_to_s)).toBe(true)
  })
})

describe('топология без зашитых id', () => {
  const MAP: Record<string, string> = { W: 'ENTRY_WEST', E: 'EXIT_EAST', GW: 'THROAT_1', GE: 'THROAT_2', P01: 'PLATFORM_A', P12: 'DEPOT', L01: 'LINE_X' }
  const rename = (v: unknown): unknown =>
    Array.isArray(v) ? v.map(rename)
      : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v).map(([k, x]) => [rename(k) as string, rename(x)]))
        : typeof v === 'string' ? v.split('_').map((t) => MAP[t] ?? t).join('_') : v

  it('находит вход, выход и горловины станции с другими id', async () => {
    const { normalizeState } = await import('./adapt')
    const initial = (await import('../../../shared/examples/state.initial.json')).default
    const { topology, snapshot } = normalizeState(rename(initial))
    expect(topology.node_ids).toEqual({ W: 'ENTRY_WEST', GW: 'THROAT_1', GE: 'THROAT_2', E: 'EXIT_EAST' })
    expect(topology.zones.map((z) => z.id).sort()).toEqual(['THROAT_1', 'THROAT_2'])
    for (const r of topology.routes)
      expect(r.kind).toBe(r.from_id === 'ENTRY_WEST' ? 'arrival' : r.to_id === 'EXIT_EAST' ? 'departure' : 'shunt')
    expect(new Set(topology.routes.map((r) => r.kind))).toEqual(new Set(['arrival', 'departure']))
    expect(topology.routes.find((r) => r.from_id === 'ENTRY_WEST')!.kind).toBe('arrival')
    expect(topology.nodes.W).toEqual(initial.topology.boundary_nodes.W)
    expect(snapshot.zones.map((z) => z.id).sort()).toEqual(['THROAT_1', 'THROAT_2'])
  })
})

describe('задержка текущего плана на backend без прогноза в снимке', () => {
  it('считается по принятому плану: конец отправления минус плановое отправление', async () => {
    const { currentDelays, normalizePlan, normalizeState } = await import('./adapt')
    const initial = (await import('../../../shared/examples/state.initial.json')).default
    const { snapshot } = normalizeState(initial)
    const dep = snapshot.operations.find((o) => o.kind === 'departure')!
    const train = snapshot.trains.find((t) => t.id === dep.train_id)!
    const plan = normalizePlan({ id: 'p', run_id: snapshot.run_id, status: 'feasible', strategy: 'passenger_first',
      assignments: [{ operation_id: dep.id, start_s: train.scheduled_departure_s + 180, end_s: train.scheduled_departure_s + 300, track_id: 'P01', route_id: null, resource_ids: [] }] }, snapshot.operations)
    expect(currentDelays(snapshot)).toEqual({ total: 0, max: 0 })
    expect(currentDelays({ ...snapshot, active_plan: plan })).toEqual({ total: 300, max: 300 })
  })
})
