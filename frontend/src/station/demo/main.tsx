import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { StationView } from '../StationView';
import type { Selection, Snapshot } from '../types';
import { formatTime } from '../geometry';
import { makePreview, makeSnapshot, topology } from './fixtures';
import './demo.css';
function Demo() {
    const [clock, setClock] = useState(() => ({ snapshot: makeSnapshot(60, 5, true), receivedAt: performance.now() }));
    const source = useRef(clock);
    const [selection, setSelection] = useState<Selection>({ kind: 'train', id: 'T03' });
    const [connected, setConnected] = useState(true);
    const [history, setHistory] = useState(false);
    const [preview, setPreview] = useState(false);
    const [conflict, setConflict] = useState(false);
    const [historySnapshot, setHistorySnapshot] = useState(() => makeSnapshot(35, 5, true));
    const runCount = useRef(1);
    const publish = (snapshot: Snapshot) => { const next = { snapshot, receivedAt: performance.now() }; source.current = next; setClock(next); };
    const currentTime = () => { const c = source.current; return c.snapshot.sim_time_s + (c.snapshot.paused ? 0 : (performance.now() - c.receivedAt) / 1000 * c.snapshot.speed); };
    useEffect(() => {
        if (!connected)
            return;
        const timer = window.setInterval(() => { const s = source.current.snapshot; publish(makeSnapshot(currentTime(), s.speed, s.paused, s.run_id)); }, 1000);
        return () => clearInterval(timer);
    }, [connected]);
    const control = (speed = clock.snapshot.speed, paused = clock.snapshot.paused) => publish(makeSnapshot(currentTime(), speed, paused, clock.snapshot.run_id));
    const snapshot = history ? historySnapshot : clock.snapshot;
    const train = selection?.kind === 'train' ? snapshot.trains.find(t => t.id === selection.id) : null;
    const track = selection?.kind === 'track' ? topology.tracks.find(t => t.id === selection.id) : null;
    const trackState = snapshot.tracks.find(t => t.id === track?.id);
    const operation = snapshot.operations.find(o => o.train_id === train?.id);
    return <div className="demo-shell">
    <header className="demo-top"><div className="demo-brand"><span className="demo-brand-mark">↗</span><strong>УЗЕЛ<span>12</span></strong><i /><span className="demo-top-caption">Цифровая станция</span></div><div className="demo-review">МОДУЛЬ Р <span>Стенд схемы</span></div></header>
    <main className="demo-main">
      <div className="demo-heading"><div><div className="demo-kicker">РАБОЧЕЕ МЕСТО ДИСПЕТЧЕРА / СХЕМА</div><h1>Станция в движении<span>.</span></h1><p>Пути, составы и маршруты — в одном поле зрения.</p></div><div className="demo-build">STATION VIEW <span>v0.1</span></div></div>
      <div className="demo-notice"><span>ДЕМО</span>Тестовые снимки для проверки графики. Backend и планировщик ещё не подключены.</div>
      <div className="demo-controls"><div className="demo-clock"><span>МОДЕЛЬНОЕ ВРЕМЯ</span><strong>{formatTime(snapshot.sim_time_s)}</strong></div><div className="demo-controls-group"><button className="demo-primary" disabled={!connected || history} onClick={() => control(undefined, !clock.snapshot.paused)}>{clock.snapshot.paused ? '▶ Запустить' : 'Ⅱ Пауза'}</button><div className="demo-speed">{([1, 5, 10] as const).map(speed => <button key={speed} disabled={!connected || history} aria-pressed={speed === clock.snapshot.speed} onClick={() => control(speed)}>×{speed}</button>)}</div><button disabled={!connected || history} onClick={() => { runCount.current++; publish(makeSnapshot(0, 5, true, `demo-${runCount.current}`)); setSelection(null); setPreview(false); }}>↺ Сброс</button></div><div className="demo-controls-group demo-modes"><button aria-pressed={preview} onClick={() => setPreview(p => !p)}>Показать прогноз</button><button aria-pressed={history} onClick={() => { if (!history)
        setHistorySnapshot(makeSnapshot(35, 5, true, clock.snapshot.run_id)); setHistory(h => !h); }}>{history ? 'Вернуться в онлайн' : 'История'}</button></div></div>
      <div className="demo-content"><StationView snapshot={snapshot} topology={topology} selection={selection} onSelect={setSelection} previewPlan={preview ? makePreview(snapshot) : null} viewMode={history ? 'history' : 'live'} clockReceivedAtMs={clock.receivedAt} connection={connected ? 'connected' : 'disconnected'} highlightedEntityIds={conflict ? ['P04', 'T03'] : []}/>
        <aside className="demo-sidebar"><section className="demo-card"><div className="demo-card-eyebrow">ВЫБРАННЫЙ ОБЪЕКТ <span>↗</span></div>{train ? <><div className="demo-object-icon">▰</div><h2>{train.id}<span>{({ passenger: 'Пассажирский', transit: 'Транзитный', local: 'Местный грузовой' })[train.kind]}</span></h2><div className={`demo-tag ${operation?.wait_reason ? 'amber' : ''}`}>{operation?.wait_reason ? '● Ожидание ресурса' : ({ moving: '→ В движении', on_track: '● На пути', waiting_entry: '◷ Очередь у W', scheduled: '◷ Запланирован', departed: '✓ Отправлен' })[train.status]}</div><dl><div><dt>Путь</dt><dd>{train.track_id ?? '—'}</dd></div><div><dt>Длина состава</dt><dd>{train.length_m} м</dd></div><div><dt>Приоритет</dt><dd>{train.priority}</dd></div><div><dt>План отправления</dt><dd>{formatTime(train.scheduled_departure_s)}</dd></div><div><dt>Прогноз задержки</dt><dd>Нет данных</dd></div></dl><div className="demo-detail"><span>ТЕКУЩАЯ ОПЕРАЦИЯ</span><p>{operation?.kind ?? (train.status === 'moving' ? `Движение по ${train.movement?.route_id}` : 'Нет данных об операции')}</p>{operation?.wait_reason && <small>{operation.wait_reason}</small>}</div></> : track ? <><div className="demo-object-icon">═</div><h2>{track.id}<span>Станционный путь</span></h2><div className="demo-tag">{trackState?.availability === 'closed' ? '× Закрыт' : trackState?.occupant_train_id ? '● Занят' : '○ Свободен'}</div><dl><div><dt>Полезная длина</dt><dd>{track.usable_length_m} м</dd></div><div><dt>Состав</dt><dd>{trackState?.occupant_train_id ?? 'Нет'}</dd></div><div><dt>Закрыт до</dt><dd>{trackState?.closed_until_s ? formatTime(trackState.closed_until_s) : '—'}</dd></div></dl></> : <div className="demo-empty"><span>⌖</span><h2>Выберите объект</h2><p>Нажмите на поезд или путь, чтобы увидеть его состояние.</p></div>}<p className="demo-panel-note">Карточка стенда · в проекте её заменит панель участника К.</p></section>
        <section className="demo-card demo-issue"><div className="demo-card-eyebrow">ПРОВЕРКА КОНФЛИКТОВ <span>01</span></div><h3><span>!</span> Путь P04 закрыт</h3><p>Проверка подсветки затронутого пути и поезда T03.</p><button aria-pressed={conflict} onClick={() => setConflict(c => !c)}>{conflict ? 'Убрать подсветку' : 'Показать на схеме →'}</button></section>
        <section className="demo-card demo-check"><div className="demo-card-eyebrow">ПРОВЕРКА СОЕДИНЕНИЯ</div><p>При отключении схема возвращается к последнему подтверждённому снимку.</p><button onClick={() => { if (!connected)
        publish(makeSnapshot(currentTime(), clock.snapshot.speed, clock.snapshot.paused, clock.snapshot.run_id)); setConnected(c => !c); }}>{connected ? 'Отключить связь' : 'Восстановить связь'}</button></section></aside>
      </div><footer className="demo-footer"><span><b>●</b> Учебная станция · Топология из ТЗ 1.0</span><span>React / SVG · 15 поездов в наборе · Без управления реальным движением</span></footer>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Demo /></React.StrictMode>);
