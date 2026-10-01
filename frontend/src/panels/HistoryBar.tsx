import { useStore } from '../app/store'
import { fmtT } from '../app/labels'
import Icon from '../app/Icon'
import s from './panels.module.css'

export default function HistoryBar() {
  const h = useStore((x) => x.history)!
  const online = useStore((x) => x.snapshot)!
  const seek = useStore((x) => x.seekHistory)
  const close = useStore((x) => x.closeHistory)
  const to = online.sim_time_s
  const from = Math.max(0, to - 15 * 60)
  const ago = Math.max(0, to - h.at_s)
  return (
    <div className={s.historyBar} role="region" aria-label="Просмотр истории">
      <span className={s.historyTag}>ИСТОРИЯ</span>
      <div className={s.historyInfo}>
        <b className="mono">{fmtT(h.at_s)}</b>
        <span className="muted">{ago ? `${Math.floor(ago / 60)} мин ${ago % 60} с назад` : 'текущий момент'}</span>
      </div>
      <div className={s.historyJump}>
        {[-300, -60, -10].map((d) => (
          <button key={d} className="btn btn-sm" onClick={() => seek(h.at_s + d)} disabled={h.at_s <= from}>
            {d <= -60 ? `−${-d / 60} мин` : `−${-d} с`}
          </button>
        ))}
      </div>
      <input type="range" id="history-slider" className={s.historyRange} min={from} max={to} step={5} value={h.at_s}
        onChange={(e) => seek(Number(e.target.value))} aria-label="Момент просмотра" />
      <div className={s.historyJump}>
        {[10, 60].map((d) => (
          <button key={d} className="btn btn-sm" onClick={() => seek(h.at_s + d)} disabled={h.at_s >= to}>
            {d >= 60 ? `+${d / 60} мин` : `+${d} с`}
          </button>
        ))}
      </div>
      <span className={s.historyState}>
        {h.loading ? 'загрузка…' : h.error ? <span className={s.badTxt}>{h.error}</span> : 'команды недоступны'}
      </span>
      <button className="btn btn-primary btn-sm" onClick={close}>
        <Icon name="back" size={14} /> В онлайн
      </button>
    </div>
  )
}
