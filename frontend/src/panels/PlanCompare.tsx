import { useState } from 'react'
import { useStore } from '../app/store'
import { fmtDur, STRATEGY } from '../app/labels'
import type { Plan, StationIndex } from '../api/types'
import Icon from '../app/Icon'
import { selectRole } from '../app/store'
import { Factors, IndexGauge } from './RightPanel'
import s from './panels.module.css'

const rank = (p: Plan) => [p.status === 'feasible' ? 0 : 1, p.metrics.unassigned_count, p.metrics.total_delay_s ?? 0, p.metrics.changed_count ?? 0]
const better = (a: Plan, b: Plan) => {
  const ra = rank(a), rb = rank(b)
  for (let i = 0; i < ra.length; i++) if (ra[i] !== rb[i]) return ra[i] < rb[i]
  return false
}

export default function PlanCompare() {
  const open = useStore((x) => x.compareOpen)
  const setOpen = useStore((x) => x.setCompareOpen)
  const replan = useStore((x) => x.replan)
  const snap = useStore((x) => x.snapshot)!
  const previewId = useStore((x) => x.previewPlanId)
  const setPreview = useStore((x) => x.setPreview)
  const apply = useStore((x) => x.applyPlan)
  const requestReplan = useStore((x) => x.requestReplan)
  const conn = useStore((x) => x.conn)
  const role = useStore(selectRole)
  const pending = useStore((x) => x.pending)
  if (!open) return null

  const plans = replan.identical ? replan.plans.slice(0, 1) : replan.plans
  const best = plans.length > 1 ? (better(plans[1], plans[0]) ? plans[1].id : plans[0].id) : plans[0]?.id
  const curTotal = snap.trains.reduce((acc, t) => acc + (t.status === 'departed' ? 0 : t.delay_s), 0)
  const curMax = Math.max(0, ...snap.trains.filter((t) => t.status !== 'departed').map((t) => t.delay_s))

  return (
    <div className={s.drawer} role="dialog" aria-label="Сравнение вариантов плана">
      <div className={s.cardHead}>
        <div>
          <div className="eyebrow">Перепланирование</div>
          <h3 className={s.drawerTitle}>Сравнение вариантов</h3>
          <div className="muted">Симуляция продолжает работу. Цифры вариантов — прогноз на 15 минут вперёд, не факт.</div>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)} aria-label="Закрыть"><Icon name="close" /></button>
      </div>
      {replan.status === 'running' && <div className={s.banner}><span className={s.spinner} />Идёт расчёт…</div>}
      {replan.status !== 'running' && plans.length === 0 && (
        <div className={s.empty}>
          Вариантов нет. <button className="btn btn-sm" onClick={() => requestReplan()}>Пересчитать</button>
        </div>
      )}
      {replan.identical && <div className={s.bannerInfo}>Стратегии дали одинаковый план</div>}
      <div className={s.baseline}>
        <IndexGauge idx={replan.baseline} size={74} ghost />
        <div>
          <div className="eyebrow">Если ничего не менять</div>
          <div className={s.baselineText}>
            Принятый план: задержка <b className="mono">{fmtDur(curTotal)}</b>, максимум <b className="mono">{fmtDur(curMax)}</b>,
            конфликтов <b className="mono">{snap.conflicts.length}</b>
          </div>
          <div className="muted" style={{ fontSize: 11 }}>Индекс слева — прогноз для текущего плана в том же окне, что и у вариантов.</div>
        </div>
      </div>
      <div className={s.compareGrid} data-n={plans.length}>
        {plans.map((p) => (
          <PlanCard key={p.id} p={p} best={plans.length > 1 && p.id === best} previewing={previewId === p.id}
            stale={p.based_on_epoch !== snap.epoch || p.run_id !== snap.run_id}
            canCmd={conn === 'online' && role !== 'viewer' && !pending}
            onPreview={() => setPreview(previewId === p.id ? null : p.id)}
            onApply={() => apply(p.id)} onReplan={() => requestReplan()} identical={replan.identical} baseline={replan.baseline} />
        ))}
      </div>
    </div>
  )
}

