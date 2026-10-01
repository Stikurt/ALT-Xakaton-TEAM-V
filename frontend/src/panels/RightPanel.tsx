import { selectRole, selectView, useStore } from '../app/store'
import { CONFLICT, fmtT, INDEX_CAT } from '../app/labels'
import type { StationIndex } from '../api/types'
import Icon from '../app/Icon'
import SelectionCard from './SelectionCard'
import AutopilotCard from './AutopilotCard'
import s from './panels.module.css'

export default function RightPanel() {
  return (
    <aside className={s.right}>
      <IndexCard />
      <AutopilotCard />
      <ConflictsCard />
      <SelectionCard />
    </aside>
  )
}

/** Индекс: число, категория и шкала 0–100 с порогами 50 и 80 (как на шкальном приборе). */
export function IndexGauge({ idx, size = 92, ghost }: { idx: StationIndex | null; size?: number; ghost?: boolean }) {
  const col = !idx ? 'var(--muted)' : idx.category === 'norm' ? 'var(--ok)' : idx.category === 'attention' ? 'var(--wait)' : 'var(--closed)'
  const w = Math.round(size * 1.35)
  const v = idx ? Math.max(0, Math.min(100, idx.value)) : 0
  return (
    <div className={s.meter} style={{ width: w, opacity: ghost ? 0.7 : 1 }}
      aria-label={idx ? `Индекс ${idx.value}, ${INDEX_CAT[idx.category]}` : 'Индекс: нет данных'}>
      <div className={s.meterTop}>
        <span className={`mono ${s.meterVal}`} style={{ color: idx ? 'var(--text)' : 'var(--muted)' }}>{idx ? idx.value : '—'}</span>
        <span className={s.meterCat} style={{ color: col }}>{idx ? INDEX_CAT[idx.category] : 'нет данных'}</span>
      </div>
      <svg width={w} height={14} viewBox={`0 0 ${w} 14`} aria-hidden="true">
        <rect x={0} y={4} width={w * 0.5} height={5} fill="rgba(255,77,94,.28)" />
        <rect x={w * 0.5} y={4} width={w * 0.3} height={5} fill="rgba(255,181,59,.28)" />
        <rect x={w * 0.8} y={4} width={w * 0.2} height={5} fill="rgba(109,255,176,.25)" />
        {idx && <rect x={Math.min(w - 3, (w * v) / 100 - 1.5)} y={0} width={3} height={13} fill={col} />}
      </svg>
    </div>
  )
}

/** Тренд фактического индекса за последние 15 минут модели (значения сервера, без пересчёта). */
function Trend() {
  const pts = useStore((x) => x.indexTrend)
  if (pts.length < 2) return null
  const W = 300, H = 34
  const t0 = pts[0].t, t1 = pts[pts.length - 1].t || 1
  const xs = (t: number) => ((t - t0) / Math.max(1, t1 - t0)) * W
  const ys = (v: number) => H - 2 - (v / 100) * (H - 4)
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${xs(p.t).toFixed(1)},${ys(p.v).toFixed(1)}`).join('')
  return (
    <div className={s.trend}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" width="100%" height={H} aria-label="Индекс за последние 15 минут">
        {[50, 80].map((v) => <line key={v} x1={0} x2={W} y1={ys(v)} y2={ys(v)} stroke="var(--border-strong)" strokeDasharray="2 3" />)}
        <path d={d} fill="none" stroke="var(--occupied)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      </svg>
      <div className={s.trendAxis}><span className="mono">{fmtT(t0)}</span><span>пороги 50 и 80</span><span className="mono">{fmtT(t1)}</span></div>
    </div>
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
          <div className={s.idxSub}>{idx ? `Факт за ${Math.max(1, Math.round(idx.window_s / 60))} мин` : 'Появится после запуска'}</div>
          <div className={s.idxFormula}>I = 100 · (1 − Σ wᵢ·pᵢ)</div>
        </div>
        <IndexGauge idx={idx} />
      </div>
      <Trend />
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
