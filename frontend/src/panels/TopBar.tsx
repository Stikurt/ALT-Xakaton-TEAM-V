import { useState } from 'react'
import { useStore } from '../app/store'
import { fmtT, INDEX_CAT, useSimNow } from '../app/labels'
import s from './panels.module.css'

export default function TopBar() {
  const snap = useStore((x) => x.snapshot)!
  const conn = useStore((x) => x.conn)
  const lastUpdate = useStore((x) => x.lastUpdate)
  const pending = useStore((x) => x.pending)
  const role = useStore((x) => x.role)
  const control = useStore((x) => x.control)
  const setIncidentOpen = useStore((x) => x.setIncidentOpen)
  const setRole = useStore((x) => x.setRole)
  const now = useSimNow(10)
  const [confirmReset, setConfirmReset] = useState(false)
  const offline = conn !== 'online'
  const canCmd = !offline && role !== 'viewer'
  const started = snap.sim_time_s > 0
  const idx = snap.index

  return (
    <header className={s.topbar}>
      <div className={s.brand}>
        <span className={s.brandMark}>▤</span>
        <div>
          <div className={s.brandName}>УЗЕЛ 12</div>
          <div className={s.brandSub}>учебная станция · <span className="mono">{snap.run_id}</span></div>
        </div>
      </div>

      <div className={s.clock} title="Модельное время от начала сценария">
        <span className={s.clockLabel}>МОДЕЛЬНОЕ ВРЕМЯ</span>
        <span className={`mono ${s.clockValue}`}>{fmtT(now)}</span>
        <span className={s.clockSub}>из {fmtT(7200)}</span>
      </div>

      <div className={s.controls}>
        {snap.paused ? (
          <button className="btn btn-primary" disabled={!canCmd || !!pending} onClick={() => control('start')}
            title={snap.active_plan ? '' : 'Без допустимого плана запуск запрещён'}>
            ▶ {started ? 'Продолжить' : 'Запустить'}
          </button>
        ) : (
          <button className="btn" disabled={!canCmd || !!pending} onClick={() => control('pause')}>⏸ Пауза</button>
        )}
        <div className={s.seg} role="group" aria-label="Скорость">
          {[1, 5, 10].map((v) => (
            <button key={v} className={snap.speed === v ? s.segOn : ''} disabled={!canCmd}
              onClick={() => control('speed', v)}>×{v}</button>
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
          title="Новый запуск с исходным сценарием (новый run_id)">↺ {confirmReset ? 'Точно сбросить?' : 'Сброс'}</button>
        <span className={s.sep} />
        <button className="btn btn-danger" disabled={!canCmd || !started} onClick={() => setIncidentOpen(true)}
          title={!started ? 'Сначала запустите симуляцию' : ''}>⚠ Внести сбой</button>
      </div>

      <div className={s.topRight}>
        {idx && (
          <div className={`${s.idxMini} ${s['cat_' + idx.category]}`} title="Индекс эффективности станции (факт, окно 15 мин)">
            <span className="mono">{idx.value}</span>
            <span>{INDEX_CAT[idx.category]}</span>
          </div>
        )}
        <div className={`${s.conn} ${offline ? s.connBad : s.connOk}`}>
          <i />
          {conn === 'online' ? 'Онлайн' : conn === 'connecting' ? 'Подключение…' : 'Нет связи'}
          {offline && lastUpdate && <span className="muted"> · данные от {new Date(lastUpdate).toLocaleTimeString('ru-RU')}</span>}
        </div>
        <select value={role} onChange={(e) => setRole(e.target.value as typeof role)} aria-label="Роль" className={s.role}
          title="Мок-роль. Настоящая аутентификация — POST /api/login (P1)">
          <option value="dispatcher">Диспетчер</option>
          <option value="viewer">Наблюдатель</option>
          <option value="admin">Администратор</option>
        </select>
      </div>
    </header>
  )
}
