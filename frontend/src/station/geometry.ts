import type { Pt } from '../api/types'

export interface Poly { pts: Pt[]; cum: number[]; total: number }

const cache = new Map<string, Poly>()

export function poly(id: string, pts: Pt[]): Poly {
  const c = cache.get(id)
  if (c) return c
  const cum = [0]
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1]
    const [x1, y1] = pts[i]
    cum.push(cum[i - 1] + Math.hypot(x1 - x0, y1 - y0))
  }
  const p = { pts, cum, total: cum[cum.length - 1] }
  cache.set(id, p)
  return p
}

/** Точка на полилинии по длине (а не по прямой между концами). */
export function pointAt(p: Poly, d: number): Pt {
  const dd = Math.max(0, Math.min(p.total, d))
  for (let i = 1; i < p.pts.length; i++) {
    if (dd <= p.cum[i] || i === p.pts.length - 1) {
      const seg = p.cum[i] - p.cum[i - 1] || 1
      const t = (dd - p.cum[i - 1]) / seg
      const [x0, y0] = p.pts[i - 1]
      const [x1, y1] = p.pts[i]
      return [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]
    }
  }
  return p.pts[p.pts.length - 1]
}

/** Участок полилинии между двумя расстояниями — «тело» состава. */
export function subPath(p: Poly, a: number, b: number): Pt[] {
  const from = Math.max(0, Math.min(p.total, a))
  const to = Math.max(0, Math.min(p.total, b))
  const out: Pt[] = [pointAt(p, from)]
  for (let i = 1; i < p.pts.length - 1; i++) {
    if (p.cum[i] > from && p.cum[i] < to) out.push(p.pts[i])
  }
  out.push(pointAt(p, to))
  return out
}

export function headingAt(p: Poly, d: number): number {
  const a = pointAt(p, d - 2)
  const b = pointAt(p, d + 2)
  return Math.atan2(b[1] - a[1], b[0] - a[0])
}

export const toPath = (pts: Pt[]) => pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join('')

/** Условная экранная длина состава: ограничена для читаемости, логику не меняет. */
export const trainPx = (length_m: number) => Math.max(120, Math.min(length_m * 0.48, 390))
