import { useMemo } from 'react'
import { selectPreviewPlan, selectRole, selectView, useStore } from '../app/store'
import Icon from '../app/Icon'
import { CONFLICT, fmtDur, fmtT, OP_KIND, RES_KIND, TRACK_KIND, TRAIN_KIND, TRAIN_STATUS, useSimNow } from '../app/labels'
import type { Assignment, Operation } from '../api/types'
import s from './panels.module.css'

export default function SelectionCard() {
  const sel = useStore((x) => x.selection)
  if (!sel) {
    return (
      <section className={`${s.card} ${s.grow}`}>
        <div className="eyebrow">Выбранный объект</div>
        <p className={s.empty}>Нажмите на поезд, путь или ресурс на схеме. Здесь появятся состояние, цепочка операций и назначения по плану.</p>
        <div className={s.legendList}>
          <span><i style={{ background: 'var(--k-passenger)' }} />пассажирский</span>
          <span><i style={{ background: 'var(--k-transit)' }} />транзитный</span>
          <span><i style={{ background: 'var(--k-local)' }} />местный грузовой</span>
          <span><i style={{ background: 'var(--wait)' }} />ждёт ресурс</span>
        </div>
      </section>
    )
  }
  return (
    <section className={`${s.card} ${s.grow}`}>
      {sel.type === 'train' && <TrainCard id={sel.id} />}
      {sel.type === 'track' && <TrackCard id={sel.id} />}
      {sel.type === 'resource' && <ResourceCard id={sel.id} />}
      {sel.type === 'conflict' && <ConflictCard id={sel.id} />}
    </section>
  )
}

function usePlanIndex() {
  const snap = useStore(selectView)!
  const preview = useStore(selectPreviewPlan)
  return useMemo(() => {
    const active = Object.fromEntries((snap.active_plan?.assignments ?? []).map((a) => [a.operation_id, a]))
    const prev = preview ? Object.fromEntries(preview.assignments.map((a) => [a.operation_id, a])) : null
    return { active, prev }
  }, [snap.active_plan, preview])
}

function Close() {
  const select = useStore((x) => x.select)
  return <button className="btn btn-ghost btn-sm" onClick={() => select(null)} aria-label="Снять выбор"><Icon name="close" /></button>
}

function TrainCard({ id }: { id: string }) {
  const snap = useStore(selectView)!
  const select = useStore((x) => x.select)
  const now = useSimNow(4)
  const { active, prev } = usePlanIndex()
  const t = snap.trains.find((x) => x.id === id)
  if (!t) return null
  const ops = snap.operations.filter((o) => o.train_id === id)
  const cur = ops.find((o) => o.status === 'running')
  const next = ops.find((o) => o.status === 'pending')
  const prog = cur && cur.actual_start_s !== null ? Math.min(1, (now - cur.actual_start_s) / cur.duration_s) : 0
  const prevDep = prev ? Object.values(prev).find((a) => a.train_id === id && a.kind === 'departure') : null

  return (
    <>
      <div className={s.cardHead}>
        <div>
          <div className={s.objTitle}><span className="mono">{t.id}</span> {TRAIN_KIND[t.kind]}</div>
          <div className="muted">{t.length_m} м · приоритет {t.priority}</div>
        </div>
        <Close />
      </div>
      <div className={s.chips}>
        <span className={t.wait_reason ? 'chip chip-wait' : t.status === 'departed' ? 'chip chip-ok' : 'chip chip-info'}>
          {TRAIN_STATUS[t.status]}
        </span>
        {t.track_id && <button className="chip chip-muted" onClick={() => select({ type: 'track', id: t.track_id! })}>путь {t.track_id}</button>}
        {t.delay_s > 0 ? <span className="chip chip-wait">задержка {fmtDur(t.delay_s)}</span> : <span className="chip chip-ok">по графику</span>}
      </div>
      {t.wait_reason && <div className={s.bannerWarn}>Ждёт: {t.wait_reason}</div>}
      {cur && (
        <div className={s.curOp}>
          <div><b>{OP_KIND[cur.kind]}</b> <span className="muted">идёт · {fmtDur(cur.duration_s)}</span></div>
          <div className={s.progress}><i style={{ width: `${Math.max(0, prog) * 100}%` }} /></div>
        </div>
      )}
      {!cur && next && !t.wait_reason && t.status !== 'scheduled' && (
        <div className="muted" style={{ marginBottom: 8 }}>
          Ожидание по плану до {fmtT(active[next.id]?.start_s)} → {OP_KIND[next.kind]}
        </div>
      )}
      <dl className={s.kv}>
        <dt>Прибытие</dt><dd className="mono">{fmtT(t.scheduled_arrival_s)}{t.expected_arrival_s !== t.scheduled_arrival_s && <span className={s.warnTxt}> → {fmtT(t.expected_arrival_s)}</span>}</dd>
        <dt>Отправление по графику</dt><dd className="mono">{fmtT(t.scheduled_departure_s)}</dd>
        <dt>{t.status === 'departed' ? 'Отправлен факт' : 'Прогноз отправления'}</dt>
        <dd className="mono">{fmtT(t.actual_departure_s ?? t.forecast_departure_s)}</dd>
        {prevDep && <><dt className={s.prevTxt}>В варианте</dt><dd className={`mono ${s.prevTxt}`}>{fmtT(prevDep.end_s)}</dd></>}
      </dl>
      <h4 className={`${s.h4} eyebrow`}>Цепочка операций</h4>
      <ol className={s.ops}>
        {ops.map((o) => <OpRow key={o.id} op={o} a={active[o.id]} pv={prev?.[o.id]} />)}
      </ol>
    </>
  )
}

