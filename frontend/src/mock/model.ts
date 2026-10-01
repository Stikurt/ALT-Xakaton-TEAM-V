// Демо-сервер в браузере: порт mock_backend/app/model.py. Только для страницы-демо без backend.
import CONFIG from '../../../shared/station.json'
import type { Pt } from '../api/types'

export { CONFIG }
export type Dict<T = unknown> = Record<string, T>

export const PASS = ['P01', 'P02']
export const FREIGHT = ['P03', 'P04', 'P05', 'P06']
export const STAGING = ['P07', 'P08', 'P09']
export const CARGO = ['P10', 'P11']
export const GROUPS: Dict<string[]> = { L: ['L01', 'L02'], B12: ['B01', 'B02'], B34: ['B03', 'B04'] }
const D = CONFIG.durations_s as Dict<number>
const MID_X = 700, WEST_X = 320, EAST_X = 1080

export const trackY = (id: string) => CONFIG.tracks.find((t) => t.id === id)!.geometry.y1

export interface RouteDef { id: string; from_id: string; to_id: string; kind: string; conflict_zone_ids: string[]; duration_s: number; polyline: Pt[] }

function buildRoutes(): RouteDef[] {
  const n = CONFIG.nodes as unknown as Dict<Pt>
  const out: RouteDef[] = []
  for (const p of [...PASS, ...FREIGHT]) {
    const y = trackY(p)
    out.push({ id: `R_W_${p}`, from_id: 'W', to_id: p, kind: 'arrival', conflict_zone_ids: ['GW'], duration_s: D.arrival, polyline: [n.W, n.GW, [WEST_X, y], [MID_X, y]] })
    out.push({ id: `R_${p}_E`, from_id: p, to_id: 'E', kind: 'departure', conflict_zone_ids: ['GE'], duration_s: D.departure, polyline: [[MID_X, y], [EAST_X, y], n.GE, n.E] })
  }
  for (const [a, b] of [[FREIGHT, CARGO], [CARGO, STAGING], [STAGING, FREIGHT]]) {
    for (const x of a) for (const y of b) for (const [src, dst] of [[x, y], [y, x]]) {
      const ys = trackY(src), yd = trackY(dst)
      out.push({ id: `R_${src}_${dst}`, from_id: src, to_id: dst, kind: 'shunt', conflict_zone_ids: ['GW'], duration_s: D.shunt, polyline: [[MID_X, ys], [WEST_X, ys], n.GW, [WEST_X, yd], [MID_X, yd]] })
    }
  }
  return out
}
export const ROUTES = buildRoutes()
export const ROUTE_BY_ID: Dict<RouteDef> = Object.fromEntries(ROUTES.map((r) => [r.id, r]))

export interface Stage { tracks: string[]; ops: [string, number, string[]][] }
export function stagesFor(kind: string): Stage[] {
  if (kind === 'passenger') return [{ tracks: PASS, ops: [['stop', D.stop, []]] }]
  if (kind === 'transit') return [{ tracks: FREIGHT, ops: [['inspection', D.inspection, ['B34']], ['preparation', D.preparation, ['B34']]] }]
  return [
    { tracks: FREIGHT, ops: [['inspection', D.inspection, ['B34']]] },
    { tracks: CARGO, ops: [['cargo', D.cargo, []]] },
    { tracks: STAGING, ops: [['formation', D.formation, ['B12']]] },
    { tracks: FREIGHT, ops: [['preparation', D.preparation, ['B34']]] },
  ]
}

export interface Op {
  id: string; train_id: string; kind: string; duration_s: number; groups: string[]; stage: number
  is_move: 'arrival' | 'shunt' | 'departure' | null; predecessor_ids: string[]
  status: 'pending' | 'running' | 'completed' | 'cancelled'; actual_start_s: number | null; actual_end_s: number | null; wait_reason: string | null
}

export function buildOperations(train: { id: string; kind: string }): Op[] {
  const ops: Op[] = []
  const stages = stagesFor(train.kind)
  const add = (kind: string, dur: number, groups: string[], stage: number, move: Op['is_move']) => {
    const n = String(ops.length + 1).padStart(2, '0')
    ops.push({ id: `${train.id}-${n}-${kind}`, train_id: train.id, kind, duration_s: dur, groups, stage, is_move: move,
      predecessor_ids: ops.length ? [ops[ops.length - 1].id] : [], status: 'pending', actual_start_s: null, actual_end_s: null, wait_reason: null })
  }
  stages.forEach((st, i) => {
    if (i === 0) add('arrival', D.arrival, [], 0, 'arrival')
    else add('shunt', D.shunt, ['L', 'B12'], i, 'shunt')
    for (const [k, dur, g] of st.ops) add(k, dur, g, i, null)
  })
  add('departure', D.departure, [], stages.length, 'departure')
  return ops
}

export function topology() {
  return { schema_version: 1, name: CONFIG.name, viewbox: CONFIG.viewbox, nodes: CONFIG.nodes, zones: CONFIG.zones, areas: CONFIG.areas,
    tracks: CONFIG.tracks, routes: ROUTES, horizon_s: CONFIG.horizon_s, resources: CONFIG.resources }
}
