// Единственное WebSocket-соединение (ТЗ разд. 7): reconnect 1,2,4,8 → 10 с; пропуск ws_seq → полный снимок.
import type { WsEnvelope } from './types'

export type WsStatus = 'connecting' | 'online' | 'offline'

export interface WsHandlers {
  onMessage: (m: WsEnvelope) => void
  onStatus: (s: WsStatus) => void
  onGap: () => void // пропуск ws_seq — нужно запросить снимок
}

const BACKOFF = [1000, 2000, 4000, 8000, 10000]

export function connectWs(h: WsHandlers): () => void {
  let ws: WebSocket | null = null
  let attempt = 0
  let lastSeq = 0
  let closed = false
  let timer: number | undefined

  const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`

  const open = () => {
    if (closed) return
    h.onStatus('connecting')
    ws = new WebSocket(url)
    lastSeq = 0
    ws.onopen = () => {
      attempt = 0
    }
    ws.onmessage = (ev) => {
      if (closed) return
      let m: WsEnvelope
      try {
        m = JSON.parse(ev.data)
      } catch {
        return
      }
      if (m.schema_version !== 1) return
      if (lastSeq && m.ws_seq !== lastSeq + 1) {
        lastSeq = m.ws_seq
        h.onGap()
        return
      }
      lastSeq = m.ws_seq
      if (m.type === 'snapshot') h.onStatus('online')
      h.onMessage(m)
    }
    ws.onclose = () => {
      if (closed) return // соединение закрыто владельцем — статус не трогаем
      h.onStatus('offline')
      const d = BACKOFF[Math.min(attempt++, BACKOFF.length - 1)]
      timer = window.setTimeout(open, d)
    }
    ws.onerror = () => ws?.close()
  }
  open()
  return () => {
    closed = true
    window.clearTimeout(timer)
    ws?.close()
  }
}