function OpRow({ op, a, pv }: { op: Operation; a?: Assignment; pv?: Assignment }) {
  const changed = pv && a && op.status === 'pending' && (pv.start_s !== a.start_s || pv.track_id !== a.track_id || pv.resource_ids.join() !== a.resource_ids.join())
  const icon = op.status === 'completed' ? '✓' : op.status === 'running' ? '▸' : op.wait_reason ? '!' : '·'
  const where = op.is_move && a?.route_id ? a.route_id.replace('R_', '').replace('_', '→') : a?.track_id
  return (
    <li className={`${s.op} ${s['op_' + op.status]} ${op.wait_reason ? s.op_wait : ''}`}>
      <span className={s.opIcon}>{icon}</span>
      <span className={s.opName}>{OP_KIND[op.kind]}</span>
      <span className={`mono ${s.opWhere}`}>{where}{a?.resource_ids.length ? ` · ${a.resource_ids.join(',')}` : ''}</span>
      <span className={`mono ${s.opTime}`}>
        {op.actual_start_s !== null ? fmtT(op.actual_start_s) : fmtT(a?.start_s)}
      </span>
      {changed && (
        <span className={s.opChange}>
          вариант: {fmtT(pv!.start_s)} {pv!.track_id !== a!.track_id ? `· ${pv!.track_id}` : ''} {pv!.resource_ids.join(',')}
        </span>
      )}
    </li>
  )
}

