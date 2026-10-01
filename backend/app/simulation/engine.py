"""Deterministic, copy-on-write station execution. No database, network or planner.

Rules are injected by participant Н. All public transitions leave their input intact.
The backend persists a Transition before replacing its live state or publishing it.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import heapq
import json
import math
from typing import Any, Protocol
from uuid import uuid4

Json = dict[str, Any]
MOVING = {'arrival', 'departure', 'shunt_to_cargo', 'shunt_to_storage', 'shunt_to_departure'}
DURATIONS = {'arrival': 120, 'departure': 120, 'dwell': 360, 'inspection': 480,
             'preparation': 180, 'shunt_to_cargo': 180, 'cargo': 900,
             'shunt_to_storage': 180, 'formation': 300, 'shunt_to_departure': 180}


class SimulationError(ValueError):
    def __init__(self, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


class Rules(Protocol):
    """Empty conflict list means permitted; exceptions abort the whole transition."""
    def can_start(self, context: Json, operation: Json, assignment: Json) -> list[Json]: ...
    def validate_plan(self, context: Json, plan: Json) -> list[Json]: ...


@dataclass
class State:
    config: Json
    run_id: str
    trains: dict[str, Json]
    tracks: dict[str, Json]
    resources: dict[str, Json]
    operations: dict[str, Json]
    sim_time_s: int = 0
    state_version: int = 0
    last_seq: int = 0
    speed: int = 1
    paused: bool = True
    active_plan: Json | None = None
    assignments: dict[str, Json] = field(default_factory=dict)
    running: dict[str, Json] = field(default_factory=dict)
    reservations: dict[str, str] = field(default_factory=dict)
    zones: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, Json] = field(default_factory=dict)
    queue: list = field(default_factory=list)
    serial: int = 0
    fractional_s: float = 0.0
    commands: dict[str, Json] = field(default_factory=dict)


@dataclass
class Transition:
    state: State
    events: list[Json]
    result: Json = field(default_factory=dict)
    replan_required: bool = False


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise SimulationError('INVALID_INPUT', f'{name}: требуется целое число >= {minimum}')
    return value


def _index(items: list[Json], label: str) -> dict[str, Json]:
    result = {}
    for item in items:
        ident = item.get('id')
        if not isinstance(ident, str) or not ident or ident in result:
            raise SimulationError('INVALID_CONFIG', f'{label}: пустой или повторный id')
        result[ident] = deepcopy(item)
    return result


def _schedule(s: State, at: int, phase: int, ident: str, kind: str, payload: Any):
    s.serial += 1
    heapq.heappush(s.queue, (at, phase, ident, s.serial, kind, payload))


def create_initial_state(config: Json, *, run_id: str | None = None) -> State:
    """Create a paused run. Config is JSON from shared/station.json."""
    c = deepcopy(config)
    s = State(c, run_id or str(uuid4()), *[_index(c[k], k) for k in
              ('trains', 'tracks', 'resources', 'operations')])
    routes = _index(c['routes'], 'routes')
    for r in routes.values():
        if r['from_id'] not in {*s.tracks, 'W'} or r['to_id'] not in {*s.tracks, 'E'}:
            raise SimulationError('INVALID_CONFIG', 'Неизвестный конец маршрута')
        _integer(r['duration_s'], 'duration_s', 1)
    for t in s.tracks.values():
        _integer(t['usable_length_m'], 'usable_length_m', 1)
        t.update(availability='open', closed_until_s=None, occupant_train_id=None)
    for r in s.resources.values():
        r.update(availability='available', unavailable_until_s=None, active_operation_id=None)
    for t in s.trains.values():
        for key in ('scheduled_arrival_s', 'scheduled_departure_s'):
            _integer(t[key], key)
        _integer(t['length_m'], 'length_m', 1)
        t.update(status='scheduled', track_id=None, movement=None,
                 expected_arrival_s=t['scheduled_arrival_s'])
        _schedule(s, t['expected_arrival_s'], 2, t['id'], 'arrival', t['expected_arrival_s'])
    for o in s.operations.values():
        if o['train_id'] not in s.trains or o['kind'] not in DURATIONS:
            raise SimulationError('INVALID_CONFIG', 'Неизвестный поезд/тип операции')
        if o['duration_s'] != DURATIONS[o['kind']]:
            raise SimulationError('INVALID_CONFIG', 'Длительность не соответствует модели')
        for pred in o['predecessor_ids']:
            if pred not in s.operations or s.operations[pred]['train_id'] != o['train_id']:
                raise SimulationError('INVALID_CONFIG', 'Неверный предшественник')
        o.update(status='pending', actual_start_s=None, actual_end_s=None, wait_reason=None)
    seen, visiting = set(), set()
    def visit(oid):
        if oid in visiting:
            raise SimulationError('INVALID_CONFIG', 'Цикл предшественников')
        if oid in seen:
            return
        visiting.add(oid)
        for pred in s.operations[oid]['predecessor_ids']:
            visit(pred)
        visiting.remove(oid)
        seen.add(oid)
    for oid in s.operations:
        visit(oid)
    return s


def snapshot(s: State) -> Json:
    """Public dynamic snapshot; callers cannot mutate the live state through it."""
    return deepcopy(dict(schema_version=1, run_id=s.run_id, state_version=s.state_version,
        last_seq=s.last_seq, sim_time_s=s.sim_time_s, speed=s.speed, paused=s.paused,
        trains=[s.trains[k] for k in sorted(s.trains)], tracks=[s.tracks[k] for k in sorted(s.tracks)],
        resources=[s.resources[k] for k in sorted(s.resources)],
        operations=[s.operations[k] for k in sorted(s.operations)],
        active_plan_id=s.active_plan['id'] if s.active_plan else None,
        conflicts=[s.conflicts[k] for k in sorted(s.conflicts)]))


def rules_context(s: State) -> Json:
    return dict(snapshot=snapshot(s), topology=deepcopy(s.config),
                reservations=deepcopy(s.reservations), busy_zones=deepcopy(s.zones),
                running_assignments=deepcopy(s.running))


def _emit(s: State, events: list, kind: str, entity: str, payload: Json):
    s.state_version += 1
    s.last_seq += 1
    events.append(dict(event_id=f'{s.run_id}:{s.last_seq}', run_id=s.run_id, seq=s.last_seq,
                       sim_time_s=s.sim_time_s, type=kind, entity_id=entity,
                       payload=deepcopy(payload)))


def _rules(rules: Rules | None) -> Rules:
    if rules is None:
        raise SimulationError('VALIDATOR_REQUIRED', 'Подключите can_start и validate_plan участника Н')
    return rules


def _conflicts(value: Any) -> list[Json]:
    if not isinstance(value, list) or any(not isinstance(c, dict) or
            not c.get('code') or not c.get('message') for c in value):
        raise SimulationError('VALIDATOR_ERROR', 'Валидатор должен вернуть список конфликтов')
    return value


def _check_plan(s: State, plan: Json, rules: Rules) -> dict[str, Json]:
    if plan.get('run_id') != s.run_id or plan.get('based_on_version') != s.state_version:
        raise SimulationError('STALE_PLAN', 'План другого запуска или устаревшей версии')
    if plan.get('status') != 'feasible' or plan.get('unassigned'):
        raise SimulationError('INVALID_PLAN', 'Принимается только полный допустимый план')
    if not isinstance(plan.get('id'), str) or not plan['id']:
        raise SimulationError('INVALID_PLAN', 'Нет идентификатора плана')
    routes = {r['id']: r for r in s.config['routes']}
    assignments = {}
    for a in plan['assignments']:
        oid = a['operation_id']
        if oid in assignments or oid not in s.operations:
            raise SimulationError('INVALID_PLAN', 'Повторная или неизвестная операция')
        o = s.operations[oid]
        _integer(a['start_s'], 'start_s')
        _integer(a['end_s'], 'end_s', 1)
        if a['end_s'] - a['start_s'] != o['duration_s']:
            raise SimulationError('INVALID_PLAN', 'Неверная длительность назначения')
        if o['status'] == 'pending' and a['start_s'] < s.sim_time_s:
            raise SimulationError('STALE_PLAN', 'Назначение начинается в прошлом')
        if a['track_id'] not in s.tracks:
            raise SimulationError('INVALID_PLAN', 'Неизвестный путь назначения')
        if o['kind'] in MOVING:
            r = routes.get(a['route_id'])
            if not r or r['duration_s'] != o['duration_s']:
                raise SimulationError('INVALID_PLAN', 'Неизвестный маршрут или длительность')
            endpoint = r['from_id'] if o['kind'] == 'departure' else r['to_id']
            if endpoint != a['track_id']:
                raise SimulationError('INVALID_PLAN', 'Путь не соответствует маршруту')
        elif a['route_id'] is not None:
            raise SimulationError('INVALID_PLAN', 'У неподвижной операции не должно быть маршрута')
        if len(a['resource_ids']) != len(set(a['resource_ids'])) or any(
                rid not in s.resources for rid in a['resource_ids']):
            raise SimulationError('INVALID_PLAN', 'Неверный список ресурсов')
        if o['status'] == 'running' and a != s.assignments[oid]:
            raise SimulationError('INVALID_PLAN', 'Выполняющееся назначение менять нельзя')
        if o['status'] == 'completed' and a != s.assignments.get(oid):
            raise SimulationError('INVALID_PLAN', 'Завершённое назначение менять нельзя')
        assignments[oid] = deepcopy(a)
    required = {k for k, o in s.operations.items() if o['status'] == 'pending'}
    if not required <= assignments.keys():
        raise SimulationError('INVALID_PLAN', 'Не все будущие операции размещены')
    conflicts = _conflicts(rules.validate_plan(rules_context(s), deepcopy(plan)))
    if conflicts:
        raise SimulationError('INVALID_PLAN', 'Общий валидатор отклонил план', conflicts)
    return assignments


def apply_plan(state: State, plan: Json, *, rules: Rules | None = None) -> Transition:
    """Apply future assignments only. Running completions are never removed."""
    checked = _check_plan(state, plan, _rules(rules))
    s, events = deepcopy(state), []
    s.queue = [e for e in s.queue if e[4] != 'start']
    heapq.heapify(s.queue)
    s.assignments = {k: a for k, a in s.assignments.items()
                     if s.operations[k]['status'] in ('running', 'completed')}
    s.assignments.update(checked)
    s.active_plan = deepcopy(plan)
    for oid, a in sorted(checked.items()):
        if s.operations[oid]['status'] == 'pending':
            _schedule(s, a['start_s'], 3, oid, 'start', plan['id'])
            s.operations[oid]['wait_reason'] = None
    s.conflicts.clear()
    _emit(s, events, 'plan_applied', plan['id'], {'plan_id': plan['id']})
    return Transition(s, events)


def _guard(s: State, o: Json, a: Json) -> tuple[str, str] | None:
    """Execution invariants, not a substitute for Н's full plan validator."""
    t = s.trains[o['train_id']]
    if any(s.operations[p]['status'] != 'completed' for p in o['predecessor_ids']):
        return 'PREDECESSOR_INCOMPLETE', 'Предшествующая операция не завершена'
    if t['movement'] or any(s.operations[k]['train_id'] == t['id'] for k in s.running):
        return 'RESOURCE_UNAVAILABLE', 'Поезд уже выполняет операцию'
    if o['kind'] == 'arrival':
        if t['status'] != 'waiting_entry' or s.sim_time_s < t['expected_arrival_s']:
            return 'PREDECESSOR_INCOMPLETE', 'Поезд ещё не прибыл к W'
    elif t['status'] != 'on_track':
        return 'PREDECESSOR_INCOMPLETE', 'Поезд не находится на станционном пути'
    if o['kind'] == 'departure' and t['kind'] in ('passenger', 'transit') and (
            s.sim_time_s + o['duration_s'] < t['scheduled_departure_s']):
        return 'NO_FEASIBLE_SLOT', 'Раннее отправление запрещено'
    track = s.tracks[a['track_id']]
    if track['usable_length_m'] < t['length_m']:
        return 'NO_FEASIBLE_SLOT', 'Поезд длиннее полезной длины пути'
    kind = o['kind']
    expected_kind = ('passenger' if t['kind'] == 'passenger' else 'freight') if kind in (
        'arrival', 'departure') else {'shunt_to_cargo': 'cargo', 'cargo': 'cargo',
        'shunt_to_storage': 'storage', 'formation': 'storage',
        'shunt_to_departure': 'freight'}.get(kind)
    if expected_kind and track['kind'] != expected_kind:
        return 'NO_FEASIBLE_SLOT', 'Назначение пути несовместимо с операцией'
    if kind in MOVING:
        route = next(r for r in s.config['routes'] if r['id'] == a['route_id'])
        if route['from_id'] != ('W' if kind == 'arrival' else t['track_id']):
            return 'NO_FEASIBLE_SLOT', 'Маршрут не начинается в фактическом положении поезда'
        if (kind == 'departure') != (route['to_id'] == 'E'):
            return 'NO_FEASIBLE_SLOT', 'Неверное направление маршрута'
        if any(z in s.zones for z in route['conflict_zone_ids']):
            return 'ROUTE_BUSY', 'Горловина занята другим перемещением'
        if route['to_id'] != 'E':
            if track['availability'] == 'closed':
                return 'TRACK_CLOSED', 'Целевой путь закрыт для новых входов'
            if track['occupant_train_id'] not in (None, t['id']) or track['id'] in s.reservations:
                return 'TRACK_OCCUPIED', 'Целевой путь занят или зарезервирован'
    elif t['track_id'] != a['track_id']:
        return 'NO_FEASIBLE_SLOT', 'Нельзя выполнять операцию на другом пути'
    if kind != 'arrival' and s.tracks[t['track_id']]['occupant_train_id'] != t['id']:
        return 'TRACK_OCCUPIED', 'Поезд не владеет исходным путём'
    selected = [s.resources[rid] for rid in a['resource_ids']]
    if any(r['availability'] != 'available' or r['active_operation_id'] for r in selected):
        return 'RESOURCE_UNAVAILABLE', 'Назначенный ресурс занят или недоступен'
    needs = ([('locomotive', 'shunt'), ('crew', 'shunt')] if kind.startswith('shunt_') else
             [('crew', kind)] if kind in ('inspection', 'preparation', 'formation') else
             [('cargo_front', 'cargo')] if kind == 'cargo' else [])
    for resource_kind, capability in needs:
        if not any(r['kind'] == resource_kind and capability in r['capabilities'] for r in selected):
            return 'RESOURCE_UNAVAILABLE', f'Не назначен ресурс {resource_kind}/{capability}'
    if kind == 'cargo' and f'F{a["track_id"][1:]}' not in a['resource_ids']:
        return 'RESOURCE_UNAVAILABLE', 'Грузовой фронт не соответствует пути'
    return None


