// Интерактивная схема станции (владелец — Р). Только отображение: данные приходят через props,
// действия пользователя уходят через onSelect. Собственного API-клиента и модели движения нет.
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Assignment, Plan, Pt, Snapshot, Topology, Train } from '../api/types'
import { fmtT, TRAIN_KIND_SHORT } from '../app/labels'
import { headingAt, poly, pointAt, subPath, toPath, trainPx } from './geometry'
import s from './StationView.module.css'

export type StationSelection = { type: 'train' | 'track' | 'resource' | 'conflict'; id: string } | null
export type ViewMode = 'online' | 'history' | 'preview'

export interface StationViewProps {
  snapshot: Snapshot
  topology: Topology
  selection: StationSelection
  previewPlan: Plan | null
  viewMode: ViewMode
  highlightIds?: string[]
  frozen?: boolean // нет связи: движение замораживается
  getSimTime: () => number
  onSelect: (sel: StationSelection) => void
}

const MID_X = 700

export default function StationView(p: StationViewProps) {
  const { topology: topo, snapshot: snap } = p
  const fit: [number, number, number, number] = [0, 50, 1400, 760]
  const [vb, setVb] = useState<[number, number, number, number]>(fit)
  const svgRef = useRef<SVGSVGElement>(null)
  const drag = useRef<{ x: number; y: number; vb: [number, number, number, number]; moved: boolean } | null>(null)

  const routes = useMemo(() => Object.fromEntries(topo.routes.map((r) => [r.id, r])), [topo])
  const trackY = useMemo(() => Object.fromEntries(topo.tracks.map((t) => [t.id, t.geometry.y1])), [topo])
  const stTracks = useMemo(() => Object.fromEntries(snap.tracks.map((t) => [t.id, t])), [snap.tracks])
  const zoneBusy = useMemo(() => Object.fromEntries(snap.zones.map((z) => [z.id, z.active_operation_id])), [snap.zones])
  const hl = useMemo(() => new Set(p.highlightIds ?? []), [p.highlightIds])
  const selId = p.selection?.id

  // ---- масштаб и перемещение ----
  const toSvg = useCallback((cx: number, cy: number): Pt => {
    const el = svgRef.current!
    const r = el.getBoundingClientRect()
    const scale = Math.max(vb[2] / r.width, vb[3] / r.height)
    const ox = (r.width * scale - vb[2]) / 2
    const oy = (r.height * scale - vb[3]) / 2
    return [vb[0] + (cx - r.left) * scale - ox, vb[1] + (cy - r.top) * scale - oy]
  }, [vb])

  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const k = e.deltaY > 0 ? 1.12 : 1 / 1.12
      setVb((v) => {
        const w = Math.min(Math.max(v[2] * k, 350), 2100)
        const h = (w * topo.viewbox[3]) / topo.viewbox[2]
        const [mx, my] = toSvg(e.clientX, e.clientY)
        const fx = (mx - v[0]) / v[2]
        const fy = (my - v[1]) / v[3]
        return [mx - fx * w, my - fy * h, w, h]
      })
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [toSvg, topo.viewbox])

  const zoom = (k: number) =>
    setVb((v) => {
      const w = Math.min(Math.max(v[2] * k, 350), 2100)
      const h = (w * topo.viewbox[3]) / topo.viewbox[2]
      return [v[0] + (v[2] - w) / 2, v[1] + (v[3] - h) / 2, w, h]
    })

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return
    drag.current = { x: e.clientX, y: e.clientY, vb, moved: false }
  }
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current
    if (!d) return
    const el = svgRef.current!
    const r = el.getBoundingClientRect()
    const scale = Math.max(d.vb[2] / r.width, d.vb[3] / r.height)
    const dx = (e.clientX - d.x) * scale
    const dy = (e.clientY - d.y) * scale
    if (Math.abs(e.clientX - d.x) + Math.abs(e.clientY - d.y) > 4) {
      if (!d.moved) el.setPointerCapture(e.pointerId)
      d.moved = true
    }
    if (d.moved) setVb([d.vb[0] - dx, d.vb[1] - dy, d.vb[2], d.vb[3]])
  }
  const onPointerUp = () => {
    setTimeout(() => (drag.current = null), 0)
  }
  const click = (sel: StationSelection) => (e: React.MouseEvent) => {
    e.stopPropagation()
    if (drag.current?.moved) return
    p.onSelect(sel)
  }

  // ---- плановые и предлагаемые маршруты ----
  const activeByOp = useMemo(
    () => Object.fromEntries((snap.active_plan?.assignments ?? []).map((a) => [a.operation_id, a])),
    [snap.active_plan],
  )
  const pendingMoves = useMemo(() => {
    const pend = new Set(snap.operations.filter((o) => o.status === 'pending' && o.is_move).map((o) => o.id))
    return pend
  }, [snap.operations])

  const plannedForSelected: Assignment[] = useMemo(() => {
    if (p.selection?.type !== 'train') return []
    const src = p.previewPlan?.assignments ?? snap.active_plan?.assignments ?? []
    return src.filter((a) => a.train_id === p.selection!.id && pendingMoves.has(a.operation_id) && a.route_id)
  }, [p.selection, p.previewPlan, snap.active_plan, pendingMoves])

  const previewChanged: Assignment[] = useMemo(() => {
    if (!p.previewPlan) return []
    const horizon = snap.sim_time_s + 1800
    return p.previewPlan.assignments.filter((a) => {
      if (!a.route_id || !pendingMoves.has(a.operation_id) || a.start_s > horizon) return false
      const o = activeByOp[a.operation_id]
      return !o || o.route_id !== a.route_id || o.start_s !== a.start_s
    })
  }, [p.previewPlan, pendingMoves, activeByOp, snap.sim_time_s])
  const previewTracks = new Set(previewChanged.map((a) => a.track_id))

  const W = topo.nodes.W, GW = topo.nodes.GW, GE = topo.nodes.GE, E = topo.nodes.E
  const resById = Object.fromEntries(snap.resources.map((r) => [r.id, r]))
  const opById = useMemo(() => Object.fromEntries(snap.operations.map((o) => [o.id, o])), [snap.operations])

  const queue = snap.trains.filter((t) => t.status === 'waiting_entry')
  const soon = snap.trains
    .filter((t) => t.status === 'scheduled')
    .sort((a, b) => a.expected_arrival_s - b.expected_arrival_s)
    .slice(0, 4)

  return (
    <div className={s.wrap}>
      <div className={s.toolbar}>
        <Legend />
        {snap.paused && p.viewMode !== 'history' && <span className={s.pauseBadge}>⏸ ПАУЗА</span>}
        {p.viewMode === 'preview' && <span className={s.previewBadge}>ПРОСМОТР ВАРИАНТА · ПРОГНОЗ</span>}
        {p.frozen && <span className={s.frozenBadge}>Нет связи — последнее подтверждённое состояние</span>}
        <div className={s.toolbarRight}>
          <button className="btn btn-sm" onClick={() => zoom(1 / 1.25)} aria-label="Приблизить">＋</button>
          <button className="btn btn-sm" onClick={() => zoom(1.25)} aria-label="Отдалить">－</button>
          <button className="btn btn-sm" onClick={() => setVb(fit)}>Вся станция</button>
        </div>
      </div>
      <div className={s.svgBox}>
      <svg
        ref={svgRef}
        className={s.svg}
        viewBox={vb.join(' ')}
        preserveAspectRatio="xMidYMid meet"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onClick={() => !drag.current?.moved && p.onSelect(null)}
        role="img"
        aria-label="Схема станции Узел 12"
      >
        <defs>
          <pattern id="hatch" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="8" height="8" fill="rgba(239,68,68,0.12)" />
            <line x1="0" y1="0" x2="0" y2="8" stroke="#ef4444" strokeWidth="3" />
          </pattern>
          <pattern id="hatchSmall" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="5" stroke="#ef4444" strokeWidth="2" />
          </pattern>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0H0V40" fill="none" stroke="rgba(255,255,255,0.025)" />
          </pattern>
        </defs>
        <rect x={-1000} y={-1000} width={3400} height={2900} fill="url(#grid)" />

        {/* зоны: платформы и грузовой фронт */}
        <g>
          <rect x={310} y={98} width={780} height={104} rx={8} className={s.area} />
          <rect x={340} y={146} width={720} height={8} rx={2} className={s.platform} />
          <text x={318} y={92} className={s.areaLabel}>ПЛАТФОРМЫ · ПАССАЖИРСКИЙ ПАРК</text>
          <rect x={310} y={638} width={780} height={104} rx={8} className={s.area} />
          <rect x={340} y={686} width={720} height={8} rx={2} className={s.cargo} />
          <text x={318} y={632} className={s.areaLabel}>ГРУЗОВОЙ ФРОНТ F10 / F11</text>
          <text x={318} y={232 - 12} className={s.areaLabel} opacity={0.6}>ГРУЗОВОЙ ПРИЁМ-ОТПРАВЛЕНИЕ</text>
          <text x={318} y={472 - 12} className={s.areaLabel} opacity={0.6}>НАКОПЛЕНИЕ И ПОДГОТОВКА</text>
        </g>

        {/* соединения горловин (условная схема, не проект СЦБ) */}
        <g className={s.links}>
          <line x1={W[0]} y1={W[1]} x2={GW[0]} y2={GW[1]} />
          <line x1={GE[0]} y1={GE[1]} x2={E[0]} y2={E[1]} />
          {topo.tracks.map((t) => (
            <line key={'w' + t.id} x1={GW[0]} y1={GW[1]} x2={t.geometry.x1} y2={t.geometry.y1} />
          ))}
          {topo.tracks.filter((t) => ['passenger', 'freight'].includes(t.kind)).map((t) => (
            <line key={'e' + t.id} x1={t.geometry.x2} y1={t.geometry.y2} x2={GE[0]} y2={GE[1]} />
          ))}
        </g>

        {/* пути */}
        {topo.tracks.map((t) => {
          const st = stTracks[t.id]
          const closed = st?.availability === 'closed'
          const occ = !!st?.occupant_train_id
          const sel = p.selection?.type === 'track' && selId === t.id
          const y = t.geometry.y1
          const cls = closed ? s.trackClosed : occ ? s.trackOcc : s.trackFree
          return (
            <g key={t.id} className={s.trackG} onClick={click({ type: 'track', id: t.id })}>
              <rect x={t.geometry.x1 - 60} y={y - 16} width={t.geometry.x2 - t.geometry.x1 + 140} height={32} fill="transparent" />
              {(sel || hl.has(t.id)) && (
                <rect x={t.geometry.x1 - 6} y={y - 12} width={t.geometry.x2 - t.geometry.x1 + 12} height={24} rx={6}
                  className={hl.has(t.id) ? s.hlRect : s.selRect} />
              )}
              {previewTracks.has(t.id) && (
                <rect x={t.geometry.x1 - 4} y={y - 10} width={t.geometry.x2 - t.geometry.x1 + 8} height={20} rx={5} className={s.previewRect} />
              )}
              <line x1={t.geometry.x1} y1={y} x2={t.geometry.x2} y2={y} className={cls} />
              {closed && <rect x={t.geometry.x1} y={y - 7} width={t.geometry.x2 - t.geometry.x1} height={14} fill="url(#hatch)" rx={2} />}
              <rect x={t.geometry.x1 - 2} y={y - 12} width={48} height={24} rx={5} className={s.trackChip} />
              <text x={t.geometry.x1 + 22} y={y + 6} textAnchor="middle" className={s.trackLabel}>{t.id}</text>
              <text x={t.geometry.x2 + 12} y={y + 4} className={closed ? s.trackNoteBad : s.trackNote}>
                {closed ? `⛔ закрыт до ${fmtT(st.closed_until_s)}` : `${t.usable_length_m} м`}
              </text>
            </g>
          )
        })}

        {/* узлы и горловины */}
        {(['W', 'GW', 'GE', 'E'] as const).map((n) => {
          const [x, y] = topo.nodes[n]
          const zone = n === 'GW' || n === 'GE'
          const busy = zone && !!zoneBusy[n]
          return (
            <g key={n} className={zone ? s.zoneG : undefined}>
              {zone && <circle cx={x} cy={y} r={busy ? 15 : 11} className={busy ? s.zoneBusy : s.zoneFree} />}
              {!zone && <rect x={x - 7} y={y - 7} width={14} height={14} rx={3} className={s.border} />}
              <text x={x} y={y + (zone ? 32 : 26)} textAnchor="middle" className={s.nodeLabel}>{n}</text>
              {zone && busy && (
                <text x={x} y={y - 22} textAnchor="middle" className={s.zoneNote}>
                  {opById[zoneBusy[n]!]?.train_id ?? ''}
                </text>
              )}
            </g>
          )
        })}

        {/* плановые маршруты выбранного поезда — пунктир */}
        {plannedForSelected.map((a) => {
          const r = routes[a.route_id!]
          return r ? <path key={'pl' + a.operation_id} d={toPath(r.polyline)} className={p.previewPlan ? s.routePreview : s.routePlan} /> : null
        })}
        {/* изменённые маршруты варианта — пунктир фиолетовый */}
        {previewChanged.map((a) => {
          const r = routes[a.route_id!]
          return r ? <path key={'pv' + a.operation_id} d={toPath(r.polyline)} className={s.routePreview} /> : null
        })}

        {/* фактическое движение и поезда */}
        <TrainsLayer
          trains={snap.trains}
          routes={routes}
          trackY={trackY}
          getSimTime={p.getSimTime}
          frozen={!!p.frozen || snap.paused || p.viewMode === 'history'}
          selId={p.selection?.type === 'train' ? selId : undefined}
          hl={hl}
          onClick={(id) => click({ type: 'train', id })}
        />

        {/* очередь у W */}
        <g>
          <text x={14} y={500} className={s.areaLabel}>ОЧЕРЕДЬ У W · {queue.length}</text>
          {queue.map((t, i) => {
            const waitingReason = t.wait_reason
            const sel = selId === t.id
            return (
              <g key={t.id} transform={`translate(14, ${512 + i * 38})`} className={s.queueChip} onClick={click({ type: 'train', id: t.id })}>
                <rect width={160} height={32} rx={6} className={waitingReason ? s.qWait : s.qOk} strokeWidth={sel ? 2.5 : 1} stroke={sel ? 'var(--select)' : undefined} />
                <text x={8} y={21} className={s.qText}>{t.id}</text>
                <text x={48} y={21} className={s.qSub}>{waitingReason ? `⏸ ${waitingReason}`.slice(0, 15) : 'вход по плану'}</text>
              </g>
            )
          })}
          {queue.length === 0 && <text x={14} y={528} className={s.qSub}>пусто</text>}
          <text x={14} y={512 + Math.max(queue.length, 1) * 38 + 22} className={s.areaLabel} opacity={0.6}>ОЖИДАЮТСЯ</text>
          {soon.map((t, i) => (
            <g key={t.id} transform={`translate(14, ${512 + Math.max(queue.length, 1) * 38 + 36 + i * 24})`}
              className={s.queueChip} onClick={click({ type: 'train', id: t.id })}>
              <text className={s.qSoon} y={10}>
                {t.id} · {fmtT(t.expected_arrival_s)}
                {t.expected_arrival_s > t.scheduled_arrival_s ? ' (опозд.)' : ''}
              </text>
            </g>
          ))}
        </g>

        {/* ресурсы */}
        <g transform="translate(1236, 496)">
          <text x={0} y={0} className={s.areaLabel}>РЕСУРСЫ</text>
          {snap.resources.map((r, i) => {
            const col = 0
            const row = i
            const unav = r.availability === 'unavailable'
            const busy = !!r.active_operation_id
            const sel = selId === r.id
            const opTrain = busy ? opById[r.active_operation_id!]?.train_id : null
            return (
              <g key={r.id} transform={`translate(${col}, ${14 + row * 38})`} className={s.queueChip}
                onClick={click({ type: 'resource', id: r.id })}>
                <rect width={160} height={32} rx={6} className={unav ? s.resBad : busy ? s.resBusy : s.resFree}
                  stroke={sel || hl.has(r.id) ? 'var(--select)' : undefined} strokeWidth={sel || hl.has(r.id) ? 2 : 1} />
                {unav && <rect width={160} height={32} rx={6} fill="url(#hatchSmall)" opacity={0.5} />}
                <text x={8} y={21} className={s.qText}>{r.id}</text>
                <text x={48} y={21} className={s.qSub}>{unav ? `до ${fmtT(r.unavailable_until_s)}` : busy ? `→ ${opTrain}` : 'свободен'}</text>
              </g>
            )
          })}
        </g>
        {/* P12 стоянка: свободные маневровые локомотивы */}
        {['L01', 'L02'].map((id, i) => {
          const r = resById[id]
          if (!r || r.active_operation_id) return null
          const y = trackY['P12']
          return (
            <g key={id} className={s.queueChip} onClick={click({ type: 'resource', id })}>
              <rect x={600 + i * 100} y={y - 10} width={84} height={20} rx={4}
                className={r.availability === 'unavailable' ? s.locoBad : s.loco} />
              <text x={642 + i * 100} y={y + 5} textAnchor="middle" className={s.locoText}>{id}</text>
            </g>
          )
        })}

      </svg>

      </div>
    </div>
  )
}

