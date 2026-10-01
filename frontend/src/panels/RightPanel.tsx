import { selectRole, selectView, useStore } from '../app/store'
import { CONFLICT, fmtT, INDEX_CAT } from '../app/labels'
import type { StationIndex } from '../api/types'
import Icon from '../app/Icon'
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

/** Дуговой индикатор 0–100 с порогами 50 и 80. */
export function IndexGauge({ idx, size = 92, ghost }: { idx: StationIndex | null; size?: number; ghost?: boolean }) {
  const r = 38
  const c = Math.PI * r
  const v = idx ? idx.value / 100 : 0
  const col = !idx ? 'var(--muted)' : idx.category === 'norm' ? 'var(--ok)' : idx.category === 'attention' ? 'var(--wait)' : 'var(--closed)'
  const tick = (p: number) => {
    const a = Math.PI * (1 - p)
    return { x1: 50 + Math.cos(a) * 31, y1: 50 - Math.sin(a) * 31, x2: 50 + Math.cos(a) * 45, y2: 50 - Math.sin(a) * 45 }
  }
  return (
    <svg width={size} height={size * 0.62} viewBox="0 0 100 62" aria-label={idx ? `Индекс ${idx.value}, ${INDEX_CAT[idx.category]}` : 'Индекс: нет данных'}>
      <path d="M12 50 A38 38 0 0 1 88 50" fill="none" stroke="var(--panel-2)" strokeWidth="8" strokeLinecap="round" />
      <path d="M12 50 A38 38 0 0 1 88 50" fill="none" stroke={col} strokeWidth="8" strokeLinecap="round"
        strokeDasharray={`${c * v} ${c}`} style={{ filter: ghost ? undefined : `drop-shadow(0 0 4px ${col})`, opacity: ghost ? 0.55 : 1 }} />
      {[0.5, 0.8].map((p) => <line key={p} {...tick(p)} stroke="var(--bg)" strokeWidth="1.5" />)}
      <text x="50" y="47" textAnchor="middle" fill="var(--text)" style={{ font: '800 22px var(--mono)' }}>{idx ? idx.value : '—'}</text>
      <text x="50" y="60" textAnchor="middle" fill={col} style={{ font: '600 8px var(--display)', letterSpacing: '.12em' }}>
        {idx ? INDEX_CAT[idx.category].toUpperCase() : 'НЕТ ДАННЫХ'}
      </text>
    </svg>
  )
}

export function Factors({ idx, compare }: { idx: StationIndex; compare?: StationIndex | null }) {
  const cmp = Object.fromEntries((compare?.factors ?? []).map((f) => [f.id, f]))
  return (
    <ul className={s.factors}>
      {idx.factors.map((f) => {
        const max = f.weight * 100
        const w = f.no_data ? 0 : Math.min(100, ((f.contribution ?? 0) / max) * 100)
        const other = cmp[f.id]
        return (
          <li key={f.id} title={f.no_data ? 'Нет данных в окне — вес исключён' : `Штраф ${f.penalty} × вес ${f.weight}`}>
            <span className={s.fLabel}>{f.label}</span>
            <span className={s.fBar}>
              <i style={{ width: `${w}%` }} className={(f.penalty ?? 0) > 0.5 ? s.fBad : (f.penalty ?? 0) > 0.05 ? s.fWarn : s.fOk} />
              {other && !other.no_data && <b style={{ left: `${Math.min(100, ((other.contribution ?? 0) / max) * 100)}%` }} />}
            </span>
            <span className={`mono ${s.fVal}`}>{f.no_data ? 'н/д' : `−${f.contribution}`}</span>
          </li>
        )
      })}
    </ul>
  )
}

function IndexCard() {
  const idx = useStore((x) => selectView(x)!.index)
  return (
    <section className={s.card}>
      <div className={s.idxHead}>
        <div>
          <div className="eyebrow">Индекс эффективности</div>
          <div className={s.idxSub}>{idx ? `факт · окно ${Math.max(1, Math.round(idx.window_s / 60))} мин` : 'появится после запуска'}</div>
          <div className={s.idxFormula}>I = 100 · (1 − Σ wᵢ·pᵢ)</div>
        </div>
        <IndexGauge idx={idx} />
      </div>
      {idx && <Factors idx={idx} />}
    </section>
  )
}

function ConflictsCard() {
  const snap = useStore(selectView)!
  const online = useStore((x) => x.snapshot)!
  const selection = useStore((x) => x.selection)
  const select = useStore((x) => x.select)
  const replan = useStore((x) => x.replan)
  const history = useStore((x) => x.history)
  const requestReplan = useStore((x) => x.requestReplan)
  const setCompareOpen = useStore((x) => x.setCompareOpen)
  const conn = useStore((x) => x.conn)
  const role = useStore(selectRole)
  const list = [...snap.conflicts].sort((a, b) => (a.severity === b.severity ? a.start_s - b.start_s : a.severity === 'high' ? -1 : 1))
  const stale = replan.status === 'done' && replan.finished_at_epoch !== online.epoch
  const canCmd = conn === 'online' && role !== 'viewer' && !history
  return (
    <section className={s.card}>
      <div className={s.cardHead}>
        <div className="eyebrow">
          Конфликты <span className={list.length ? s.countBad : s.countOk}>{list.length}</span>
        </div>
        <button className="btn btn-sm" disabled={!canCmd || replan.status === 'running'} onClick={() => requestReplan()} title="R">
          <Icon name="replan" size={14} /> {replan.status === 'running' ? 'Расчёт…' : 'Пересчитать'}
        </button>
      </div>
      {!history && replan.status === 'running' && <div className={s.banner}><span className={s.spinner} />Считаем два варианта плана</div>}
      {!history && replan.status === 'done' && !stale && (
        <button className={s.bannerAction} onClick={() => setCompareOpen(true)}>
          <span>Готово: {replan.identical || replan.plans.length === 1 ? '1 вариант' : `${replan.plans.length} варианта`}{replan.calc_ms !== null ? ` · ${replan.calc_ms} мс` : ''}</span>
          <span>Сравнить <span className="kbd">C</span></span>
        </button>
      )}
      {!history && stale && <div className={s.bannerWarn}>Варианты устарели: после расчёта обстановка изменилась.</div>}
      {list.length === 0 ? (
        <p className={s.empty}>
          {snap.active_plan_id
            ? 'Конфликтов нет. Принятый план исполняется без ожиданий.'
            : 'Конфликтов нет, но принятого плана пока нет: запуск станции станет доступен после расчёта и принятия плана.'}
        </p>
      ) : (
        <ul className={s.conflicts}>
          {list.map((c) => (
            <li key={c.id}>
              <button className={`${s.conflict} ${selection?.id === c.id ? s.conflictOn : ''}`} data-sev={c.severity}
                onClick={() => select(selection?.id === c.id ? null : { type: 'conflict', id: c.id })}>
                <span className={s.cCode}>{CONFLICT[c.code] ?? c.code}</span>
                <span className={`mono ${s.cTime}`}>
                  {c.kind === 'execution' ? `ждёт с ${fmtT(c.start_s)}` : `${fmtT(c.start_s)}${c.end_s ? `–${fmtT(c.end_s)}` : ''}`}
                </span>
                <span className={s.cMsg}>{c.message}</span>
                <span className={s.cKind}>{c.kind === 'execution' ? 'сейчас' : 'в плане'}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
