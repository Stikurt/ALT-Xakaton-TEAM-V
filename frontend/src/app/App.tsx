import { useCallback, useEffect, useMemo, useState } from 'react'
import StationView from '../station/StationView'
import { estimateSim, selectPreviewPlan, useStore } from './store'
import TopBar from '../panels/TopBar'
import LeftNav from '../panels/LeftNav'
import RightPanel from '../panels/RightPanel'
import IncidentDialog from '../panels/IncidentDialog'
import PlanCompare from '../panels/PlanCompare'
import SidePanel from '../panels/SidePanel'
import Toasts from '../panels/Toasts'
import Timeline from '../timeline/Timeline'
import s from './App.module.css'

export default function App() {
  const init = useStore((x) => x.init)
  useEffect(() => init(), [init])

  const snapshot = useStore((x) => x.snapshot)
  const topology = useStore((x) => x.topology)
  const loadError = useStore((x) => x.loadError)
  const conn = useStore((x) => x.conn)
  const selection = useStore((x) => x.selection)
  const select = useStore((x) => x.select)
  const nav = useStore((x) => x.nav)
  const previewPlan = useStore(selectPreviewPlan)
  const [timelineOpen, setTimelineOpen] = useState(true)

  const getSimTime = useCallback(() => estimateSim(useStore.getState().clock, performance.now()), [])

  const highlightIds = useMemo(() => {
    if (selection?.type !== 'conflict' || !snapshot) return []
    const c = snapshot.conflicts.find((x) => x.id === selection.id)
    return c ? c.entity_ids : []
  }, [selection, snapshot])

  if (!snapshot || !topology) {
    return (
      <div className={s.loading}>
        <div className={s.loadingCard}>
          <div className={s.logo}>УЗЕЛ 12</div>
          {loadError ? (
            <>
              <p>Не удалось получить состояние станции.</p>
              <p className="muted mono">{loadError}</p>
              <p className="muted">Проверьте, что сервер запущен. Повторное подключение идёт автоматически.</p>
            </>
          ) : (
            <p className="muted">Загрузка состояния станции…</p>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={s.app} data-timeline={timelineOpen ? 'open' : 'closed'}>
      <TopBar />
      <LeftNav />
      <main className={s.center}>
        {nav !== 'overview' && <SidePanel />}
        <div className={s.station}>
          <StationView
            snapshot={snapshot}
            topology={topology}
            selection={selection}
            previewPlan={previewPlan}
            viewMode={previewPlan ? 'preview' : 'online'}
            highlightIds={highlightIds}
            frozen={conn !== 'online'}
            getSimTime={getSimTime}
            onSelect={select}
          />
        </div>
      </main>
      <RightPanel />
      <section className={s.bottom}>
        <button className={s.bottomToggle} onClick={() => setTimelineOpen((v) => !v)}>
          {timelineOpen ? '▾' : '▸'} Диаграмма операций
        </button>
        {timelineOpen && <Timeline />}
      </section>
      <IncidentDialog />
      <PlanCompare />
      <Toasts />
    </div>
  )
}