function TrackCard({ id }: { id: string }) {
  const snap = useStore(selectView)!
  const topo = useStore((x) => x.topology)!
  const select = useStore((x) => x.select)
  const tr = snap.tracks.find((x) => x.id === id)
  const tt = topo.tracks.find((x) => x.id === id)
  if (!tr || !tt) return null
  const opById = Object.fromEntries(snap.operations.map((o) => [o.id, o]))
  const upcoming = (snap.active_plan?.assignments ?? [])
    .filter((a) => a.track_id === id && (a.kind === 'arrival' || a.kind === 'shunt') && opById[a.operation_id]?.status === 'pending')
    .slice(0, 8)
  const closed = tr.availability === 'closed'
  return (
    <>
      <div className={s.cardHead}>
        <div>
          <div className={s.objTitle}><span className="mono">{tr.id}</span> путь</div>
          <div className="muted">{TRACK_KIND[tr.kind]} · {tr.usable_length_m} м</div>
        </div>
        <Close />
      </div>
      <div className={s.chips}>
        <span className={closed ? 'chip chip-bad' : 'chip chip-ok'}>{closed ? `закрыт до ${fmtT(tr.closed_until_s)}` : 'открыт'}</span>
        {tr.occupant_train_id ? (
          <button className="chip chip-info" onClick={() => select({ type: 'train', id: tr.occupant_train_id! })}>занят {tr.occupant_train_id}</button>
        ) : <span className="chip chip-muted">свободен</span>}
      </div>
      {closed && <p className="muted">Новые входы запрещены. Находящийся состав может закончить работы и выйти.</p>}
      <h4 className={`${s.h4} eyebrow`}>Ближайшие въезды по принятому плану</h4>
      {upcoming.length === 0 ? <p className={s.empty}>Нет назначений</p> : (
        <ul className={s.simpleList}>
          {upcoming.map((a) => (
            <li key={a.operation_id}>
              <button className={s.linkBtn} onClick={() => select({ type: 'train', id: a.train_id })}>{a.train_id}</button>
              <span>{OP_KIND[a.kind]}</span>
              <span className="mono muted">{fmtT(a.start_s)}</span>
              {closed && tr.closed_until_s && a.start_s < tr.closed_until_s && <span className="chip chip-bad">конфликт</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function ResourceCard({ id }: { id: string }) {
  const snap = useStore(selectView)!
  const select = useStore((x) => x.select)
  const r = snap.resources.find((x) => x.id === id)
  if (!r) return null
  const opById = Object.fromEntries(snap.operations.map((o) => [o.id, o]))
  const cur = r.active_operation_id ? opById[r.active_operation_id] : null
  const upcoming = (snap.active_plan?.assignments ?? [])
    .filter((a) => a.resource_ids.includes(id) && opById[a.operation_id]?.status === 'pending')
    .slice(0, 8)
  const unav = r.availability === 'unavailable'
  return (
    <>
      <div className={s.cardHead}>
        <div>
          <div className={s.objTitle}><span className="mono">{r.id}</span></div>
          <div className="muted">{RES_KIND[r.kind] ?? r.kind}</div>
        </div>
        <Close />
      </div>
      <div className={s.chips}>
        <span className={unav ? 'chip chip-bad' : cur ? 'chip chip-info' : 'chip chip-ok'}>
          {unav ? `недоступен до ${fmtT(r.unavailable_until_s)}` : cur ? 'занят' : 'свободен'}
        </span>
      </div>
      {cur && (
        <p>Сейчас: <b>{OP_KIND[cur.kind]}</b>{' '}
          <button className={s.linkBtn} onClick={() => select({ type: 'train', id: cur.train_id })}>{cur.train_id}</button></p>
      )}
      <h4 className={`${s.h4} eyebrow`}>Ближайшие задания по плану</h4>
      {upcoming.length === 0 ? <p className={s.empty}>Нет заданий</p> : (
        <ul className={s.simpleList}>
          {upcoming.map((a) => (
            <li key={a.operation_id}>
              <button className={s.linkBtn} onClick={() => select({ type: 'train', id: a.train_id })}>{a.train_id}</button>
              <span>{OP_KIND[a.kind]}</span>
              <span className="mono muted">{fmtT(a.start_s)}–{fmtT(a.end_s)}</span>
              {unav && r.unavailable_until_s && a.start_s < r.unavailable_until_s && <span className="chip chip-bad">конфликт</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

function ConflictCard({ id }: { id: string }) {
  const snap = useStore(selectView)!
  const select = useStore((x) => x.select)
  const requestReplan = useStore((x) => x.requestReplan)
  const replan = useStore((x) => x.replan)
  const role = useStore(selectRole)
  const history = useStore((x) => x.history)
  const c = snap.conflicts.find((x) => x.id === id)
  if (!c) return <p className={s.empty}>Конфликт разрешён. <button className={s.linkBtn} onClick={() => select(null)}>Закрыть</button></p>
  const typeOf = (eid: string) =>
    snap.trains.some((t) => t.id === eid) ? 'train' : snap.tracks.some((t) => t.id === eid) ? 'track'
      : snap.resources.some((r) => r.id === eid) ? 'resource' : null
  return (
    <>
      <div className={s.cardHead}>
        <div>
          <div className={s.objTitle}>{CONFLICT[c.code] ?? c.code}</div>
          <div className="muted mono">{c.code}</div>
        </div>
        <Close />
      </div>
      <p>{c.message}</p>
      <dl className={s.kv}>
        <dt>Тип</dt><dd>{c.kind === 'execution' ? 'Операция ждёт сейчас' : 'Нарушение в будущем плане'}</dd>
        <dt>Интервал</dt><dd className="mono">{fmtT(c.start_s)}{c.end_s ? ` → ${fmtT(c.end_s)}` : ' → …'}</dd>
      </dl>
      <h4 className={`${s.h4} eyebrow`}>Затронуто</h4>
      <div className={s.chips}>
        {c.entity_ids.map((eid) => {
          const t = typeOf(eid)
          return t ? <button key={eid} className="chip chip-info" onClick={() => select({ type: t, id: eid })}>{eid}</button>
            : <span key={eid} className="chip chip-muted">{eid}</span>
        })}
      </div>
      <h4 className={`${s.h4} eyebrow`}>Рекомендация</h4>
      <p className="muted">
        {c.kind === 'plan'
          ? 'Принятый план нарушает новое ограничение. Пересчитайте план и сравните варианты.'
          : 'Операция не может начаться. Состав остаётся на месте, пока ресурс не освободится или не будет принят новый план.'}
      </p>
      <button className="btn btn-primary" disabled={replan.status === 'running' || role === 'viewer' || !!history} onClick={() => requestReplan()}>
        <Icon name="replan" size={14} /> Пересчитать план
      </button>
    </>
  )
}
