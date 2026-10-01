import { useMemo, useState } from 'react'
import { useStore } from '../app/store'
import { HOTKEYS } from '../app/useHotkeys'
import { transport } from '../api/transport'
import Icon from '../app/Icon'
import s from './panels.module.css'

export function CsvDialog() {
  const csv = useStore((x) => x.csv)
  const close = useStore((x) => x.closeCsv)
  const runId = useStore((x) => x.snapshot?.run_id)
  const [copied, setCopied] = useState(false)
  const rows = useMemo(() => (csv.text ?? '').replace(/^﻿/, '').trim().split('\n').map((r) => r.split(';')), [csv.text])
  if (!csv.open) return null
  const header = rows[0] ?? []
  const body = rows.slice(1)
  const download = () => {
    const blob = new Blob([csv.text ?? ''], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = `uzel12_${runId}.csv`
    a.click()
    URL.revokeObjectURL(a.href)
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(csv.text ?? '')
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      const ta = document.getElementById('csv-raw') as HTMLTextAreaElement | null
      ta?.select()
    }
  }
  return (
    <div className={s.modalBack} onClick={close}>
      <div className={`${s.modal} ${s.modalWide}`} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Отчёт CSV">
        <div className={s.cardHead}>
          <div>
            <h3 className={s.h3}>Отчёт по запуску</h3>
            <div className="muted mono" style={{ fontSize: 11 }}>{runId} · разделитель «;» · UTF-8</div>
          </div>
          <button className="btn btn-ghost btn-sm" onClick={close} aria-label="Закрыть"><Icon name="close" /></button>
        </div>
        {csv.loading ? <p className="muted">Формирование отчёта…</p> : (
          <>
            <div className={s.csvTable}>
              <table className={s.table}>
                <thead><tr>{header.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
                <tbody>{body.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className="mono">{c}</td>)}</tr>)}</tbody>
              </table>
            </div>
            <textarea id="csv-raw" className={s.csvRaw} readOnly value={csv.text ?? ''} aria-label="Текст CSV" />
            <div className={s.modalFoot}>
              <span className="muted">Текстовые поля, начинающиеся с = + − @, экранированы.</span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="btn btn-sm" onClick={copy}><Icon name="copy" size={14} />{copied ? 'Скопировано' : 'Копировать'}</button>
                {!transport().demo && <button className="btn btn-primary btn-sm" onClick={download}><Icon name="download" size={14} />Скачать .csv</button>}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export function HelpDialog() {
  const open = useStore((x) => x.helpOpen)
  const setOpen = useStore((x) => x.setHelpOpen)
  if (!open) return null
  return (
    <div className={s.modalBack} onClick={() => setOpen(false)}>
      <div className={s.modal} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Горячие клавиши">
        <div className={s.cardHead}>
          <h3 className={s.h3}>Горячие клавиши</h3>
          <button className="btn btn-ghost btn-sm" onClick={() => setOpen(false)} aria-label="Закрыть"><Icon name="close" /></button>
        </div>
        <dl className={s.keys}>
          {HOTKEYS.map(([k, v]) => (
            <div key={k}><dt><span className="kbd">{k}</span></dt><dd>{v}</dd></div>
          ))}
        </dl>
        <h4 className={s.h4}>На схеме</h4>
        <p className="muted" style={{ margin: 0 }}>Колесо — масштаб, перетаскивание — перемещение. Нажатие на поезд, путь или ресурс открывает карточку справа.</p>
      </div>
    </div>
  )
}
