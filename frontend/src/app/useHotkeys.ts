import { useEffect } from 'react'
import { useStore } from './store'

/** Горячие клавиши диспетчера. Не срабатывают при вводе в поля формы. */
export const HOTKEYS: [string, string][] = [
  ['Пробел', 'Запуск / пауза'],
  ['1 · 5 · 0', 'Скорость ×1 · ×5 · ×10'],
  ['S', 'Внести сбой'],
  ['R', 'Пересчитать план'],
  ['C', 'Сравнение вариантов'],
  ['H', 'История'],
  ['Esc', 'Закрыть окно, снять выбор, выйти из истории'],
  ['?', 'Эта справка'],
]

export function useHotkeys() {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA')) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const st = useStore.getState()
      const snap = st.snapshot
      if (!snap) return
      const k = e.key
      if (k === 'Escape') {
        if (st.helpOpen) st.setHelpOpen(false)
        else if (st.incidentOpen) st.setIncidentOpen(false)
        else if (st.csv.open) st.closeCsv()
        else if (st.compareOpen) st.setCompareOpen(false)
        else if (st.history) st.closeHistory()
        else st.select(null)
        return
      }
      if (st.incidentOpen || st.csv.open) return
      if (k === ' ') {
        e.preventDefault()
        void st.control(snap.paused ? 'start' : 'pause')
      } else if (k === '1') void st.control('speed', 1)
      else if (k === '5') void st.control('speed', 5)
      else if (k === '0') void st.control('speed', 10)
      else if (k === 's' || k === 'S' || k === 'ы' || k === 'Ы') st.setIncidentOpen(true)
      else if (k === 'r' || k === 'R' || k === 'к' || k === 'К') void st.requestReplan()
      else if ((k === 'c' || k === 'C' || k === 'с' || k === 'С') && st.replan.plans.length) st.setCompareOpen(!st.compareOpen)
      else if (k === 'h' || k === 'H' || k === 'р' || k === 'Р') {
        if (st.history) st.closeHistory()
        else st.openHistory()
      }
      else if (k === '?' || k === ',') st.setHelpOpen(!st.helpOpen)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])
}
