import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './app/theme.css'
import App from './app/App'
import { setTransport } from './api/transport'

async function start() {
  if (import.meta.env.VITE_MOCK === '1') {
    // Страница-демо: сервер-заглушка работает прямо в браузере, backend не нужен.
    const { demoTransport } = await import('./mock/server')
    setTransport(demoTransport)
  }
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}
void start()
