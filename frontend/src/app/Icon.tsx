// Линейные иконки 16×16 (stroke = currentColor). Без эмодзи в интерфейсе.
const P: Record<string, string> = {
  play: 'M5 3.5v9l7-4.5z',
  pause: 'M5 3.5v9M11 3.5v9',
  reset: 'M3 8a5 5 0 1 0 1.6-3.7M3 2.5v2.8h2.8',
  alert: 'M8 2.5 14 13H2zM8 6.5v3M8 11.2v.3',
  overview: 'M2.5 3.5h11v9h-11zM2.5 6.5h11M6.5 6.5v6',
  train: 'M4 2.5h8a1.5 1.5 0 0 1 1.5 1.5v6.5a1.5 1.5 0 0 1-1.5 1.5H4a1.5 1.5 0 0 1-1.5-1.5V4A1.5 1.5 0 0 1 4 2.5zM2.5 7.5h11M5.5 12l-1.5 2M10.5 12l1.5 2M5.5 9.8h.01M10.5 9.8h.01',
  crew: 'M5.5 6.5a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM2 13c.4-2.4 1.8-3.8 3.5-3.8S8.6 10.6 9 13M11 6.5a1.7 1.7 0 1 0 0-3.4M11.5 9.3c1.3.3 2.2 1.5 2.5 3.7',
  history: 'M8 4.5V8l2.5 1.5M2.5 8a5.5 5.5 0 1 0 1.7-4M2.5 2.5v2.8h2.8',
  replan: 'M13.5 5.5A5.5 5.5 0 0 0 3 6M2.5 10.5A5.5 5.5 0 0 0 13 10M13.5 2.5v3h-3M2.5 13.5v-3h3',
  close: 'M4 4l8 8M12 4l-8 8',
  check: 'M3 8.5 6.5 12 13 4.5',
  eye: 'M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8zM8 10a2 2 0 1 0 0-4 2 2 0 0 0 0 4z',
  download: 'M8 2.5v8M4.5 7.5 8 11l3.5-3.5M2.5 13.5h11',
  copy: 'M5.5 5.5h7v8h-7zM3.5 10.5v-8h7',
  logout: 'M6 2.5H3v11h3M10 5l3 3-3 3M13 8H6',
  plus: 'M8 3v10M3 8h10',
  minus: 'M3 8h10',
  fit: 'M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10',
  keyboard: 'M1.5 4.5h13v7h-13zM4 7h.01M6.5 7h.01M9 7h.01M11.5 7h.01M5 9.5h6',
  lock: 'M4 7.5h8v6H4zM5.5 7.5V5a2.5 2.5 0 0 1 5 0v2.5',
  back: 'M10 3.5 5.5 8l4.5 4.5',
  wait: 'M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM8 4.5V8l2 1.5',
}

export default function Icon({ name, size = 16, className }: { name: keyof typeof P | string; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}
      style={{ flex: 'none', ...(name === 'play' ? { fill: 'currentColor' } : null) }}>
      <path d={P[name] ?? ''} />
    </svg>
  )
}