def _attempt(s: State, oid: str, events: list, rules: Rules):
    o, a = s.operations[oid], s.assignments[oid]
    # Always consult the shared validator before executing, even when a local guard fails.
    external = _conflicts(rules.can_start(rules_context(s), deepcopy(o), deepcopy(a)))
    blocked = _guard(s, o, a)
    if blocked is None and external:
        blocked = external[0]['code'], external[0]['message']
    if blocked:
        code, message = blocked
        reason = {'code': code, 'message': message}
        if o['wait_reason'] != reason:
            o['wait_reason'] = reason
            conflict = dict(id=f'wait:{oid}', code=code, severity='error',
                entity_ids=[o['train_id']], operation_ids=[oid], start_s=s.sim_time_s,
                end_s=None, message=message)
            s.conflicts[oid] = conflict
            _emit(s, events, 'operation_waiting', oid, {'conflict': conflict})
        return
    s.conflicts.pop(oid, None)
    o.update(status='running', actual_start_s=s.sim_time_s, wait_reason=None)
    t = s.trains[o['train_id']]
    running = deepcopy(a)
    running.update(start_s=s.sim_time_s, end_s=s.sim_time_s + o['duration_s'])
    s.running[oid] = running
    for rid in a['resource_ids']:
        s.resources[rid]['active_operation_id'] = oid
    if o['kind'] in MOVING:
        route = next(r for r in s.config['routes'] if r['id'] == a['route_id'])
        for z in route['conflict_zone_ids']:
            s.zones[z] = oid
        if route['to_id'] != 'E':
            s.reservations[route['to_id']] = oid
        t.update(status='moving', movement=dict(route_id=route['id'],
            started_at_s=s.sim_time_s, expected_end_at_s=running['end_s']))
    _schedule(s, running['end_s'], 0, oid, 'finish', None)
    _emit(s, events, 'operation_started', oid, {'operation': o, 'assignment': running,
                                               'movement': t['movement']})


