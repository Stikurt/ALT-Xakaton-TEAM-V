import { useStore, type Nav } from '../app/store'
import Icon from '../app/Icon'
import s from './panels.module.css'

const ITEMS: { id: Nav; icon: string; label: string; key?: string }[] = [
  { id: 'overview', icon: 'overview', label: 'Обзор' },
  { id: 'trains', icon: 'train', label: 'Поезда' },
  { id: 'resources', icon: 'crew', label: 'Ресурсы' },
  { id: 'history', icon: 'history', label: 'История', key: 'H' },
]

export default function LeftNav() {
  const nav = useStore((x) => x.nav)
  const setNav = useStore((x) => x.setNav)
  return (
    <nav className={s.leftnav} aria-label="Разделы">
      {ITEMS.map((i) => (
        <button key={i.id} className={nav === i.id ? s.navOn : ''} onClick={() => setNav(nav === i.id && i.id !== 'overview' ? 'overview' : i.id)}
          aria-current={nav === i.id} title={i.key ? `${i.label} (${i.key})` : i.label}>
          <Icon name={i.icon} size={18} />
          <span className={s.navLabel}>{i.label}</span>
        </button>
      ))}
    </nav>
  )
}
