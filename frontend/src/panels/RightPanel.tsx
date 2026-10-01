import { useStore } from '../app/store'
import { CONFLICT, fmtT, INDEX_CAT } from '../app/labels'
import SelectionCard from './SelectionCard'
import s from './panels.module.css'

export default function RightPanel() {
  return (
    <aside className={s.right}>
      <IndexCard />
      <ConflictsCard />
      <SelectionCard />
    </aside>
  )
}

function IndexCard() {
  const idx = useStore((x) => x.snapshot!.index)
  if (!idx) {
    return (
      <section className={s.card}>
        <h3 className={s.h3}>Индекс эффективности</h3>
        <p className="muted">Нет данных — индекс появится после начала работы станции.</p>
      </section>
    )
  }
  const top = [...idx.factors].sort((a, b) => (b.contribution ?? -1) - (a.contribution ?? -1))
  return (
    <section className={s.card}>
      <div className={s.idxHead}>
        <div>
          <h3 className={s.h3}>Индекс эффективности</h3>
          <div className="muted" style={{ fontSize: 11 }}>факт · окно {Math.round(idx.window_s / 60)} мин · вклад штрафов</div>
        </div>
        <div className={`${s.idxBig} ${s['cat_' + idx.category]}`}>
          <span className="mono">{idx.value}</span>
          <small>{INDEX_CAT[idx.category]}</small>
        </div>
      </div>
      <ul className={s.factors}>
        {top.map((f) => (
          <li key={f.id}>
            <span className={s.fLabel}>{f.label}</span>
            <span className={s.fBar}>
              <i style={{ width: `${Math.min(100, ((f.contribution ?? 0) / (f.weight * 100)) * 100)}%` }}
                className={(f.penalty ?? 0) > 0.5 ? s.fBad : (f.penalty ?? 0) > 0 ? s.fWarn : s.fOk} />
            </span>
            <span className={`mono ${s.fVal}`}>{f.no_data ? 'нет данных' : `−${f.contribution}`}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function ConflictsCard() {
  const snap = useStore((x) => x.snapshot)!
  const selection = useStore((x) => x.selection)
  const select = useStore((x) => x.select)
  const replan = useStore((x) => x.replan)
  const requestReplan = useStore((x) => x.requestReplan)
  const setCompareOpen = useStore((x) => x.setCompareOpen)
  const conn = useStore((x) => x.conn)
  const role = useStore((x) => x.role)
  const list = [...snap.conflicts].sort((a, b) => (a.severity === b.severity ? a.start_s - b.start_s : a.severity === 'high' ? -1 : 1))
  const stale = replan.status === 'done' && replan.finished_at_epoch !== snap.epoch
  return (
    <section className={s.card}>
      <div className={s.cardHead}>
        <h3 className={s.h3}>
          Конфликты {list.length > 0 && <span className="chip chip-bad">{list.length}</span>}
        </h3>
        <button className="btn btn-sm" disabled={conn !== 'online' || role === 'viewer' || replan.status === 'running'}
          onClick={() => requestReplan()}>
          {replan.status === 'running' ? 'Расчёт…' : '⟳ Пересчитать'}
        </button>
      </div>
      {replan.status === 'running' && <div className={s.banner}><span className={s.spinner} />Идёт расчёт двух вариантов плана…</div>}
      {replan.status === 'done' && !stale && (
        <button className={s.bannerAction} onClick={() => setCompareOpen(true)}>
          Готово {replan.plans.length} варианта за {replan.calc_ms} мс → сравнить и принять
        </button>
      )}
      {stale && (
        <div className={s.bannerWarn}>Варианты устарели — обстановка изменилась. Нужен пересчёт.</div>
      )}
      {list.length === 0 ? (
        <p className={s.empty}>✓ Конфликтов нет. Принятый план исполняется.</p>
      ) : (
        <ul className={s.conflicts}>
          {list.map((c) => (
            <li key={c.id}>
              <button className={`${s.conflict} ${selection?.id === c.id ? s.conflictOn : ''}`}
                onClick={() => select(selection?.id === c.id ? null : { type: 'conflict', id: c.id })}>
                <span className={c.severity === 'high' ? 'chip chip-bad' : 'chip chip-wait'}>
                  {c.kind === 'execution' ? '● сейчас' : '◌ по плану'}
                </span>
                <span className={s.cCode}>{CONFLICT[c.code] ?? c.code}</span>
                <span className={s.cMsg}>{c.message}</span>
                <span className={`mono muted ${s.cTime}`}>
                  {c.kind === 'execution' ? `с ${fmtT(c.start_s)}` : `${fmtT(c.start_s)}${c.end_s ? ` → ${fmtT(c.end_s)}` : ''}`}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