def _finish(s: State, oid: str, events: list):
    o, a = s.operations[oid], s.running.pop(oid)
    t = s.trains[o['train_id']]
    if o['kind'] in MOVING:
        route = next(r for r in s.config['routes'] if r['id'] == a['route_id'])
        if t['track_id'] is not None:
            s.tracks[t['track_id']]['occupant_train_id'] = None
        for z in route['conflict_zone_ids']:
            del s.zones[z]
        if route['to_id'] == 'E':
            t.update(status='departed', track_id=None, movement=None)
        else:
            dest = route['to_id']
            del s.reservations[dest]
            s.tracks[dest]['occupant_train_id'] = t['id']
            t.update(status='on_track', track_id=dest, movement=None)
    for rid in a['resource_ids']:
        s.resources[rid]['active_operation_id'] = None
    o.update(status='completed', actual_end_s=s.sim_time_s)
    _emit(s, events, 'operation_completed', oid, {'operation': o, 'train': t})


def _advance(s: State, target: int, events: list, rules: Rules):
    while s.queue and s.queue[0][0] <= target:
        now = s.queue[0][0]
        s.sim_time_s = now
        while s.queue and s.queue[0][0] == now:
            _, _, ident, _, kind, payload = heapq.heappop(s.queue)
            if kind == 'finish':
                _finish(s, ident, events)
            elif kind == 'restore_track':
                t = s.tracks[ident]
                if t['closed_until_s'] == payload:
                    t.update(availability='open', closed_until_s=None)
                    _emit(s, events, 'track_reopened', ident, {})
            elif kind == 'restore_resource':
                r = s.resources[ident]
                if r['unavailable_until_s'] == payload:
                    r.update(availability='available', unavailable_until_s=None)
                    _emit(s, events, 'resource_restored', ident, {})
            elif kind == 'incident':
                try:
                    _incident(s, payload, events)
                except SimulationError as exc:
                    _emit(s, events, 'incident_rejected', ident,
                          {'code': exc.code, 'message': exc.message})
            elif kind == 'arrival':
                t = s.trains[ident]
                if t['status'] == 'scheduled' and t['expected_arrival_s'] == payload:
                    t['status'] = 'waiting_entry'
                    _emit(s, events, 'train_arrived', ident, {})
            # 'start' marks a wake-up only; all due attempts are sorted together below.
        for oid, a in sorted(s.assignments.items()):
            if s.operations[oid]['status'] == 'pending' and a['start_s'] <= now:
                _attempt(s, oid, events, rules)
    s.sim_time_s = target


