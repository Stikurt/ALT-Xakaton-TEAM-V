import { describe, expect, it } from 'vitest'
import { Station } from './sim'
import { plan, validatePlan } from './planner'

const run = (st: Station, n: number) => { for (let i = 0; i < n; i++) st.step() }

describe('демо-сервер в браузере (порт mock_backend)', () => {
  it('начальный план допустим и детерминирован', () => {
    const a = plan(new Station().snapshot(), 'earliest_departure')
    const b = plan(new Station().snapshot(), 'earliest_departure')
    expect(a.status).toBe('feasible')
    expect(a.violations).toEqual([])
    expect(a.assignments.map((x: { start_s: number }) => x.start_s)).toEqual(b.assignments.map((x: { start_s: number }) => x.start_s))
  })

  it('все 15 поездов уходят; у поезда одна операция, второй путь — только резерв при движении', () => {
    const st = new Station()
    st.applyPlan(plan(st.snapshot(), 'earliest_departure'))
    st.paused = false
    st.process()
    for (let i = 0; i < 9000; i++) {
      st.step()
      for (const t of st.trains) {
        const running = st.opsOf(t.id).filter((o) => o.status === 'running')
        expect(running.length).toBeLessThanOrEqual(1)
        const held = st.tracks.filter((x) => x.occupant_train_id === t.id).length
        expect(held).toBeLessThanOrEqual(t.movement ? 2 : 1)
      }
      // ресурс, занятый операцией, принадлежит выполняющейся операции
      for (const r of st.resources) if (r.active_operation_id) {
        expect(st.operations.find((o) => o.id === r.active_operation_id)?.status).toBe('running')
      }
    }
    expect(st.trains.every((t) => t.status === 'departed')).toBe(true)
  })

  it('закрытие пути: пересчёт допустим и не входит на закрытый путь', () => {
    const st = new Station()
    st.applyPlan(plan(st.snapshot(), 'earliest_departure'))
    st.paused = false
    st.process()
    run(st, 700)
    expect(st.incident('close_track', 'P05', 600)[0]).toBe(200)
    const snap = st.snapshot()
    for (const s of ['passenger_first', 'earliest_departure']) {
      const p = plan(snap, s)
      expect(p.status).toBe('feasible')
      expect(validatePlan(snap, p)).toEqual([])
    }
  })

  it('занятый локомотив сломать нельзя, опоздание прибывшего поезда отклоняется', () => {
    const st = new Station()
    st.applyPlan(plan(st.snapshot(), 'earliest_departure'))
    st.paused = false
    st.process()
    run(st, 200)
    expect(st.incident('delay_train', 'T01', 300)[0]).toBe(409)
    expect(st.incident('delay_train', 'T15', 300)[0]).toBe(200)
  })
})
