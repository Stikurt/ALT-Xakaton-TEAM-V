# Участник Н — планировщик и общие ограничения

Ветка: `feat/planner`.

Модуль реализует область участника Н из ТЗ: общий валидатор, планирование,
две стратегии и расчёт показателей. Код не меняет фактическое состояние симуляции.

## Основные файлы

- `backend/app/constraints.py` — `can_start`, `validate_plan`, `RulesAdapter`, `RULES`.
- `backend/app/planner/planner.py` — `plan(snapshot, config, strategy, budget_s)`.
- `backend/app/planner/calendars.py` — полуоткрытые ресурсные календари.
- `backend/app/planner/strategies.py` — `passenger_first`, `earliest_departure`.
- `backend/app/planner/metrics.py` — индекс и сравнение метрик.
- `backend/tests` — локальные проверки планировщика и ограничений.

## Контракт с веткой feat/simulation

Движок В ожидает объект `rules`:

```python
from app.planner import RULES

transition = apply_plan(state, plan, rules=RULES)
transition = apply_command(state, command, rules=RULES)
transition = advance_elapsed(state, elapsed_s, rules=RULES)
```

Адаптер предоставляет:

```python
RULES.can_start(context, operation, assignment) -> list[conflict]
RULES.validate_plan(context, plan) -> list[conflict]
```

Пустой список означает, что проверка пройдена. Каждый конфликт содержит минимум
`code` и `message`.

Контекст совместим с `rules_context(state)` из симулятора:

- `snapshot` — динамический снимок;
- `topology` — полный `shared/station.json`;
- `reservations` — резерв целевых путей;
- `busy_zones` — занятые конфликтные зоны;
- `running_assignments` — фактически выполняющиеся назначения.

## Контракт с backend участника И

Для построения варианта:

```python
from app.planner import plan

candidate = plan(
    snapshot=current_snapshot,
    config=station_config,
    strategy="passenger_first",
    budget_s=2.0,
)
```

Второй вариант:

```python
candidate2 = plan(
    snapshot=current_snapshot,
    config=station_config,
    strategy="earliest_departure",
    budget_s=2.0,
)
```

Планировщик должен запускаться вне управляющей транзакции симуляции.
Перед применением backend повторно проверяет `run_id`, `based_on_version` и
`RULES.validate_plan(...)`.

## Запуск локальных тестов

Из корня репозитория:

```powershell
cd backend
py -m pytest tests -v
```

Если используется Python 3.12 launcher:

```powershell
cd backend
py -3.12 -m pytest tests -v
```

## Проверка после объединения с feat/simulation

После того как ветки Н и В окажутся в одной интеграционной ветке:

```powershell
py -3.12 -m unittest discover -s tests/simulation -v
py -3.12 tests/simulation/run_scenarios.py
cd backend
py -3.12 -m pytest tests -v
```

Важно: тестовая `PermissiveRules` из `tests/simulation/support.py` используется
только движком В для изолированных тестов. В общем приложении вместо неё нужно
передавать `app.planner.RULES`.

## Что проверяет валидатор

- предшественники;
- длительности и интервалы;
- начало будущих операций не в прошлом;
- длину и назначение пути;
- маршруты и конфликтные зоны;
- занятость и удержание путей между операциями;
- ресурсы и capabilities;
- закрытые пути и недоступные ресурсы;
- запрет раннего отправления;
- отсутствие двойных назначений;
- полный набор pending-операций для feasible-плана.

## Перед merge

1. Объединить `feat/planner` и `feat/simulation` во временной интеграционной ветке.
2. Использовать реальный `shared/station.json`.
3. Построить оба варианта плана.
4. Проверить `RULES.validate_plan(...)`.
5. Принять план через движок В.
6. Прогнать сценарий закрытия пути P04 и выполнить replan.
7. Только после этого объединять в общую ветку команды.
