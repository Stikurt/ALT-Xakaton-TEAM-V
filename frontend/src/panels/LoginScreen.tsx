import { useState } from 'react'
import { useStore } from '../app/store'
import { transport } from '../api/transport'
import Icon from '../app/Icon'
import s from './panels.module.css'

const DEMO = [
  { u: 'dispatcher', p: 'dispatcher', label: 'Диспетчер', note: 'управляет симуляцией и принимает планы' },
  { u: 'viewer', p: 'viewer', label: 'Наблюдатель', note: 'только просмотр' },
  { u: 'admin', p: 'admin', label: 'Администратор', note: 'плюс настройки и веса' },
]

export default function LoginScreen() {
  const login = useStore((x) => x.login)
  const error = useStore((x) => x.authError)
  const [u, setU] = useState('dispatcher')
  const [p, setP] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    await login(u.trim(), p)
    setBusy(false)
  }

  return (
    <div className={s.loginWrap}>
      <svg className={s.loginArt} viewBox="0 0 900 600" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        {[...Array(9)].map((_, i) => (
          <line key={i} x1="0" x2="900" y1={120 + i * 46} y2={120 + i * 46} stroke="#1c2440" strokeWidth="3" />
        ))}
        <path d="M0 304 L180 304 L300 166 L900 166" stroke="#2ee6ff" strokeWidth="3" fill="none" opacity=".7" />
        <path d="M0 304 L180 304 L300 442 L900 442" stroke="#ff3ea5" strokeWidth="3" fill="none" opacity=".55" strokeDasharray="14 10" />
        <rect x="520" y="158" width="210" height="16" rx="8" fill="#2ee6ff" opacity=".9" />
      </svg>
      <form className={s.loginCard} onSubmit={submit}>
        <div className={s.loginBrand}>УЗЕЛ 12</div>
        <p className={s.loginLead}>Рабочее место диспетчера учебной станции</p>
        <label className={s.field} htmlFor="login-user">
          <span>Пользователь</span>
          <input id="login-user" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" autoFocus />
        </label>
        <label className={s.field} htmlFor="login-pass">
          <span>Пароль</span>
          <input id="login-pass" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <div className={s.loginErr} role="alert">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy || !u} style={{ width: '100%', justifyContent: 'center', height: 36 }}>
          <Icon name="lock" size={14} /> Войти
        </button>
        <div className={s.demoBox}>
          <div className="eyebrow">Учётные записи{transport().demo ? ' демо-версии' : ' мок-сервера'}</div>
          {DEMO.map((d) => (
            <button type="button" key={d.u} className={s.demoRow} onClick={() => { setU(d.u); setP(d.p) }}>
              <span className="mono">{d.u}</span>
              <span>{d.label}</span>
              <span className="muted">{d.note}</span>
            </button>
          ))}
          <div className="muted" style={{ fontSize: 11 }}>Пароль совпадает с именем. Нажмите строку, чтобы подставить.</div>
        </div>
        <p className={s.loginNote}>Демонстрационная модель. Не предназначена для управления реальным движением.</p>
      </form>
    </div>
  )
}
