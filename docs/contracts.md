# Контракты backend, schema_version=1

Источник истины — `backend/app/domain/models.py`. JSON Schema:
`shared/schemas/contracts.schema.json`. OpenAPI отражает **только реализованные**
маршруты. Будущие API перечислены в плане, но не зарегистрированы как заглушки.

## Состояние

`GET /api/state` возвращает `{schema_version, snapshot, topology}`.
snapshot содержит run_id, state_version, last_seq, sim_time_s, speed, paused,
trains, tracks, resources, operations, active_plan_id и replan_required.
replan_required=true означает потребность в новом принятом плане. Признак сохраняется
после перезапуска и расчёта, включается при инциденте/ожидании операции/ручном запросе
и очищается успешным принятием плана или reset.
topology содержит station_id, name, view_box, boundary_nodes, conflict_zones, routes.
Пути и их geometry находятся в snapshot.tracks. Координаты — единицы SVG;
usable_length_m и length_m — метры. `null` означает отсутствие назначения.
Точка geometry/polyline — массив `[x,y]`, как в существующем модуле В.
Snapshot также содержит schema_version и conflicts. Во время движения исходный
track_id сохраняется, а целевой резерв находится во внутреннем State движка.
Фактический occupant_train_id целевого пути появляется по завершении движения.

Списки сущностей используют стабильные строковые ID; время целое и неотрицательное.
Интервалы `[start_s,end_s)` имеют положительную длину. Отправление считается
завершённым у E, не в момент начала движения с пути.
У текущего неразрешённого конфликта end_s=null; это открытое ожидание, а не
нулевой интервал. Operation.wait_reason — `{code,message}` либо null, как у В.

Состояния resource: available/unavailable. Занятость определяется отдельно по
active_operation_id; cargo_front представлен ресурсами F10/F11. Это уточнения
контракта, которые необходимо подтвердить с В и Н.

## Три счётчика

| Поле | Значение |
|---|---|
| state_version | Версия существенного изменения состояния внутри run |
| seq / last_seq | Порядок сохранённых событий внутри run |
| ws_seq | Порядок отправленных сообщений одного соединения, начиная с 1 |

run_id в примерах равен `example-run`, а bootstrap выдаёт новый UUID. Клиент не
должен зашивать ID из примеров. При смене run или разрыве последовательности
получать согласованный snapshot. Пауза не останавливает сетевое соединение.

## WebSocket — контракт этапа 2

Конверт: `{schema_version, run_id, ws_seq, state_version, type, payload}`.

| type | payload |
|---|---|
| snapshot | StateResponse: schema_version, snapshot, topology |
| state_updated | `{snapshot: Snapshot}` — полный динамический снимок |
| clock_sync | `{sim_time_s, speed, paused}` |
| replan_started | `{job_id, based_on_version}` |
| replan_finished | `{job_id, plan_ids, based_on_version, stale, identical}` |
| replan_failed | `{job_id, code, message}` |
| simulation_error | `{code, message}` |

Сейчас WsEnvelope проверяет конверт; payload описан таблицей и примерами.
Типизированный discriminated union payload добавляется при реализации WS.
Примеры каждого типа — отдельные сообщения, не последовательный журнал.
Все перечисленные события реализованы. replan_* относятся к сохранённым задачам;
успешный расчёт не означает допустимость всех вариантов или автоматическое принятие.

## Команды и ошибки

ControlCommand: command_id, run_id, action=start/pause/speed/reset;
speed обязателен только для action=speed и равен 1, 5 или 10.
IncidentCommand: command_id, run_id, kind, target_id; delay_train требует delay_s,
close_track и locomotive_unavailable требуют duration_s. Параметры положительные.
`POST /api/incidents/batch`: command_id, run_id, incidents — от 1 до 50 IncidentSpec
без отдельных command_id/run_id внутри списка. Пакет атомарный.
Успешный ответ обеих команд и control: `{command_id,run_id,state_version,replan_required}`.
Результат повтора совпадает с первоначальным ответом, поэтому его версия может быть
старее текущей. Отказы движка тоже сохраняются для существующего run_id;
структурные ошибки 422 и неизвестные run_id не сохраняются. Новая попытка после
изменения условий требует нового command_id. Подробности — incidents.md.
ApplyPlanCommand: command_id, run_id, expected_state_version.

На этапе 4 Plan дополнен based_on_time_s, calculation_time_ms, violations и changes.
Операционные unassigned и текстовые explanations нормализуются из формата Н в
runtime/planning.py. Изменения содержат operation_id, before/after Assignment либо null.
GET плана оборачивает Plan в `{plan,stale,applicable}`; GET задачи перечисляет plan_ids.
Полные форматы API планирования и истории — planning-history.md.

Ошибка: `{code, message, details: []}`. Секреты, входные пароли, SQL и traceback
в ответ не включаются. Доступны 503 DATABASE_UNAVAILABLE / NOT_INITIALIZED /
SIMULATION_UNAVAILABLE, 409 ошибок движка и повторного ID с другим телом,
422 валидации, единый формат 404/405. Пока 403 относится только к отклонённому
Origin; роли ещё не реализованы.

## Границы участников

| Владелец | Вход | Выход |
|---|---|---|
| И (вы) | HTTP-команды, Transition от В, Plan от Н | БД, версии, API/WS, история |
| В | Внутренний State, команда/время/Plan, общие rules | Transition: state, events, result, replan_required |
| Н | Копия Snapshot, стратегия, бюджет | Plan; список Conflict для проверки |
| К | StateResponse, WS, ошибки | Команды пользователя с уникальным command_id |
| Р | snapshot, topology и выбор объекта от К | Схема и анимация по movement |

Pydantic проверяет структуру и ссылки, но не доказывает отсутствие конфликтов
станции. Жёсткие правила и проверка физической допустимости — функции Н.
SimulationPort в `ports.py` отражает существующий модуль В на commit d4d2b62;
PlannerPort пока предложение. Адаптер состояния и координатор подключены на этапе 2. Движок
предварительно назначает seq/event_id/state_version, а backend подтверждает их
сохранением. recorded_at и ws_seq назначает backend. Отдельные модели в каждом
модуле создавать не нужно.
