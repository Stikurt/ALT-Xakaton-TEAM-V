import { useEffect, useState } from 'react'
import { useStore } from '../app/store'
import Icon from '../app/Icon'
import { fmtT } from '../app/labels'
import type { IncidentCmd, IncidentKind } from '../api/types'
import s from './panels.module.css'

const KINDS: { id: IncidentKind; label: string; hint: string }[] = [
  { id: 'close_track', label: 'Закрытие пути', hint: 'Новые входы на путь запрещены на заданное время. Состав на пути может закончить работы и выйти. Закрытие во время уже начатого движения на этот путь отклоняется.' },
  { id: 'delay', label: 'Опоздание поезда', hint: 'Применимо только к поезду, который ещё не прибыл (scheduled). Прибытие переносится на заданную величину.' },
  { id: 'loco_unavailable', label: 'Недоступность локомотива', hint: 'Только для свободного маневрового локомотива: новые назначения запрещены. Поломка в движении — вне первой версии.' },
]

export default function IncidentDialog() {
  const open = useStore((x) => x.incidentOpen)
  const setOpen = useStore((x) => x.setIncidentOpen)
  const snap = useStore((x) => x.snapshot)!
  const incident = useStore((x) => x.incident)
  const pending = useStore((x) => x.pending)
  const [kind, setKind] = useState<IncidentKind>('close_track')
  const [target, setTarget] = useState('')
  const [dur, setDur] = useState(600)
  const [batch, setBatch] = useState<IncidentCmd[]>([])

  const options =
    kind === 'close_track'
      ? snap.tracks.filter((t) => t.id !== 'P12').map((t) => ({
          id: t.id,
          label: `${t.id} · ${t.availability === 'closed' ? 'уже закрыт' : t.occupant_train_id ? `занят ${t.occupant_train_id}` : 'свободен'}`,
          disabled: t.availability === 'closed',
        }))
      : kind === 'delay'
        ? snap.trains.filter((t) => t.status === 'scheduled').map((t) => ({
            id: t.id, label: `${t.id} · прибытие ${fmtT(t.expected_arrival_s)}`, disabled: false,
          }))
        : snap.resources.filter((r) => r.kind === 'shunting_loco').map((r) => ({
            id: r.id,
            label: `${r.id} · ${r.availability === 'unavailable' ? 'уже недоступен' : r.active_operation_id ? 'занят — нельзя' : 'свободен'}`,
            disabled: r.availability === 'unavailable' || !!r.active_operation_id,
          }))

  useEffect(() => {
    const first = options.find((o) => !o.disabled)
    if (!options.some((o) => o.id === target && !o.disabled)) setTarget(first?.id ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, open, snap.state_version])

  useEffect(() => {
    setDur(kind === 'delay' ? 300 : 600)
  }, [kind])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [setOpen])

  if (!open) return null
  const k = KINDS.find((x) => x.id === kind)!
  const invalid = !target || dur <= 0 || dur > 7200

  const current = (): IncidentCmd => (kind === 'delay' ? { kind, target_id: target, delay_s: dur } : { kind, target_id: target, duration_s: dur })
  const inBatch = (c: IncidentCmd) => batch.some((b) => b.kind === c.kind && b.target_id === c.target_id)
  const addToBatch = () => {
    if (invalid || inBatch(current())) return
    setBatch((b) => [...b, current()])
  }
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    const items = batch.length ? [...batch, ...(invalid || inBatch(current()) ? [] : [current()])] : invalid ? [] : [current()]
    if (!items.length) return
    const ok = await incident(items.length === 1 ? items[0] : items)
    if (ok) {
      setBatch([])
      setOpen(false)
    }
  }
  const label = (c: IncidentCmd) => `${KINDS.find((k) => k.id === c.kind)!.label}: ${c.target_id}, ${Math.round((c.duration_s ?? c.delay_s ?? 0) / 60)} мин`

  return (
    <div className={s.modalBack} onClick={() => setOpen(false)}>
      <form className={s.modal} onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <div className={s.cardHead}>
          <h3 className={s.h3}>Внести нештатную ситуацию</h3>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)} aria-label="Закрыть"><Icon name="close" /></button>
        </div>
        <div className={s.tabs}>
          {KINDS.map((x) => (
            <button type="button" key={x.id} className={kind === x.id ? s.tabOn : ''} onClick={() => setKind(x.id)}>{x.label}</button>
          ))}
        </div>
        <p className={s.hint}>{k.hint}</p>
        <label className={s.field}>
          <span>{kind === 'close_track' ? 'Путь' : kind === 'delay' ? 'Поезд' : 'Локомотив'}</span>
          <select id="incident-target" value={target} onChange={(e) => setTarget(e.target.value)}>
            {options.length === 0 && <option value="">Нет подходящих объектов</option>}
            {options.map((o) => <option key={o.id} value={o.id} disabled={o.disabled}>{o.label}</option>)}
          </select>
        </label>
        <label className={s.field}>
          <span>{kind === 'delay' ? 'Опоздание, модельных секунд' : 'Длительность, модельных секунд'}</span>
          <input id="incident-duration" type="number" min={60} max={7200} step={60} value={dur} onChange={(e) => setDur(Number(e.target.value))} />
          <small className="muted">≈ {Math.round(dur / 60)} мин. Сейчас {fmtT(snap.sim_time_s)}{kind !== 'delay' ? `, до ${fmtT(snap.sim_time_s + dur)}` : ''}</small>
        </label>
        {batch.length > 0 && (
          <div className={s.batch}>
            <div className="eyebrow">Пакет сбоев: применяются вместе, затем один пересчёт</div>
            <ul>
              {batch.map((c, i) => (
                <li key={i}>
                  <span>{label(c)}</span>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setBatch((b) => b.filter((_, j) => j !== i))} aria-label="Убрать из пакета">
                    <Icon name="close" size={12} />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        <div className={s.modalFoot}>
          <button type="button" className="btn btn-sm" onClick={addToBatch} disabled={invalid || inBatch(current())}
            title="Собрать несколько сбоев и внести их одной командой">
            <Icon name="plus" size={12} /> В пакет
          </button>
          <button type="submit" className="btn btn-danger" disabled={(invalid && !batch.length) || !!pending}>
            <Icon name="alert" size={14} />
            {batch.length ? `Внести пакет (${batch.length + (invalid || inBatch(current()) ? 0 : 1)})` : 'Внести сбой'}
          </button>
        </div>
      </form>
    </div>
  )
}
