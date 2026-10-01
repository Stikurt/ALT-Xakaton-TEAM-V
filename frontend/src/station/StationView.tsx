import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import type { StationViewProps, Selection } from './types';
import { clamp, formatTime, modelTime, polylinePoints, trainPosition } from './geometry';
import './station.css';
const kinds = { passenger: 'Пассажирский', transit: 'Транзитный', local: 'Местный' };
const trackKinds = { passenger: 'пасс.', freight: 'приём / отпр.', holding: 'накопление', cargo: 'грузовой', depot: 'локомотивы' };
const initialCamera = { x: 0, y: 0, width: 1400, height: 900 };
export function StationView(props: StationViewProps) {
    const { snapshot, topology, selection, onSelect, previewPlan, viewMode, connection, clockReceivedAtMs, conflicts = [], highlightedEntityIds = [] } = props;
    const [now, setNow] = useState(() => performance.now());
    const [camera, setCamera] = useState(initialCamera);
    const svgRef = useRef<SVGSVGElement>(null);
    const drag = useRef<{
        x: number;
        y: number;
        camera: typeof initialCamera;
        moved: boolean;
    } | null>(null);
    const suppressClick = useRef(false);
    const patternId = useId().replace(/:/g, '');
    useEffect(() => {
        if (snapshot.paused || connection !== 'connected' || viewMode !== 'live')
            return;
        let raf: number;
        const frame = (time: number) => { setNow(time); raf = requestAnimationFrame(frame); };
        raf = requestAnimationFrame(frame);
        return () => cancelAnimationFrame(raf);
    }, [snapshot.paused, connection, viewMode]);
    const time = modelTime(snapshot, clockReceivedAtMs, now, viewMode === 'live', connection === 'connected');
    const highlighted = new Set([...highlightedEntityIds, ...conflicts.flatMap(c => c.entity_ids)]);
    const occupied = snapshot.tracks.filter(t => t.occupant_train_id).length;
    const closed = snapshot.tracks.filter(t => t.availability === 'closed').length;
    const waiting = snapshot.trains.filter(t => t.status === 'waiting_entry');
    const activeTrains = snapshot.trains.filter(t => t.status === 'on_track' || t.status === 'moving');
    const invalidTrains = activeTrains.filter(t => !trainPosition(t, topology, time));
    const usablePreview = previewPlan?.run_id === snapshot.run_id ? previewPlan : null;
    const stalePreview = usablePreview && usablePreview.based_on_version !== snapshot.state_version;
    const previewAssignments = usablePreview?.assignments.filter(a => a.end_s > snapshot.sim_time_s) ?? [];
    const previewTracks = new Set(previewAssignments.map(a => a.track_id));
    const previewRoutes = new Set(previewAssignments.map(a => a.route_id));
    const actualRoutes = new Set(snapshot.trains.filter(t => t.status === 'moving').map(t => t.movement?.route_id));
    const selected = (kind: 'train' | 'track', id: string) => selection?.kind === kind && selection.id === id;
    const select = (value: Selection) => { if (!suppressClick.current)
        onSelect(value); };
    const keySelect = (event: KeyboardEvent, value: Selection) => {
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onSelect(value);
        }
    };
    const zoom = (factor: number, px = .5, py = .5) => setCamera(c => {
        const width = clamp(c.width * factor, 420, 1400), height = width * 900 / 1400;
        return { x: clamp(c.x + (c.width - width) * px, 0, 1400 - width), y: clamp(c.y + (c.height - height) * py, 0, 900 - height), width, height };
    });
    const pointerDown = (event: PointerEvent<SVGSVGElement>) => {
        if (event.button !== 0)
            return;
        suppressClick.current = false;
        drag.current = { x: event.clientX, y: event.clientY, camera, moved: false };
    };
    const pointerMove = (event: PointerEvent<SVGSVGElement>) => {
        const d = drag.current, svg = svgRef.current;
        if (!d || !svg)
            return;
        const dx = event.clientX - d.x, dy = event.clientY - d.y;
        if (Math.hypot(dx, dy) < 5 && !d.moved)
            return;
        d.moved = true;
        suppressClick.current = true;
        svg.setPointerCapture(event.pointerId);
        const rect = svg.getBoundingClientRect();
        const scale = Math.min(rect.width / d.camera.width, rect.height / d.camera.height);
        setCamera({ ...d.camera, x: clamp(d.camera.x - dx / scale, 0, 1400 - d.camera.width), y: clamp(d.camera.y - dy / scale, 0, 900 - d.camera.height) });
    };
    const pointerUp = (event: PointerEvent<SVGSVGElement>) => {
        drag.current = null;
        if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
    };
    return <section className={`sv ${props.className ?? ''}`} aria-label="Интерактивная схема станции">
    <header className="sv-header">
      <div><span className="sv-eyebrow">ИНТЕРАКТИВНАЯ СХЕМА</span><h2>Станция Узел 12 <span className="sv-counter">12 путей</span></h2></div>
      <div className="sv-status"><span className={`sv-dot ${connection === 'disconnected' ? 'sv-dot-off' : ''}`}/>{viewMode === 'history' ? 'История' : connection === 'disconnected' ? 'Нет связи' : snapshot.paused ? 'Пауза' : 'Движение'}<time data-testid="map-time">{formatTime(time)}</time></div>
    </header>
    {viewMode === 'history' && <div className="sv-banner" role="status">ИСТОРИЯ · Снимок на {formatTime(snapshot.sim_time_s)} · Движение остановлено</div>}
    {connection === 'disconnected' && <div className="sv-banner sv-warning" role="status">Нет связи · Последнее подтверждённое состояние на {formatTime(snapshot.sim_time_s)}</div>}
    {previewPlan && <div className="sv-banner sv-preview" role="status">ПРОГНОЗ · {previewPlan.id} · {previewPlan.run_id !== snapshot.run_id ? 'Другой запуск — наложение скрыто' : stalePreview ? 'Устарел — требуется пересчёт' : ({ feasible: 'Допустимый', partial: 'Частичный', infeasible: 'Недопустимый', timeout: 'Время расчёта истекло' }[previewPlan.status])} · Пунктир показывает будущие назначения</div>}
    <div className="sv-map-wrap">
      <div className="sv-map-meta"><span>W → E / УСЛОВНАЯ ТОПОЛОГИЯ</span><span>{occupied} занято <i /> {closed} закрыто</span></div>
      <svg ref={svgRef} className="sv-map" viewBox={`${camera.x} ${camera.y} ${camera.width} ${camera.height}`} aria-label="Пути, маршруты и поезда" onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp} onPointerCancel={pointerUp} onClick={event => { if (event.target === event.currentTarget)
        select(null); }}>
        <defs>
          <pattern id={`${patternId}-grid`} width="40" height="40" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" fill="#26323c"/></pattern>
          <pattern id={`${patternId}-closed`} width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="14" height="14" fill="#3c252b"/><path d="M0 0V14" stroke="#a95261" strokeWidth="5"/></pattern>
        </defs>
        <rect width="1400" height="900" fill={`url(#${patternId}-grid)`} pointerEvents="none"/>
        <g className="sv-zones" pointerEvents="none">
          <rect x="307" y="66" width="786" height="145" rx="16"/><text x="336" y="95">01 / ПАССАЖИРСКИЙ ПАРК</text>
          <rect x="307" y="216" width="786" height="229" rx="16"/><text x="336" y="234" className="sv-hidden-label">ПРИЁМ И ОТПРАВЛЕНИЕ</text>
          <rect x="307" y="454" width="786" height="171" rx="16"/>
          <rect x="307" y="638" width="786" height="104" rx="16"/>
          <rect x="320" y="139" width="760" height="18" rx="4" className="sv-platform"/><text x="1010" y="154" className="sv-platform-label">ПЛАТФОРМА</text>
          <rect x="355" y="691" width="275" height="14" rx="3" className="sv-platform"/><text x="650" y="705" className="sv-platform-label">ГРУЗОВОЙ ФРОНТ F10 / F11</text>
          <text x="320" y="848">УЧЕБНАЯ МОДЕЛЬ · ДЛИНА СОСТАВОВ НА СХЕМЕ УСЛОВНАЯ</text>
        </g>
        <g className="sv-approaches" pointerEvents="none">
          <path d="M40 450H180 M1220 450H1360"/>
          {topology.tracks.map(track => { const a = track.geometry[0], b = track.geometry.at(-1); return a && b ? <path key={track.id} d={`M180 450L${a[0]} ${a[1]} M${b[0]} ${b[1]}L1220 450`}/> : null; })}
        </g>
        <g pointerEvents="none"><circle cx="180" cy="450" r="9" className="sv-junction"/><circle cx="1220" cy="450" r="9" className="sv-junction"/><text className="sv-gate" x="160" y="483">GW</text><text className="sv-gate" x="1201" y="483">GE</text><text className="sv-boundary" x="38" y="428">W</text><text className="sv-boundary" x="1335" y="428">E</text></g>
        {topology.tracks.map(track => {
            const state = snapshot.tracks.find(t => t.id === track.id), isClosed = state?.availability === 'closed', isOccupied = !!state?.occupant_train_id;
            const start = track.geometry[0], end = track.geometry.at(-1);
            if (!start || !end)
                return null;
            const label = `${track.id}, ${!state ? 'нет данных' : isClosed ? 'закрыт' : isOccupied ? 'занят' : 'свободен'}, ${track.usable_length_m} м`;
            return <g key={track.id} role="button" tabIndex={0} aria-label={label} aria-pressed={selected('track', track.id)} className={`sv-track ${isClosed ? 'is-closed' : isOccupied ? 'is-occupied' : ''} ${selected('track', track.id) ? 'is-selected' : ''} ${highlighted.has(track.id) ? 'is-conflict' : ''}`} data-testid={`track-${track.id}`} onClick={e => { e.stopPropagation(); select({ kind: 'track', id: track.id }); }} onKeyDown={e => keySelect(e, { kind: 'track', id: track.id })}>
            <title>{label}{state?.occupant_train_id ? ` · ${state.occupant_train_id}` : ''}</title>
            <polyline className="sv-track-hit" points={polylinePoints(track.geometry)}/>
            <polyline className="sv-track-bed" points={polylinePoints(track.geometry)}/>
            {isClosed && <polyline className="sv-track-hatch" stroke={`url(#${patternId}-closed)`} points={polylinePoints(track.geometry)}/>}
            <polyline className="sv-track-rail" points={polylinePoints(track.geometry)}/>
            {previewTracks.has(track.id) && <polyline className="sv-track-preview" points={polylinePoints(track.geometry)}/>}
            <text x={start[0] - 14} y={start[1] + 7} textAnchor="end" className="sv-track-id">{track.id}</text>
            <text x={end[0] + 18} y={end[1] - 7} className="sv-track-label">{isClosed ? '× ЗАКРЫТ' : isOccupied ? `● ${state?.occupant_train_id}` : !state ? '? НЕТ ДАННЫХ' : trackKinds[track.kind]}</text>
            <text x={end[0] + 18} y={end[1] + 15} className="sv-track-length">{track.usable_length_m} м</text>
            {highlighted.has(track.id) && <text x={start[0] + 16} y={start[1] - 14} className="sv-conflict-label">! КОНФЛИКТ</text>}
          </g>;
        })}
        <g pointerEvents="none">{topology.routes.filter(r => actualRoutes.has(r.id)).map(r => <polyline key={r.id} className="sv-route-actual" points={polylinePoints(r.polyline)}/>)}{topology.routes.filter(r => previewRoutes.has(r.id)).map(r => <polyline key={r.id} className="sv-route-preview" points={polylinePoints(r.polyline)}/>)}</g>
        {activeTrains.map(train => {
            const point = trainPosition(train, topology, time);
            if (!point)
                return null;
            const wait = snapshot.operations.find(o => o.train_id === train.id && o.status === 'pending' && o.wait_reason)?.wait_reason;
            const length = clamp(train.length_m / 5, 94, 156);
            return <g key={train.id} data-testid={`train-${train.id}`} transform={`translate(${point.x} ${point.y})`} className={`sv-train ${train.kind} ${wait ? 'is-waiting' : ''} ${selected('train', train.id) ? 'is-selected' : ''} ${highlighted.has(train.id) ? 'is-conflict' : ''}`} role="button" tabIndex={0} aria-label={`${train.id}, ${kinds[train.kind]}, ${wait ? `ожидание: ${wait}` : train.status === 'moving' ? 'в движении' : 'на пути'}`} aria-pressed={selected('train', train.id)} onClick={e => { e.stopPropagation(); select({ kind: 'train', id: train.id }); }} onKeyDown={e => keySelect(e, { kind: 'train', id: train.id })}>
            <title>{train.id} · {kinds[train.kind]}{wait ? ` · ${wait}` : ''}</title>
            <g transform={`rotate(${point.angle})`}><rect className="sv-train-outline" x={-length / 2 - 4} y="-21" width={length + 8} height="42" rx="11"/><rect className="sv-train-body" x={-length / 2} y="-17" width={length} height="34" rx="7"/>{[-.3, -.1, .1].map(x => <path key={x} d={`M${length * x} -13V13`} className="sv-car-seam"/>)}<path d={`M${length / 2 - 15} -7l8 7-8 7`} className="sv-train-arrow"/></g>
            <text className="sv-train-id" textAnchor="middle" y="6">{train.id} · {train.kind === 'passenger' ? 'П' : train.kind === 'transit' ? 'Т' : 'М'}</text>
            {(wait || highlighted.has(train.id)) && <text className="sv-wait-mark" x={length / 2 + 12} y="6">!</text>}
          </g>;
        })}
      </svg>
      <div className="sv-zoom" aria-label="Управление масштабом"><button type="button" aria-label="Уменьшить масштаб" onClick={() => zoom(1.25)} disabled={camera.width >= 1400}>−</button><span>{Math.round(1400 / camera.width * 100)}%</span><button type="button" aria-label="Увеличить масштаб" onClick={() => zoom(.8)} disabled={camera.width <= 420}>+</button><button type="button" onClick={() => setCamera(initialCamera)} aria-label="Показать всю станцию">↗</button></div>
      <span className="sv-pan-hint">Масштаб + / − · Перетаскивание схемы</span>
    </div>
    <div className="sv-queue"><span className="sv-eyebrow">ОЧЕРЕДЬ У W <b>{waiting.length.toString().padStart(2, '0')}</b></span>{waiting.length ? waiting.map(t => <button type="button" key={t.id} className={selected('train', t.id) ? 'is-selected' : ''} onClick={() => onSelect({ kind: 'train', id: t.id })}>{t.id} <span>{kinds[t.kind]}</span> ↗</button>) : <span className="sv-muted">Нет ожидающих поездов</span>}</div>
    {invalidTrains.length > 0 && <div className="sv-banner sv-warning" role="alert">Не удалось определить позицию: {invalidTrains.map(t => t.id).join(', ')}. Проверьте movement, route_id и geometry.</div>}
    <footer className="sv-legend"><span><i className="free"/>Свободно</span><span><i className="occupied"/>Занято</span><span><i className="waiting"/>Ожидание</span><span><i className="closed"/>Закрыто ×</span><span><i className="actual"/>Движение</span><span><i className="forecast"/>Прогноз</span></footer>
  </section>;
}
