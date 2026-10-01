import { describe, expect, it } from 'vitest'
import { poly, pointAt, subPath, trainPx } from './geometry'

describe('геометрия маршрутов', () => {
  const p = poly('t-L', [[0, 0], [100, 0], [100, 50]])
  it('длина полилинии считается по сегментам', () => expect(p.total).toBe(150))
  it('точка по длине, а не по прямой между концами', () => {
    expect(pointAt(p, 120)).toEqual([100, 20])
    expect(pointAt(p, -5)).toEqual([0, 0])
    expect(pointAt(p, 999)).toEqual([100, 50])
  })
  it('тело состава повторяет излом маршрута', () => {
    expect(subPath(p, 90, 110)).toEqual([[90, 0], [100, 0], [100, 10]])
  })
  it('экранная длина ограничена', () => {
    expect(trainPx(50)).toBe(120)
    expect(trainPx(5000)).toBe(390)
  })
})
