// Временная диаграмма (Гант) по путям, горловинам и ресурсам. Один масштаб и общие модельные часы.
// Факт — сплошные полосы, прогноз по плану — контур. Вариант плана — фиолетовый контур.
import { useEffect, useMemo, useRef, useState } from 'react'
import { selectPreviewPlan, selectView, useStore } from '../app/store'
import { fmtT, OP_KIND, useSimNow } from '../app/labels'
import type { Assignment, Operation, Train } from '../api/types'
import s from './Timeline.module.css'

const LABEL_W = 56
const ROW_H = 20
const BEFORE = 15 * 60
const AFTER = 45 * 60

interface Bar { row: string; train: string; start: number; end: number; factUntil: number | null; label: string; wait: boolean; preview: boolean; kind: Train['kind'] }

export default function Timeline() {
  const snap = useStore(selectView)!
  const topo = useStore((x) => x.topology)!
  const sel = useStore((x) => x.selection)
  const select = useStore((x) => x.select)
  const preview = useStore(selectPreviewPlan)
  const now = useSimNow(4)
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(800)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(e.contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const rows = useMemo(
    () => [
      ...topo.tracks.filter((t) => t.id !== 'P12').map((t) => ({ id: t.id, group: 'track' })),
      ...topo.zones.map((z) => ({ id: z.id, group: 'zone' })),
      ...topo.resources.map((r) => ({ id: r.id, group: 'res' })),
    ],
    [topo],
  )

  const bars = useMemo(() => buildBars(snap.operations, snap.trains, snap.active_plan?.assignments ?? [], preview?.assignments ?? null, topo.routes),
    [snap.operations, snap.trains, snap.active_plan, preview, topo.routes])

  const t0 = Math.max(0, Math.floor(now) - BEFORE)
  const t1 = t0 + BEFORE + AFTER
  const plotW = Math.max(200, width - LABEL_W)
  const x = (t: number) => LABEL_W + ((t - t0) / (t1 - t0)) * plotW
  const rowIdx = Object.fromEntries(rows.map((r, i) => [r.id, i]))
  const H = rows.length * ROW_H + 22
  const selTrain = sel?.type === 'train' ? sel.id : null
  const ticks: number[] = []
  for (let t = Math.ceil(t0 / 300) * 300; t <= t1; t += 300) ticks.push(t)

  const closures = snap.tracks.filter((t) => t.closed_until_s).map((t) => ({ row: t.id, end: t.closed_until_s! }))
  const unav = snap.resources.filter((r) => r.unavailable_until_s).map((r) => ({ row: r.id, end: r.unavailable_until_s! }))
  const incidentStart = (row: string) => {
    const inc = [...snap.incidents].reverse().find((i) => i.target_id === row)
    return inc ? inc.at_s : snap.sim_time_s
  }

  return (
    <div className={s.wrap} ref={ref}>
      <svg width={width} height={H} className={s.svg}>
        <defs>
          <pattern id="tlHatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="rgba(255,77,94,.6)" strokeWidth="2" />
          </pattern>
          <pattern id="tlPlan" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" stroke="rgba(166,176,205,.16)" strokeWidth="2" />
          </pattern>
        </defs>
        {/* шкала */}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={18} y2={H} className={t % 1800 === 0 ? s.tickMajor : s.tick} />
            {Math.abs(x(t) - x(now)) > 34 && <text x={x(t) + 3} y={12} className={s.tickLabel}>{fmtT(t)}</text>}
          </g>
        ))}
        {rows.map((r, i) => (
          <g key={r.id}>
            <rect x={0} y={22 + i * ROW_H} width={width} height={ROW_H} className={i % 2 ? s.rowOdd : s.rowEven} />
            {(r.group === 'zone' && i === rowIdx[topo.zones[0].id]) || (r.group === 'res' && i === rowIdx[topo.resources[0].id]) ? (
              <line x1={0} x2={width} y1={22 + i * ROW_H} y2={22 + i * ROW_H} className={s.groupSep} />
            ) : null}
            <text x={8} y={22 + i * ROW_H + 14} className={s.rowLabel}
              onClick={() => select({ type: r.group === 'track' ? 'track' : 'resource', id: r.id })}>{r.id}</text>
          </g>
        ))}
        {/* ограничения */}
        {[...closures, ...unav].map((c) => {
          const i = rowIdx[c.row]
          if (i === undefined) return null
          const a = Math.max(t0, incidentStart(c.row))
          return <rect key={'c' + c.row} x={x(a)} y={22 + i * ROW_H + 1} width={Math.max(0, x(c.end) - x(a))} height={ROW_H - 2} fill="url(#tlHatch)" />
        })}
        {/* назначения */}
        {bars.map((b, k) => {
          const i = rowIdx[b.row]
          if (i === undefined || b.end < t0 || b.start > t1) return null
          const y = 22 + i * ROW_H + 3
          const h = ROW_H - 6
          const xa = Math.max(LABEL_W, x(b.start))
          const xb = Math.min(width, x(b.end))
          if (xb <= xa) return null
          const dim = selTrain && selTrain !== b.train
          const factEnd = b.factUntil !== null ? Math.min(x(Math.min(b.factUntil, now)), xb) : xa
          const w = xb - xa
          return (
            <g key={k} className={s.bar} opacity={dim ? 0.25 : 1} onClick={() => select({ type: 'train', id: b.train })}>
              <title>{`${b.train} · ${b.label}\n${fmtT(b.start)}–${fmtT(b.end)}${b.preview ? ' (вариант)' : b.factUntil !== null ? ' (факт)' : ' (план)'}`}</title>
              <rect x={xa} y={y} width={w} height={h} rx={3}
                className={b.preview ? s.barPreview : s.barPlan} style={b.preview ? undefined : { stroke: `var(--k-${b.kind})` }} />
              {factEnd > xa && (
                <rect x={xa} y={y} width={factEnd - xa} height={h} rx={3} className={s.barFact}
                  style={{ fill: b.wait ? 'var(--wait)' : `var(--k-${b.kind})` }} />
              )}
              {w > 34 && <text x={xa + 4} y={y + h - 3.5} className={s.barText}>{b.train}</text>}
              {selTrain === b.train && <rect x={xa - 1} y={y - 1} width={w + 2} height={h + 2} rx={3} className={s.barSel} />}
            </g>
          )
        })}
        {/* текущее время */}
        <line x1={x(now)} x2={x(now)} y1={14} y2={H} className={s.now} />
        <rect x={x(now) - 23} y={1} width={46} height={14} rx={2} className={s.nowTag} />
        <text x={x(now)} y={11.5} textAnchor="middle" className={s.nowText}>{fmtT(now)}</text>
      </svg>
      {bars.length === 0 && (
        <div className={s.emptyNote}>Назначений нет: принятого плана ещё нет. Полосы появятся после расчёта и принятия плана.</div>
      )}
      <div className={s.legend}>
        <span><i className={s.lgFact} />факт</span>
        <span><i className={s.lgPlan} />принятый план</span>
        <span><i className={s.lgPrev} />вариант</span>
        <span><i className={s.lgClosed} />закрыто / недоступно</span>
      </div>
    </div>
  )
}