function PlanCard(props: {
  p: Plan; best: boolean; previewing: boolean; stale: boolean; canCmd: boolean; identical: boolean; baseline: StationIndex | null
  onPreview: () => void; onApply: () => void; onReplan: () => void
}) {
  const { p } = props
  const [all, setAll] = useState(false)
  const m = p.metrics
  const reasons: string[] = []
  if (props.stale) reasons.push('План устарел: после расчёта изменилась обстановка')
  if (p.status !== 'feasible') reasons.push(p.status === 'partial' ? 'Неполный план: часть поездов не размещена' : 'Недопустимый план')
  if (!props.canCmd) reasons.push('Нет связи или недостаточно прав')
  const expl = all ? p.explanations : p.explanations.slice(0, 5)
  return (
    <div className={`${s.planCard} ${props.best ? s.planBest : ''} ${props.previewing ? s.planPreview : ''}`}>
      <div className={s.planHead}>
        <div className={s.planTitle}>{props.identical ? 'Обе стратегии' : STRATEGY[p.strategy]}</div>
        <div className={s.chips}>
          <span className={p.status === 'feasible' ? 'chip chip-ok' : 'chip chip-bad'}>
            {p.status === 'feasible' ? 'допустимый' : p.status === 'partial' ? 'неполный' : p.status === 'timeout' ? 'тайм-аут' : 'недопустимый'}
          </span>
          {props.best && <span className="chip chip-info">рекомендуем</span>}
          {p.timed_out && <span className="chip chip-wait">по тайм-ауту</span>}
        </div>
        <div className="muted mono" style={{ fontSize: 11 }}>{p.id}{p.calc_ms !== null ? ` · расчёт ${p.calc_ms} мс` : ''}</div>
      </div>
      {p.index_forecast && (
        <div className={s.planIdx}>
          <IndexGauge idx={p.index_forecast} size={84} />
          <div>
            <div className="eyebrow">Индекс · прогноз</div>
            {props.baseline && (
              <div className={`mono ${s.delta}`} data-sign={Math.sign(p.index_forecast.value - props.baseline.value)}>
                {p.index_forecast.value - props.baseline.value >= 0 ? '+' : '−'}{Math.abs(p.index_forecast.value - props.baseline.value)} к текущему плану
              </div>
            )}
          </div>
        </div>
      )}
      {p.index_forecast && <Factors idx={p.index_forecast} compare={props.baseline} />}
      <div className={s.metrics}>
        <Metric label="Суммарная задержка" value={fmtDur(m.total_delay_s)} />
        <Metric label="Макс. задержка" value={fmtDur(m.max_delay_s)} />
        <Metric label="Не размещено" value={String(m.unassigned_count)} bad={m.unassigned_count > 0} />
        <Metric label="Переназначений" value={m.changed_count === null ? '—' : String(m.changed_count)} />
      </div>
      {p.unassigned.length > 0 && (
        <ul className={s.unassigned}>{p.unassigned.map((u, i) => <li key={i}>{u.message}</li>)}</ul>
      )}
      {p.violations.length > 0 && (
        <ul className={s.unassigned}>{p.violations.slice(0, 4).map((v, i) => <li key={i}>{v.message}</li>)}</ul>
      )}
      <h4 className={`${s.h4} eyebrow`}>Что меняется и почему</h4>
      {p.explanations.length === 0 ? <p className={s.empty}>Будущие назначения не изменились</p> : (
        <ul className={s.expl}>
          {expl.map((e) => (
            <li key={`${e.train_id}-${e.code}-${e.message.length}`}>
              <span className={`chip ${e.code === 'RESCHEDULED' ? 'chip-muted' : 'chip-wait'}`}>{e.train_id}</span> {e.message}
            </li>
          ))}
        </ul>
      )}
      {p.explanations.length > 5 && (
        <button className={s.linkBtn} onClick={() => setAll((v) => !v)}>{all ? 'Свернуть' : `Ещё ${p.explanations.length - 5}`}</button>
      )}
      <div className={s.planFoot}>
        <button className={props.previewing ? 'btn btn-sm ' + s.previewOn : 'btn btn-sm'} onClick={props.onPreview}>
          <Icon name="eye" size={14} />{props.previewing ? 'Показано на схеме' : 'Показать на схеме'}
        </button>
        {props.stale ? (
          <button className="btn btn-sm" onClick={props.onReplan}><Icon name="replan" size={14} />Пересчитать</button>
        ) : (
          <button className="btn btn-primary btn-sm" disabled={reasons.length > 0} onClick={props.onApply}
            title={reasons.join('. ')}><Icon name="check" size={14} />Принять план</button>
        )}
      </div>
      {reasons.length > 0 && <div className={s.reasons}>{reasons.join(' · ')}</div>}
    </div>
  )
}

function Metric({ label, value, bad }: { label: string; value: string; bad?: boolean }) {
  return (
    <div className={s.metric}>
      <span className="muted">{label}</span>
      <b className={`mono ${bad ? s.warnTxt : ''}`}>{value}</b>
    </div>
  )
}