def advance_to(state: State, target_s: int, *, rules: Rules | None = None) -> Transition:
    _integer(target_s, 'target_s')
    if target_s < state.sim_time_s:
        raise SimulationError('INVALID_TIME', 'Время нельзя переводить назад')
    s, events = deepcopy(state), []
    if not s.paused:
        _advance(s, target_s, events, _rules(rules))
    return Transition(s, events, replan_required=any(e['type'] in (
        'incident_applied', 'operation_waiting') for e in events))


def advance_elapsed(state: State, elapsed_s: float, *, rules: Rules | None = None) -> Transition:
    """Backend supplies a monotonic real delta. Fractional model seconds are retained."""
    if isinstance(elapsed_s, bool) or not isinstance(elapsed_s, (int, float)) or not math.isfinite(elapsed_s) or elapsed_s < 0:
        raise SimulationError('INVALID_TIME', 'Неверный реальный интервал')
    if state.paused:
        return Transition(deepcopy(state), [])
    total = state.fractional_s + elapsed_s * state.speed
    whole = math.floor(total + 1e-9)
    result = advance_to(state, state.sim_time_s + whole, rules=rules)
    result.state.fractional_s = max(0.0, total - whole)
    return result


def _incident(s: State, incident: Json, events: list):
    kind, target = incident.get('kind'), incident.get('target_id')
    if kind == 'delay_train':
        if target not in s.trains or s.trains[target]['status'] != 'scheduled':
            raise SimulationError('INCIDENT_REJECTED', 'Опоздание возможно только для scheduled поезда')
        delay = _integer(incident.get('delay_s', 300), 'delay_s', 1)
        t = s.trains[target]
        t['expected_arrival_s'] += delay
        _schedule(s, t['expected_arrival_s'], 2, target, 'arrival', t['expected_arrival_s'])
    elif kind == 'close_track':
        if target not in s.tracks:
            raise SimulationError('INCIDENT_REJECTED', 'Путь не существует')
        if target in s.reservations:
            raise SimulationError('INCIDENT_REJECTED', 'На путь уже начато движение')
        until = s.sim_time_s + _integer(incident.get('duration_s', 600), 'duration_s', 1)
        t = s.tracks[target]
        until = max(until, t['closed_until_s'] or 0)
        t.update(availability='closed', closed_until_s=until)
        _schedule(s, until, 1, target, 'restore_track', until)
    elif kind == 'locomotive_unavailable':
        r = s.resources.get(target)
        if not r or r['kind'] != 'locomotive' or r['active_operation_id']:
            raise SimulationError('INCIDENT_REJECTED', 'Локомотив не существует или занят')
        until = s.sim_time_s + _integer(incident.get('duration_s', 600), 'duration_s', 1)
        until = max(until, r['unavailable_until_s'] or 0)
        r.update(availability='unavailable', unavailable_until_s=until)
        _schedule(s, until, 1, target, 'restore_resource', until)
    else:
        raise SimulationError('INVALID_INPUT', 'Неизвестный тип сбоя')
    _emit(s, events, 'incident_applied', target, incident)


