import { useMemo, useState } from 'react'
import { useStore, type JEvent } from '../app/store'
import { fmtT } from '../app/labels'
import s from './Journal.module.css'

const KIND: Record<JEvent['kind'], string> = {
  arrive: 'прибытие', enter: 'приём', depart: 'отправление', incident: 'сбой', plan: 'план', conflict: 'конфликт', resolved: 'снято', run: 'запуск',
}
const FILTERS: { id: string; label: string; kinds: JEvent['kind'][] | null }[] = [
  { id: 'all', label: 'Все', kinds: null },
  { id: 'trains', label: 'Поезда', kinds: ['arrive', 'enter', 'depart'] },
  { id: 'problems', label: 'Сбои и конфликты', kinds: ['incident', 'conflict', 'resolved'] },
  { id: 'plans', label: 'Планы', kinds: ['plan'] },
]

/** Журнал событий запуска: что произошло на станции, по модельному времени. */
export default function Journal() {
  const journal = useStore((x) => x.journal)
  const ap = useStore((x) => x.autopilot.log)
  const select = useStore((x) => x.select)
  const [f, setF] = useState('all')
  const rows = useMemo(() => {
    const flt = FILTERS.find((x) => x.id === f)!.kinds
    const base = flt ? journal.filter((e) => flt.includes(e.kind)) : journal
    if (f !== 'all' && f !== 'plans') return base
    const aiRows = ap.filter((d) => d.kind === 'apply' || d.kind === 'advice').map((d) => ({
      id: -d.id, t: d.at_sim, kind: 'plan' as const, text: `ИИ-помощник: ${d.title}`,
    }))
    return [...base, ...aiRows].sort((a, b) => b.t - a.t || b.id - a.id)
  }, [journal, ap, f])
  return (
    <div className={s.wrap}>
      <div className={s.filters} role="tablist">
        {FILTERS.map((x) => (
          <button key={x.id} role="tab" aria-selected={f === x.id} className={f === x.id ? s.on : ''} onClick={() => setF(x.id)}>{x.label}</button>
        ))}
        <span className={s.count}>{rows.length} событий</span>
      </div>
      {rows.length === 0 ? (
        <p className={s.empty}>Событий пока нет. Запустите симуляцию — здесь появятся прибытия, отправления, сбои и принятые планы.</p>
      ) : (
        <ol className={s.list}>
          {rows.map((e) => (
            <li key={e.id} data-kind={e.kind} data-bad={'bad' in e && e.bad ? 'true' : undefined}>
              <span className={s.t}>{fmtT(e.t)}</span>
              <span className={s.k}>{KIND[e.kind]}</span>
              {'train' in e && e.train ? (
                <button className={s.txtBtn} onClick={() => select({ type: 'train', id: e.train! })}>{e.text}</button>
              ) : <span className={s.txt}>{e.text}</span>}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
