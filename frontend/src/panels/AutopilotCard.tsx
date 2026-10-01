import { useState } from 'react'
import { selectRole, useStore, type ApDecision } from '../app/store'
import { fmtT } from '../app/labels'
import s from './panels.module.css'

const STATUS: Record<string, string> = {
  off: 'выключен', watching: 'наблюдает', replanning: 'ждёт расчёт', deciding: 'сравнивает варианты', applying: 'принимает план', paused: 'на паузе',
}
const KIND: Record<ApDecision['kind'], string> = { apply: 'принял', keep: 'оставил', skip: 'отказался', replan: 'пересчёт', error: 'ошибка', info: 'режим' }

export default function AutopilotCard() {
  const ap = useStore((x) => x.autopilot)
  const set = useStore((x) => x.setAutopilot)
  const role = useStore(selectRole)
  const [open, setOpen] = useState<number | null>(null)
  return (
    <section className={`${s.card} ${ap.enabled ? s.apCardOn : ''}`}>
      <div className={s.cardHead}>
        <div>
          <div className="eyebrow">ИИ-диспетчер</div>
          <div className={s.apStatus} data-status={ap.status}>
            <i /> {STATUS[ap.status]}{ap.note ? ` · ${ap.note}` : ''}
          </div>
        </div>
        <button role="switch" aria-checked={ap.enabled} className={s.switch} data-on={ap.enabled} disabled={role === 'viewer'}
          onClick={() => set(!ap.enabled)} title="A">
          <span />
        </button>
      </div>
      {!ap.enabled && ap.log.length === 0 && (
        <p className={s.empty}>
          Включите, чтобы при конфликте или сбое план пересчитывался и лучший допустимый вариант принимался автоматически.
          Правило выбора: все поезда размещены → меньше суммарная задержка → меньше переназначений. Каждое решение проверяет сервер.
        </p>
      )}
      {ap.applied > 0 && <div className={s.apStat}>Принято планов: <b className="mono">{ap.applied}</b></div>}
      {ap.log.length > 0 && (
        <ol className={s.apLog}>
          {ap.log.slice(0, 8).map((d) => (
            <li key={d.id} data-kind={d.kind}>
              <button className={s.apRow} onClick={() => setOpen(open === d.id ? null : d.id)} aria-expanded={open === d.id}>
                <span className="mono muted">{fmtT(d.at_sim)}</span>
                <span className={s.apKind}>{KIND[d.kind]}</span>
                <span className={s.apTitle}>{d.title}</span>
              </button>
              {(open === d.id || (d === ap.log[0] && d.kind === 'apply')) && d.reasons.length > 0 && (
                <ul className={s.apReasons}>{d.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
