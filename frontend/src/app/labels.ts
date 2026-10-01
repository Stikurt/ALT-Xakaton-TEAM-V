import { useEffect, useState } from 'react'
import { estimateSim, useStore } from './store'
import type { OpKind, TrainKind, TrainStatus, ConflictCode } from '../api/types'

export const TRAIN_KIND: Record<TrainKind, string> = { passenger: 'Пассажирский', transit: 'Транзитный', local: 'Местный' }
export const TRAIN_KIND_SHORT: Record<TrainKind, string> = { passenger: 'Пасс', transit: 'Транз', local: 'Мест' }
export const TRAIN_STATUS: Record<TrainStatus, string> = {
  scheduled: 'По расписанию', waiting_entry: 'Ждёт у W', moving: 'В движении', on_track: 'На пути', departed: 'Отправлен',
}
export const OP_KIND: Record<OpKind, string> = {
  arrival: 'Приём', departure: 'Отправление', shunt: 'Маневр', stop: 'Стоянка', inspection: 'Осмотр',
  preparation: 'Подготовка', cargo: 'Грузовая обработка', formation: 'Формирование',
}
export const CONFLICT: Record<ConflictCode, string> = {
  TRACK_CLOSED: 'Путь закрыт', TRACK_OCCUPIED: 'Путь занят', ROUTE_BUSY: 'Горловина занята',
  RESOURCE_UNAVAILABLE: 'Ресурс недоступен', RESOURCE_BUSY: 'Ресурс занят', PREDECESSOR_INCOMPLETE: 'Нет предшественника',
  NO_FEASIBLE_SLOT: 'Нет слота', STALE_PLAN: 'План устарел',
}
export const TRACK_KIND: Record<string, string> = {
  passenger: 'Пассажирский приём/отправление', freight: 'Грузовой приём/отправление', staging: 'Накопление и подготовка',
  cargo: 'Погрузка и выгрузка', loco: 'Стоянка локомотивов',
}
export const RES_KIND: Record<string, string> = {
  shunting_loco: 'Маневровый локомотив', shunting_crew: 'Составительская бригада', inspection_crew: 'Бригада осмотра',
}
export const STRATEGY: Record<string, string> = {
  passenger_first: 'Пассажирские вперёд', earliest_departure: 'Раньше по отправлению',
}
export const INDEX_CAT = { norm: 'Норма', attention: 'Внимание', critical: 'Критично' } as const

/** Модельные секунды → «ММ:СС» от начала сценария (не время суток). */
export function fmtT(s: number | null | undefined): string {
  if (s === null || s === undefined) return '—'
  const v = Math.max(0, Math.floor(s))
  const h = Math.floor(v / 3600)
  const m = Math.floor((v % 3600) / 60)
  const sec = v % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(sec).padStart(2, '0')
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}
export function fmtDur(s: number): string {
  if (!s) return '0 с'
  const m = Math.floor(s / 60)
  const sec = s % 60
  if (!m) return `${sec} с`
  return sec ? `${m} мин ${sec} с` : `${m} мин`
}

/** Модельное время, оценённое в браузере, с перерисовкой через requestAnimationFrame. */
export function useSimNow(maxFps = 60): number {
  const clock = useStore((s) => s.clock)
  const [now, setNow] = useState(() => estimateSim(clock, performance.now()))
  useEffect(() => {
    let raf = 0
    let last = 0
    const frame = (t: number) => {
      if (t - last >= 1000 / maxFps) {
        last = t
        setNow(estimateSim(clock, t))
      }
      if (!clock.paused) raf = requestAnimationFrame(frame)
    }
    setNow(estimateSim(clock, performance.now()))
    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [clock, maxFps])
  return now
}