def apply_command(state: State, command: Json, *, rules: Rules | None = None) -> Transition:
    """One command, atomic batch of incidents, or a control action. Idempotent per run."""
    cid = command.get('command_id')
    if not isinstance(cid, str) or not cid:
        raise SimulationError('INVALID_INPUT', 'Нужен command_id')
    key = json.dumps([command.get('run_id'), cid])
    fingerprint = json.dumps(command, sort_keys=True, ensure_ascii=False)
    if key in state.commands:
        previous = state.commands[key]
        if previous['fingerprint'] != fingerprint:
            raise SimulationError('COMMAND_ID_REUSED', 'command_id уже использован с другим содержимым')
        return Transition(deepcopy(state), [], deepcopy(previous['result']))
    if command.get('run_id') != state.run_id:
        raise SimulationError('STALE_RUN', 'Команда другого запуска')
    s, events = deepcopy(state), []
    action, replan = command.get('action'), False
    if action == 'start':
        validator = _rules(rules)
        if s.active_plan is None:
            raise SimulationError('PLAN_REQUIRED', 'Нельзя запускать без принятого допустимого плана')
        candidate = deepcopy(s.active_plan)
        # Revalidate remaining work against now; historical assignments are frozen.
        candidate['based_on_version'] = s.state_version
        problems = _conflicts(validator.validate_plan(rules_context(s), candidate))
        if problems:
            raise SimulationError('INVALID_PLAN', 'Перед запуском требуется новый допустимый план', problems)
        if s.paused:
            s.paused = False
            _emit(s, events, 'simulation_started', s.run_id, {})
        _advance(s, s.sim_time_s, events, validator)
    elif action == 'pause':
        if not s.paused:
            s.paused = True
            _emit(s, events, 'simulation_paused', s.run_id, {})
    elif action == 'speed':
        speed = _integer(command.get('speed'), 'speed', 1)
        if speed not in (1, 5, 10):
            raise SimulationError('INVALID_INPUT', 'Скорость должна быть 1, 5 или 10')
        if s.speed != speed:
            s.speed = speed
            _emit(s, events, 'speed_changed', s.run_id, {'speed': speed})
    elif action == 'reset':
        old_run = s.run_id
        cache = s.commands
        s = create_initial_state(s.config)
        s.commands = cache
        _emit(s, events, 'simulation_reset', s.run_id, {'previous_run_id': old_run})
    elif action in ('incident', 'incidents'):
        items = command.get('incidents') if action == 'incidents' else [command.get('incident')]
        if not isinstance(items, list) or not items or any(not isinstance(i, dict) for i in items):
            raise SimulationError('INVALID_INPUT', 'Нужен непустой список сбоев')
        for item in items:
            _incident(s, item, events)
        replan = True
    else:
        raise SimulationError('INVALID_INPUT', 'Неизвестное действие')
    result = {'command_id': cid, 'run_id': s.run_id, 'state_version': s.state_version}
    s.commands[key] = {'fingerprint': fingerprint, 'result': deepcopy(result)}
    return Transition(s, events, result, replan or any(e['type'] == 'operation_waiting' for e in events))


def schedule_incidents(state: State, incidents: list[Json]) -> State:
    """Scenario/replay hook, not an HTTP endpoint. Entries: id, at_s, incident.

    At equal time completions run first, then these incidents, then arrivals/starts.
    Future validity is checked when the incident executes; rejection is an event.
    """
    s = deepcopy(state)
    used = {e[2] for e in s.queue if e[4] == 'incident'}
    for item in incidents:
        at = _integer(item['at_s'], 'at_s')
        if at < s.sim_time_s or not isinstance(item.get('id'), str) or not item['id'] or item['id'] in used:
            raise SimulationError('INVALID_INPUT', 'Неверное время/id запланированного сбоя')
        used.add(item['id'])
        _schedule(s, at, 1, item['id'], 'incident', deepcopy(item['incident']))
    return s
