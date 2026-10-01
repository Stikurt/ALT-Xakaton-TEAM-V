import { useState } from 'react'
import { selectRole, useStore, type ApDecision, type AssistantMode, type Tip } from '../app/store'
import { fmtT } from '../app/labels'
import Icon from '../app/Icon'
import s from './panels.module.css'

const STATUS: Record<string, string> = {
  off: 'выключен', watching: 'следит', replanning: 'считает варианты', deciding: 'сравнивает', applying: 'принимает план', paused: 'на паузе',
}
const KIND: Record<ApDecision['kind'], string> = {
  apply: 'принял', keep: 'оставил', skip: 'отказался', replan: 'пересчёт', error: 'ошибка', info: 'режим', advice: 'совет',
}
const MODES: { id: AssistantMode; label: string; hint: string }[] = [
  { id: 'off', label: 'Выкл', hint: 'Помощник не анализирует станцию' },
  { id: 'advise', label: 'Советы', hint: 'Сам следит, считает варианты при сбое и рекомендует; принимаете вы' },
  { id: 'auto', label: 'Автопилот', hint: 'Сам пересчитывает и принимает лучший допустимый план' },
]

export default function AutopilotCard() {
  const ap = useStore((x) => x.autopilot)
  const setMode = useStore((x) => x.setAssistantMode)
  const role = useStore(selectRole)
  const applyPlan = useStore((x) => x.applyPlan)
  const setPreview = useStore((x) => x.setPreview)
  const previewId = useStore((x) => x.previewPlanId)
  const setCompareOpen = useStore((x) => x.setCompareOpen)
  const requestReplan = useStore((x) => x.requestReplan)
  const select = useStore((x) => x.select)
  const apUpdate = useStore((x) => x.apUpdate)
  const pending = useStore((x) => x.pending)
  const [logOpen, setLogOpen] = useState(false)
  const [open, setOpen] = useState<number | null>(null)

  const runTip = (t: Tip) => {
    if (!t.action) return
    if (t.action.kind === 'replan') void requestReplan()
    else select(t.action.target)
  }

  return (
    <section className={`${s.card} ${ap.mode !== 'off' ? s.apCardOn : ''}`} data-mode={ap.mode}>
      <div className={s.cardHead}>
        <div>
          <div className="eyebrow">ИИ-помощник</div>
          <div className={s.apStatus} data-status={ap.status}>
            <i /> {STATUS[ap.status]}{(() => {
              const note = ap.status === 'watching'
                ? ap.advice ? 'есть рекомендация' : ap.mode === 'auto' ? 'сам принимает лучший допустимый план' : 'следит за станцией и даёт советы'
                : ap.note
              return note ? ` · ${note}` : ''
            })()}
          </div>
        </div>
      </div>
      <div className={s.modeSeg} role="radiogroup" aria-label="Режим помощника">
        {MODES.map((m) => (
          <button key={m.id} role="radio" aria-checked={ap.mode === m.id} className={ap.mode === m.id ? s.modeOn : ''} data-mode={m.id}
            disabled={m.id === 'auto' && role === 'viewer'} title={m.hint} onClick={() => setMode(m.id)}>
            {m.label}
          </button>
        ))}
      </div>

      {ap.mode === 'off' && (
        <p className={s.empty}>Включите «Советы», чтобы помощник следил за очередью, ожиданиями и сбоями и предлагал решения.</p>
      )}

      {ap.advice && (
        <div className={s.advice}>
          <div className={s.adviceTitle}>{ap.advice.title}</div>
          <ul className={s.apReasons}>{ap.advice.reasons.slice(0, 4).map((r, i) => <li key={i}>{r}</li>)}</ul>
          <div className={s.adviceBtns}>
            <button className="btn btn-primary btn-sm" disabled={role === 'viewer' || !!pending} onClick={() => applyPlan(ap.advice!.plan_id)}>
              <Icon name="check" size={13} /> Принять
            </button>
            <button className="btn btn-sm" onClick={() => setPreview(previewId === ap.advice!.plan_id ? null : ap.advice!.plan_id)}>
              <Icon name="eye" size={13} /> {previewId === ap.advice.plan_id ? 'Скрыть' : 'На схеме'}
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setCompareOpen(true)}>Сравнить</button>
            <button className="btn btn-ghost btn-sm" onClick={() => apUpdate({ advice: null })} aria-label="Отклонить совет">Отклонить</button>
          </div>
        </div>
      )}

      {ap.mode !== 'off' && (
        ap.tips.length === 0 ? (
          !ap.advice && <p className={s.empty}>Замечаний нет: очередь, ожидания и задержки в норме.</p>
        ) : (
          <ul className={s.tips}>
            {ap.tips.map((t) => (
              <li key={t.key} data-sev={t.severity}>
                <span className={s.tipText}>{t.text}</span>
                {t.action && (
                  <button className={s.linkBtn} disabled={t.action.kind === 'replan' && role === 'viewer'} onClick={() => runTip(t)}>
                    {t.action.kind === 'replan' ? 'Пересчитать' : 'Показать'}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )
      )}

      {ap.log.length > 0 && (
        <>
          <button className={s.logToggle} onClick={() => setLogOpen((v) => !v)} aria-expanded={logOpen}>
            Журнал решений · {ap.log.length}{ap.applied ? ` · принято планов: ${ap.applied}` : ''} {logOpen ? '▴' : '▾'}
          </button>
          {logOpen && (
            <ol className={s.apLog}>
              {ap.log.slice(0, 12).map((d) => (
                <li key={d.id} data-kind={d.kind}>
                  <button className={s.apRow} onClick={() => setOpen(open === d.id ? null : d.id)} aria-expanded={open === d.id}>
                    <span className="mono muted">{fmtT(d.at_sim)}</span>
                    <span className={s.apKind}>{KIND[d.kind]}</span>
                    <span className={s.apTitle}>{d.title}</span>
                  </button>
                  {open === d.id && d.reasons.length > 0 && <ul className={s.apReasons}>{d.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>}
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  )
}
