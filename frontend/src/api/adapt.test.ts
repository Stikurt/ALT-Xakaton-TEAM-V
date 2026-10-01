import { describe, expect, it } from 'vitest'
import { normalizePlan, normalizeSnapshot, normalizeState } from './adapt'
import moving from './__fixtures__/backend/state.moving.json'
import initial from './__fixtures__/backend/state.initial.json'
import feasible from './__fixtures__/backend/plan.feasible.json'
import infeasible from './__fixtures__/backend/plan.infeasible.json'
import wsUpdated from './__fixtures__/backend/ws.state_updated.json'
import wsSnapshot from './__fixtures__/backend/ws.snapshot.json'

describe('адаптер контракта backend (ветка backend, 45bf1b8)', () => {
  it('собирает топологию: 12 путей из snapshot.tracks, узлы и горловины', () => {
    const { topology } = normalizeState(moving)
    expect(topology.tracks).toHaveLength(12)
    expect(topology.tracks[0]).toMatchObject({ id: 'P01', geometry: { x1: 320, y1: 120, x2: 1080, y2: 120 } })
    expect(topology.nodes.GW).toEqual([180, 450])
    expect(topology.nodes.E).toEqual([1360, 450])
    expect(topology.zones.map((z) => z.id).sort()).toEqual(['GE', 'GW'])
    expect(topology.routes.find((r) => r.id === 'R_W_P01')?.kind).toBe('arrival')
  })

  it('приводит виды операций и ресурсов, выводит этапы и перемещения', () => {
    const { snapshot } = normalizeState(moving)
    const kinds = snapshot.operations.map((o) => o.kind)
    expect(kinds).toEqual(['arrival', 'stop', 'departure'])
    expect(snapshot.operations.map((o) => o.is_move)).toEqual(['arrival', null, 'departure'])
    expect(snapshot.resources.find((r) => r.id === 'L01')?.kind).toBe('shunting_loco')
    expect(snapshot.resources.find((r) => r.id === 'B03')?.kind).toBe('inspection_crew')
    expect(snapshot.resources.find((r) => r.id === 'B01')?.kind).toBe('shunting_crew')
  })

  it('горловина занята движущимся составом, если backend не прислал zones', () => {
    const { snapshot } = normalizeState(moving)
    expect(snapshot.zones.find((z) => z.id === 'GW')?.active_operation_id).toBe('T01_01_arrival')
    expect(snapshot.zones.find((z) => z.id === 'GE')?.active_operation_id).toBeNull()
  })

  it('пустые поля дают безопасные значения, а не падение', () => {
    const { snapshot } = normalizeState(initial)
    expect(snapshot.index).toBeNull()
    expect(snapshot.active_plan).toBeNull()
    expect(snapshot.incidents).toEqual([])
    expect(snapshot.queue).toEqual([])
  })

  it('wait_reason {code,message} превращается в текст', () => {
    const raw = structuredClone(initial.snapshot) as any // eslint-disable-line @typescript-eslint/no-explicit-any
    raw.operations[0].wait_reason = { code: 'ROUTE_BUSY', message: 'горловина GW занята' }
    const s = normalizeSnapshot(raw)
    expect(s.operations[0].wait_reason).toBe('горловина GW занята')
    expect(s.trains[0].wait_reason).toBe('горловина GW занята')
  })

  it('план: строковые объяснения, пустые метрики, неразмещённые операции', () => {
    const ops = normalizeState(moving).snapshot.operations
    const p = normalizePlan(feasible, ops)
    expect(p.assignments[0]).toMatchObject({ train_id: 'T01', kind: 'arrival' })
    expect(p.explanations[0].message).toMatch(/Учебный пример/)
    expect(p.metrics.total_delay_s).toBeNull()
    const q = normalizePlan(infeasible, ops)
    expect(q.unassigned.every((u) => u.train_id === 'T01')).toBe(true)
    expect(q.metrics.unassigned_count).toBe(1)
  })

  it('WS-конверты snapshot и state_updated читаются', () => {
    const n = normalizeState(wsSnapshot.payload)
    expect(n.snapshot.run_id).toBe(wsSnapshot.run_id)
    const s = normalizeSnapshot(wsUpdated.payload.snapshot, n.topology)
    expect(s.trains.length).toBeGreaterThan(0)
  })
})
