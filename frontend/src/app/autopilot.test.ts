import { describe, expect, it } from 'vitest'
import { pickBest } from './autopilotPolicy'
import type { Plan } from '../api/types'

const mk = (id: string, status: Plan['status'], un: number, delay: number | null, changed: number): Plan => ({
  id, run_id: 'r', based_on_version: 0, based_on_epoch: 1, based_on_time_s: 0, strategy: 'passenger_first', status, timed_out: false,
  assignments: [], unassigned: [], explanations: [], violations: [], calc_ms: 1, index_forecast: null,
  metrics: { total_delay_s: delay, max_delay_s: null, unassigned_count: un, changed_count: changed, delayed_trains: null, forecast_departures: {}, delays: {} },
})

describe('правило выбора ИИ-диспетчера', () => {
  it('недопустимый и неполный план не выбирается никогда', () => {
    expect(pickBest([mk('a', 'partial', 0, 0, 0), mk('b', 'infeasible', 0, 0, 0)])).toBeNull()
  })
  it('сначала меньше неразмещённых, потом меньше задержка, потом меньше изменений', () => {
    expect(pickBest([mk('a', 'feasible', 1, 0, 0), mk('b', 'feasible', 0, 999, 9)])!.id).toBe('b')
    expect(pickBest([mk('a', 'feasible', 0, 600, 1), mk('b', 'feasible', 0, 300, 9)])!.id).toBe('b')
    expect(pickBest([mk('a', 'feasible', 0, 300, 5), mk('b', 'feasible', 0, 300, 2)])!.id).toBe('b')
  })
})
