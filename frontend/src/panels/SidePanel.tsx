import { selectView, useStore } from '../app/store'
import Icon from '../app/Icon'
import { fmtDur, fmtT, RES_KIND, TRAIN_KIND_SHORT, TRAIN_STATUS } from '../app/labels'
import s from './panels.module.css'

export default function SidePanel() {
  const nav = useStore((x) => x.nav)
  const setNav = useStore((x) => x.setNav)
  return (
    <aside className={s.side}>
      <div className={s.cardHead}>
        <div className="eyebrow">{nav === 'trains' ? 'Поезда · 15 по сценарию' : 'Ресурсы и горловины'}</div>
        <button className="btn btn-ghost btn-sm" onClick={() => setNav('overview')} aria-label="Закрыть"><Icon name="close" /></button>
      </div>
      {nav === 'trains' && <Trains />}
      {nav === 'resources' && <Resources />}
    </aside>
  )
}

function Trains() {
  const snap = useStore(selectView)!
  const sel = useStore((x) => x.selection)
  const select = useStore((x) => x.select)
  return (
    <table className={s.table}>
      <thead>
        <tr><th>ID</th><th>Тип</th><th>Статус</th><th>Отпр.</th><th>Задержка</th></tr>
      </thead>
      <tbody>
        {snap.trains.map((t) => (
          <tr key={t.id} className={sel?.id === t.id ? s.rowOn : ''} onClick={() => select({ type: 'train', id: t.id })}>
            <td className="mono"><b>{t.id}</b></td>
            <td>{TRAIN_KIND_SHORT[t.kind]}</td>
            <td>
              <span className={t.wait_reason ? s.warnTxt : t.status === 'departed' ? 'muted' : ''}>
                {t.wait_reason ? 'ждёт' : TRAIN_STATUS[t.status]}
              </span>
              {t.track_id && <span className="muted"> · {t.track_id}</span>}
            </td>
            <td className="mono">{fmtT(t.scheduled_departure_s)}</td>
            <td className={`mono ${t.delay_s > 0 ? s.warnTxt : 'muted'}`}>{t.delay_s > 0 ? '+' + fmtDur(t.delay_s) : '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Resources() {
  const snap = useStore(selectView)!
  const sel = useStore((x) => x.selection)
  const select = useStore((x) => x.select)
  const opById = Object.fromEntries(snap.operations.map((o) => [o.id, o]))
  return (
    <table className={s.table}>
      <thead><tr><th>ID</th><th>Вид</th><th>Состояние</th></tr></thead>
      <tbody>
        {snap.resources.map((r) => (
          <tr key={r.id} className={sel?.id === r.id ? s.rowOn : ''} onClick={() => select({ type: 'resource', id: r.id })}>
            <td className="mono"><b>{r.id}</b></td>
            <td>{RES_KIND[r.kind]}</td>
            <td className={r.availability === 'unavailable' ? s.badTxt : ''}>
              {r.availability === 'unavailable' ? `недоступен до ${fmtT(r.unavailable_until_s)}`
                : r.active_operation_id ? `занят · ${opById[r.active_operation_id]?.train_id}` : 'свободен'}
            </td>
          </tr>
        ))}
        {snap.zones.map((z) => (
          <tr key={z.id}>
            <td className="mono"><b>{z.id}</b></td>
            <td>Горловина</td>
            <td>{z.active_operation_id ? `занята · ${opById[z.active_operation_id]?.train_id}` : 'свободна'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