/** factUntil: до какого момента полоса — факт (Infinity = идёт сейчас, обрезается текущим временем). */
function buildBars(ops: Operation[], trains: Train[], active: Assignment[], prev: Assignment[] | null,
  routes: { id: string; conflict_zone_ids: string[] }[]): Bar[] {
  const act = Object.fromEntries(active.map((a) => [a.operation_id, a]))
  const pv = prev ? Object.fromEntries(prev.map((a) => [a.operation_id, a])) : null
  const zones = Object.fromEntries(routes.map((r) => [r.id, r.conflict_zone_ids]))
  const kind = Object.fromEntries(trains.map((t) => [t.id, t.kind]))
  const out: Bar[] = []
  const byTrain: Record<string, Operation[]> = {}
  for (const o of ops) (byTrain[o.train_id] ??= []).push(o)

  for (const [tid, list] of Object.entries(byTrain)) {
    let hold: { track: string; start: number; started: boolean; wait: boolean; preview: boolean } | null = null
    for (const o of list) {
      const usePv = !!pv && o.status === 'pending' && !!pv[o.id]
      const a = usePv ? pv![o.id] : act[o.id]
      if (!a) continue
      const start = o.actual_start_s ?? a.start_s
      const end = o.actual_end_s ?? (o.actual_start_s !== null ? o.actual_start_s + o.duration_s : a.end_s)
      const fact = o.status === 'completed' ? end : o.status === 'running' ? Infinity : null
      const wait = !!o.wait_reason
      const base = { train: tid, start, end, factUntil: fact, label: OP_KIND[o.kind], wait, preview: usePv, kind: kind[tid] }
      for (const r of a.resource_ids) out.push({ ...base, row: r })
      if (o.is_move && a.route_id) {
        for (const z of zones[a.route_id] ?? []) out.push({ ...base, row: z })
        if (hold) {
          out.push({ row: hold.track, train: tid, start: hold.start, end, label: 'занятие пути', wait: hold.wait,
            preview: hold.preview, kind: kind[tid], factUntil: hold.started ? (o.status === 'completed' ? end : Infinity) : null })
          hold = null
        }
        if (o.is_move !== 'departure' && a.track_id) {
          hold = { track: a.track_id, start, started: o.actual_start_s !== null, wait, preview: usePv }
        }
      } else if (hold && wait) hold.wait = true
    }
  }
  return out
}