function Legend() {
  return (
    <div className={s.legend}>
      <span><i className={s.lgFree} />свободно</span>
      <span><i className={s.lgOcc} />занято</span>
      <span><i className={s.lgWait} />ожидание</span>
      <span><i className={s.lgClosed} />закрыто</span>
      <span><i className={s.lgPlan} />план</span>
      <span><i className={s.lgPrev} />вариант</span>
    </div>
  )
}

// ---------------- поезда ----------------
interface TLProps {
  trains: Train[]
  routes: Record<string, { polyline: Pt[] }>
  trackY: Record<string, number>
  getSimTime: () => number
  frozen: boolean
  selId?: string
  hl: Set<string>
  onClick: (id: string) => (e: React.MouseEvent) => void
}

const TrainsLayer = memo(function TrainsLayer(p: TLProps) {
  const [, force] = useState(0)
  const anyMoving = p.trains.some((t) => t.movement)
  useEffect(() => {
    if (p.frozen || !anyMoving) return
    let raf = 0
    const f = () => {
      force((x) => x + 1)
      raf = requestAnimationFrame(f)
    }
    raf = requestAnimationFrame(f)
    return () => cancelAnimationFrame(raf)
  }, [p.frozen, anyMoving])
  const now = p.getSimTime()

  return (
    <g>
      {p.trains.map((t) => {
        if (t.status === 'scheduled' || t.status === 'departed' || t.status === 'waiting_entry') return null
        const L = trainPx(t.length_m)
        let body: Pt[]
        let center: Pt
        let head: Pt
        let ang = 0
        let trace: Pt[] | null = null
        if (t.movement) {
          const r = p.routes[t.movement.route_id]
          if (!r) return null
          const pl = poly(t.movement.route_id, r.polyline)
          const span = t.movement.expected_end_at_s - t.movement.started_at_s || 1
          const prog = Math.max(0, Math.min(1, (now - t.movement.started_at_s) / span))
          const d = prog * pl.total
          body = subPath(pl, d - L / 2, d + L / 2)
          center = pointAt(pl, d)
          head = pointAt(pl, d + L / 2)
          ang = headingAt(pl, Math.min(d + L / 2, pl.total - 1))
          trace = r.polyline
        } else if (t.track_id) {
          const y = p.trackY[t.track_id]
          body = [[MID_X - L / 2, y], [MID_X + L / 2, y]]
          center = [MID_X, y]
          head = [MID_X + L / 2, y]
        } else return null
        const waiting = !!t.wait_reason
        const sel = p.selId === t.id
        const hl = p.hl.has(t.id)
        const color = waiting ? 'var(--wait)' : `var(--k-${t.kind})`
        const d = toPath(body)
        return (
          <g key={t.id} className={s.trainG} onClick={p.onClick(t.id)}>
            {trace && <path d={toPath(trace)} className={s.routeActual} />}
            <path d={d} className={s.trainHit} />
            {(sel || hl) && <path d={d} className={hl ? s.trainHl : s.trainSel} />}
            <path d={d} className={s.trainBody} style={{ stroke: color }} />
            <path d={d} className={s.trainCars} />
            <g transform={`translate(${head[0]},${head[1]}) rotate(${(ang * 180) / Math.PI})`}>
              <path d="M-2,-6 L8,0 L-2,6 Z" fill={color} />
            </g>
            <g transform={`translate(${center[0]},${center[1] - 18})`}>
              <rect x={-56} y={-15} width={112} height={21} rx={5} className={s.trainTag} />
              <text textAnchor="middle" y={1} className={s.trainLabel}>
                {t.id} · {TRAIN_KIND_SHORT[t.kind]}
              </text>
            </g>
            {t.delay_s > 0 && (
              <g transform={`translate(${center[0] + 62},${center[1] - 18})`}>
                <rect x={0} y={-15} width={70} height={21} rx={5} className={s.delayTag} />
                <text x={35} y={1} textAnchor="middle" className={s.delayText}>+{Math.round(t.delay_s / 60)} мин</text>
              </g>
            )}
            {waiting && (
              <text x={center[0]} y={center[1] + 28} textAnchor="middle" className={s.waitText}>⏸ {t.wait_reason}</text>
            )}
          </g>
        )
      })}
    </g>
  )
})
