// Правило выбора варианта ИИ-диспетчером (без зависимостей от store — проверяется тестами).
import type { Plan } from '../api/types'

/** Правило выбора (ТЗ разд. 14): допустимый → меньше неразмещённых → меньше суммарная задержка → меньше изменений. */
export function rankPlan(p: Plan): number[] {
  return [p.status === 'feasible' ? 0 : 1, p.metrics.unassigned_count, p.metrics.total_delay_s ?? Infinity, p.metrics.changed_count ?? 0]
}
export function pickBest(plans: Plan[]): Plan | null {
  const ok = plans.filter((p) => p.status === 'feasible')
  if (!ok.length) return null
  return [...ok].sort((a, b) => {
    const ra = rankPlan(a), rb = rankPlan(b)
    for (let i = 0; i < ra.length; i++) if (ra[i] !== rb[i]) return ra[i] - rb[i]
    return a.strategy < b.strategy ? -1 : 1
  })[0]
}

