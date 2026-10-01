import { useState } from 'react'
import { selectRole, useStore } from '../app/store'
import { fmtT, INDEX_CAT, useSimNow } from '../app/labels'
import Icon from '../app/Icon'
import { transport } from '../api/transport'
import s from './panels.module.css'

const ROLE_LABEL = { viewer: 'Наблюдатель', dispatcher: 'Диспетчер', admin: 'Администратор' } as const

export default function TopBar() {
  const snap = useStore((x) => x.snapshot)!
  const conn = useStore((x) => x.conn)
  const lastUpdate = useStore((x) => x.lastUpdate)
  const pending = useStore((x) => x.pending)
  const role = useStore(selectRole)
  const user = useStore((x) => x.user)
  const history = useStore((x) => x.history)
  const control = useStore((x) => x.control)
  const setIncidentOpen = useStore((x) => x.setIncidentOpen)
  const setHelpOpen = useStore((x) => x.setHelpOpen)
  const openCsv = useStore((x) => x.openCsv)
  const logout = useStore((x) => x.logout)
  const now = useSimNow(10)
  const [confirmReset, setConfirmReset] = useState(false)
  const offline = conn !== 'online'
  const canCmd = !offline && role !== 'viewer' && !history
  const started = snap.sim_time_s > 0
  const idx = snap.index
  const running = !snap.paused && !offline && !history

  return (
    <header className={s.topbar}>
      <div className={s.brand}>
        <svg width="30" height="30" viewBox="0 0 30 30" aria-hidden="true" className={s.brandMark}>
          <path d="M3 10h24M3 15h24M3 20h24" stroke="currentColor" strokeWidth="1.4" opacity=".35" />
          <path d="M3 15 L10 15 L16 10 L27 10" stroke="currentColor" strokeWidth="2.2" fill="none" />
          <circle cx="16" cy="10" r="2.2" fill="currentColor" />
        </svg>
        <div>
          <div className={s.brandName}>УЗЕЛ 12</div>
          <div className={s.brandSub}>{transport().demo ? 'демо в браузере' : 'учебная станция'} · <span className="mono">{snap.run_id}</span></div>
        </div>
      </div>

      <div className={s.clock} data-running={running} data-history={!!history}
        title="Модельное время от начала сценария, не время суток">
        <span className="eyebrow">{history ? 'история' : 'модельное время'}</span>
        <span className={`mono ${s.clockValue}`}>{fmtT(now)}</span>
        <span className={s.clockSub}>{history ? `онлайн ${fmtT(snap.sim_time_s)}` : `горизонт ${fmtT(7200)}`}</span>
      </div>

      <div className={s.controls}>
        {snap.paused ? (
          <button className="btn btn-primary" disabled={!canCmd || !!pending} onClick={() => control('start')}
            title={canCmd ? 'Пробел' : ''}>
            <Icon name="play" size={14} /> {started ? 'Продолжить' : 'Запустить'}
          </button>
        ) : (
          <button className="btn" disabled={!canCmd || !!pending} onClick={() => control('pause')} title="Пробел">
            <Icon name="pause" size={14} /> Пауза
          </button>
        )}
        <div className={s.seg} role="group" aria-label="Скорость модельного времени">
          {[1, 5, 10].map((v) => (
            <button key={v} className={snap.speed === v ? s.segOn : ''} disabled={!canCmd}
              onClick={() => control('speed', v)} aria-pressed={snap.speed === v}>×{v}</button>
          ))}
        </div>
        <button className={confirmReset ? 'btn btn-danger' : 'btn btn-ghost'} disabled={!canCmd || !!pending}
          onClick={() => {
            if (!confirmReset) {
              setConfirmReset(true)
              window.setTimeout(() => setConfirmReset(false), 3000)
            } else {
              setConfirmReset(false)
              void control('reset')
            }
          }}
          title="Новый запуск с исходным сценарием">
          <Icon name="reset" size={14} /> {confirmReset ? 'Нажмите ещё раз' : 'Сброс'}
        </button>
        <span className={s.sep} />
        <button className="btn btn-danger" disabled={!canCmd || !started} onClick={() => setIncidentOpen(true)}
          title={!started ? 'Сначала запустите симуляцию' : 'S'}>
          <Icon name="alert" size={14} /> Внести сбой
        </button>
        <button className="btn btn-ghost" onClick={() => openCsv()} disabled={offline} title="Отчёт по запуску (CSV)">
          <Icon name="download" size={14} /> CSV
        </button>
      </div>

      <div className={s.topRight}>
        {idx && (
          <div className={`${s.idxMini} ${s['cat_' + idx.category]}`} title="Индекс эффективности (факт, окно 15 мин)">
            <span className="mono">{idx.value}</span>
            <span>{INDEX_CAT[idx.category]}</span>
          </div>
        )}
        <div className={`${s.conn} ${offline ? s.connBad : s.connOk}`} role="status">
          <i />
          <span>{conn === 'online' ? 'На связи' : conn === 'connecting' ? 'Подключение' : 'Нет связи'}</span>
          {offline && lastUpdate && <span className="muted mono">{new Date(lastUpdate).toLocaleTimeString('ru-RU')}</span>}
        </div>
        <div className={s.user}>
          <div>
            <div className={s.userName}>{user?.username}</div>
            <div className={s.userRole}>{ROLE_LABEL[role]}</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={() => setHelpOpen(true)} aria-label="Горячие клавиши" title="?">
            <Icon name="keyboard" />
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => logout()} aria-label="Выйти" title="Выйти">
            <Icon name="logout" />
          </button>
        </div>
      </div>
    </header>
  )
}
