import { useStore } from '../app/store'
import s from './panels.module.css'

export default function Toasts() {
  const toasts = useStore((x) => x.toasts)
  const dismiss = useStore((x) => x.dismissToast)
  return (
    <div className={s.toasts} aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`${s.toast} ${s['toast_' + t.kind]}`} onClick={() => dismiss(t.id)}>
          {t.kind === 'error' ? '✕ ' : t.kind === 'ok' ? '✓ ' : ''}{t.text}
        </div>
      ))}
    </div>
  )
}
