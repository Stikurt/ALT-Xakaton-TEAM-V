// Транспорт: настоящий fetch/WebSocket или встроенный демо-сервер (VITE_MOCK=1, для страницы-демо без backend).
export interface SocketLike {
  onopen: (() => void) | null
  onmessage: ((ev: { data: string }) => void) | null
  onclose: (() => void) | null
  onerror: (() => void) | null
  close: () => void
}
export interface Transport {
  request: (method: string, path: string, body?: unknown, headers?: Record<string, string>) => Promise<{ status: number; text: string; headers?: Record<string, string> }>
  socket: (path: string) => SocketLike
  demo: boolean
}

const real: Transport = {
  demo: false,
  request: async (method, path, body, headers) => {
    const h: Record<string, string> = { ...(headers ?? {}) }
    if (body !== undefined) h['content-type'] = 'application/json'
    const r = await fetch(path, {
      method,
      headers: Object.keys(h).length ? h : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: 'include',
    })
    return { status: r.status, text: await r.text() }
  },
  socket: (path) => {
    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`
    return new WebSocket(url) as unknown as SocketLike
  },
}

let current: Transport = real
export const transport = () => current
export const setTransport = (t: Transport) => {
  current = t
}
