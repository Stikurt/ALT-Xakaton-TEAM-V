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

import { computeTips } from './autopilotPolicy'
import { Station } from '../mock/sim'
import { plan } from '../mock/planner'
import { normalizeSnapshot } from '../api/adapt'

describe('советы ИИ-помощника', () => {
  const run = (n: number, setup?: (st: Station) => void) => {
    const st = new Station()
    st.applyPlan(plan(st.snapshot(), 'earliest_departure'))
    st.paused = false
    st.process()
    setup?.(st)
    for (let i = 0; i < n; i++) st.step()
    return normalizeSnapshot(st.snapshot())
  }
  it('на старте замечаний нет', () => {
    expect(computeTips(run(10))).toEqual([])
  })
  it('сообщает о поезде, который долго ждёт у W', () => {
    const tips = computeTips(run(1500))
    const wait = tips.find((t) => t.key.startsWith('wait-'))
    expect(wait?.text).toMatch(/ждёт у W/)
  })
  it('предупреждает о скором открытии закрытого пути', () => {
    const tips = computeTips(run(560, (st) => st.incident('close_track', 'P09', 600)))
    expect(tips.some((t) => t.key === 'open-P09')).toBe(true)
  })
  it('не больше 6 советов, сначала самые важные', () => {
    const tips = computeTips(run(3000))
    expect(tips.length).toBeLessThanOrEqual(6)
    const order = { bad: 0, warn: 1, info: 2 }
    for (let i = 1; i < tips.length; i++) expect(order[tips[i].severity]).toBeGreaterThanOrEqual(order[tips[i - 1].severity])
  })
})
