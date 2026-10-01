// Правило выбора варианта ИИ-диспетчером (без зависимостей от store — проверяется тестами).
import type { Plan, Snapshot } from '../api/types'
import type { Tip } from './store'

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



const fmtMin = (s: number) => {
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return m ? (sec ? `${m} мин ${sec} с` : `${m} мин`) : `${sec} с`
}
const TRACKS_FOR: Record<string, string[]> = {
  passenger: ['P01', 'P02'], transit: ['P03', 'P04', 'P05', 'P06'], local: ['P03', 'P04', 'P05', 'P06'],
}
const SEV = { bad: 0, warn: 1, info: 2 } as const

/** Советы по текущему снимку. Только наблюдения по данным сервера, без собственного расчёта плана или индекса. */
export function computeTips(snap: Snapshot): Tip[] {
  const now = snap.sim_time_s
  const tips: Tip[] = []
  const track = Object.fromEntries(snap.tracks.map((t) => [t.id, t]))
  const freeOf = (ids: string[]) => ids.filter((id) => track[id] && track[id].availability === 'open' && !track[id].occupant_train_id)

  // 1. Поезд долго ждёт у W, хотя подходящие пути свободны
  for (const t of snap.trains) {
    if (t.status !== 'waiting_entry') continue
    const waited = now - t.expected_arrival_s
    if (waited < 300) continue
    const free = freeOf(TRACKS_FOR[t.kind] ?? [])
    tips.push({
      key: `wait-${t.id}`, severity: waited >= 900 ? 'bad' : 'warn',
      text: free.length
        ? `${t.id} ждёт у W ${fmtMin(waited)}, а ${free.join(', ')} ${free.length > 1 ? 'свободны' : 'свободен'}. Пересчёт может сократить ожидание.`
        : `${t.id} ждёт у W ${fmtMin(waited)}: все подходящие пути заняты.`,
      action: free.length ? { kind: 'replan' } : { kind: 'select', target: { type: 'train', id: t.id } },
    })
  }
  // 2. Операция долго не может начаться
  for (const c of snap.conflicts) {
    if (c.kind !== 'execution' || now - c.start_s < 60) continue
    const tid = c.entity_ids[0]
    tips.push({ key: `exec-${c.id}`, severity: 'warn', text: `${c.message.replace(/ — /, ': ')} уже ${fmtMin(now - c.start_s)}.`,
      action: tid ? { kind: 'select', target: { type: 'train', id: tid } } : undefined })
  }
  // 3. Опаздывает пассажирский — у него высший приоритет
  for (const t of snap.trains) {
    if (t.kind !== 'passenger' || t.status === 'departed' || t.delay_s < 300) continue
    tips.push({ key: `pdelay-${t.id}`, severity: t.delay_s >= 900 ? 'bad' : 'warn',
      text: `Пассажирский ${t.id} по прогнозу отправится на ${fmtMin(t.delay_s)} позже графика. Стратегия «Пассажирские вперёд» может помочь.`,
      action: { kind: 'select', target: { type: 'train', id: t.id } } })
  }
  // 4. Все пути приёма грузовых заняты
  const freight = ['P03', 'P04', 'P05', 'P06'].filter((id) => track[id])
  const soonFreight = snap.trains.filter((t) => t.kind !== 'passenger' && (t.status === 'scheduled' || t.status === 'waiting_entry') && t.expected_arrival_s - now < 600)
  if (freight.length && !freeOf(freight).length && soonFreight.length)
    tips.push({ key: 'freight-full', severity: 'warn',
      text: `Пути P03–P06 заняты или закрыты, а в ближайшие 10 мин ${soonFreight.length === 1 ? 'подходит' : 'подходят'} ${soonFreight.map((t) => t.id).join(', ')}. Возможна очередь у W.` })
  // 5. Скоро откроется путь или вернётся локомотив
  for (const t of snap.tracks) {
    if (t.closed_until_s && t.closed_until_s - now > 0 && t.closed_until_s - now <= 120)
      tips.push({ key: `open-${t.id}`, severity: 'info', text: `${t.id} откроется через ${fmtMin(t.closed_until_s - now)}. После этого стоит пересчитать план.`, action: { kind: 'replan' } })
  }
  for (const r of snap.resources) {
    if (r.unavailable_until_s && r.unavailable_until_s - now > 0 && r.unavailable_until_s - now <= 120)
      tips.push({ key: `res-${r.id}`, severity: 'info', text: `${r.id} снова будет доступен через ${fmtMin(r.unavailable_until_s - now)}.` })
  }
  // 6. Индекс просел — назвать главный фактор
  if (snap.index && snap.index.category !== 'norm') {
    const top = [...snap.index.factors].filter((f) => !f.no_data).sort((a, b) => (b.contribution ?? 0) - (a.contribution ?? 0))[0]
    tips.push({ key: 'index', severity: snap.index.category === 'critical' ? 'bad' : 'warn',
      text: `Индекс ${snap.index.value}${top ? `: больше всего отнимает «${top.label}» (−${top.contribution})` : ''}.` })
  }
  return tips.sort((a, b) => SEV[a.severity] - SEV[b.severity]).slice(0, 6)
}
