import { useCallback, useEffect, useMemo, useState } from 'react'
import StationView from '../station/StationView'
import { estimateSim, selectPreviewPlan, selectView, useStore } from './store'
import TopBar from '../panels/TopBar'
import LeftNav from '../panels/LeftNav'
import RightPanel from '../panels/RightPanel'
import IncidentDialog from '../panels/IncidentDialog'
import PlanCompare from '../panels/PlanCompare'
import SidePanel from '../panels/SidePanel'
import Toasts from '../panels/Toasts'
import LoginScreen from '../panels/LoginScreen'
import HistoryBar from '../panels/HistoryBar'
import { CsvDialog, HelpDialog } from '../panels/Dialogs'
import Timeline from '../timeline/Timeline'
import Journal from '../timeline/Journal'
import { useHotkeys } from './useHotkeys'
import { startAutopilot } from './autopilot'
import s from './App.module.css'

export default function App() {
  const boot = useStore((x) => x.boot)
  useEffect(() => boot(), [boot])
  const auth = useStore((x) => x.auth)

  if (auth === 'checking') return <Splash text="Проверка сессии…" />
  if (auth === 'anonymous') return <LoginScreen />
  return <Workspace />
}

function Splash({ text, error }: { text: string; error?: string | null }) {
  return (
    <div className={s.loading}>
      <div className={s.loadingCard}>
        <div className={s.logo}>УЗЕЛ 12</div>
        <p className={error ? s.loadErr : 'muted'}>{error ? 'Не удалось получить состояние станции' : text}</p>
        {error && <p className="muted mono" style={{ fontSize: 11 }}>{error}</p>}
        {error && <p className="muted">Повторное подключение идёт автоматически.</p>}
      </div>
    </div>
  )
}

function Workspace() {
  const online = useStore((x) => x.snapshot)
  const view = useStore(selectView)
  const topology = useStore((x) => x.topology)
  const loadError = useStore((x) => x.loadError)
  const conn = useStore((x) => x.conn)
  const selection = useStore((x) => x.selection)
  const select = useStore((x) => x.select)
  const nav = useStore((x) => x.nav)
  const history = useStore((x) => x.history)
  const previewPlan = useStore(selectPreviewPlan)
  const httpPolling = useStore((x) => x.httpPolling)
  const [timelineOpen, setTimelineOpen] = useState(true)
  const [bottomTab, setBottomTab] = useState<'gantt' | 'journal'>('gantt')
  const journalCount = useStore((x) => x.journal.length)
  useHotkeys()
  useEffect(() => startAutopilot(), [])

  const histAt = history?.at_s
  const getSimTime = useCallback(
    () => (histAt !== undefined ? histAt : estimateSim(useStore.getState().clock, performance.now())),
    [histAt],
  )

  const highlightIds = useMemo(() => {
    if (selection?.type !== 'conflict' || !view) return []
    const c = view.conflicts.find((x) => x.id === selection.id)
    return c ? c.entity_ids : []
  }, [selection, view])

  if (!online || !view || !topology) return <Splash text="Загрузка состояния станции…" error={loadError} />

  const mode = history ? 'history' : previewPlan ? 'preview' : 'online'
  return (
    <div className={s.app} data-timeline={timelineOpen ? 'open' : 'closed'} data-mode={mode}>
      <TopBar />
      <LeftNav />
      <main className={s.center}>
        {nav !== 'overview' && nav !== 'history' && <SidePanel />}
        <div className={s.station}>
          {history && <HistoryBar />}
          <div className={s.stationInner}>
            <StationView
              snapshot={view}
              topology={topology}
              selection={selection}
              previewPlan={previewPlan}
              viewMode={mode}
              highlightIds={highlightIds}
              frozen={!history && conn !== 'online' && !httpPolling}
              getSimTime={getSimTime}
              onSelect={select}
            />
          </div>
        </div>
      </main>
      <RightPanel />
      <section className={s.bottom}>
        <div className={s.bottomBar}>
          <button className={s.caretBtn} onClick={() => setTimelineOpen((v) => !v)} aria-expanded={timelineOpen} aria-label="Свернуть нижнюю панель">
            <span className={s.caret} data-open={timelineOpen} />
          </button>
          {(['gantt', 'journal'] as const).map((t) => (
            <button key={t} className={`${s.bottomTab} ${bottomTab === t && timelineOpen ? s.bottomTabOn : ''}`}
              onClick={() => { setBottomTab(t); setTimelineOpen(true) }}>
              {t === 'gantt' ? 'Диаграмма операций' : `Журнал событий${journalCount ? ` · ${journalCount}` : ''}`}
            </button>
          ))}
          <span className="muted" style={{ marginLeft: 'auto', fontSize: 11.5 }}>
            {bottomTab === 'gantt' ? 'пути, горловины и ресурсы по модельному времени' : 'факты запуска по модельному времени'}
          </span>
        </div>
        {timelineOpen && (bottomTab === 'gantt' ? <Timeline /> : <Journal />)}
      </section>
      <IncidentDialog />
      <PlanCompare />
      <CsvDialog />
      <HelpDialog />
      <Toasts />
    </div>
  )
}
