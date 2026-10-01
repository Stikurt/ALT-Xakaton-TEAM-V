import { useStore, type Nav } from '../app/store'
import s from './panels.module.css'

const ITEMS: { id: Nav; icon: string; label: string; disabled?: boolean }[] = [
  { id: 'overview', icon: '◫', label: 'Обзор' },
  { id: 'trains', icon: '🚆', label: 'Поезда' },
  { id: 'resources', icon: '⚙', label: 'Ресурсы' },
  { id: 'history', icon: '⟲', label: 'История' },
]

export default function LeftNav() {
  const nav = useStore((x) => x.nav)
  const setNav = useStore((x) => x.setNav)
  return (
    <nav className={s.leftnav}>
      {ITEMS.map((i) => (
        <button key={i.id} className={nav === i.id ? s.navOn : ''} onClick={() => setNav(i.id)} disabled={i.disabled}>
          <span className={s.navIcon}>{i.icon}</span>
          <span className={s.navLabel}>{i.label}</span>
        </button>
      ))}
    </nav>
  )
}
