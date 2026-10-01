# Узел 12 — учебная цифровая станция

Диспетчерская панель (frontend) + backend + планировщик + общие ограничения + симуляция,
собранные в ветке `develop`. Python 3.12, FastAPI, Pydantic, PostgreSQL 16, psycopg;
React 19 + Vite 8, Node 22.

| Часть | Где | Что делает |
| --- | --- | --- |
| Frontend | `frontend/` | Схема станции, Гант, сравнение вариантов плана, сбои, история, ИИ-помощник |
| Backend | `backend/app/{api,runtime,storage,auth}` | HTTP/WS API, единственный владелец состояния, PostgreSQL, вход и роли, CSRF |
| Планировщик | `backend/app/planner` | Два варианта плана (passenger_first, earliest_departure) по данным станции |
| Ограничения | `backend/app/constraints.py` | can_start / validate_plan — общие правила для планировщика и движка |
| Топология | `backend/app/topology.py` | Вход/выход, грузовые фронты, окно планирования — только из `shared/station.json` |
| Симуляция | `backend/app/simulation` | Детерминированный событийный движок, инциденты, завершение прогона |
| Мок | `mock_backend/`, `frontend/src/mock` | Демо без PostgreSQL с тем же контрактом API |

Что работает: полная станция из 15 поездов (80 операций) — начальный план, запуск, пакет
сбоев, два варианта пересчёта, принятие варианта из интерфейса, отправление всех поездов и
автоматическое завершение прогона (`simulation_completed`, часы останавливаются). Окно
планирования вычисляется по расписанию (`app.topology.planning_horizon`), фиксированного
`horizon_s` нет. Идентификаторы путей, ресурсов и узлов в коде не зашиты.

Доступ: вход по cookie-сессии в PostgreSQL, роли viewer/dispatcher/admin, CSRF
(`X-CSRF-Token` из ответа `/api/login` и `/api/me`) и Origin — `docs/auth.md`.
Запуск по-прежнему привязан к `127.0.0.1`.

## Быстрый старт на Windows (двойной щелчок)

Нужны Python 3.12 (подойдут 3.13/3.14), Node.js 22 и запущенный Docker Desktop.

1. `setup.cmd` — один раз: окружение Python, зависимости, `.env` с секретами, PostgreSQL в Docker,
   миграции и начальный план 15 поездов. Пароли пользователей — в `ПАРОЛИ-ДЕМО.txt` (не в git).
2. `start.cmd` — каждый раз: база, backend (окно «backend :8000»), frontend (окно «frontend :5173»)
   и браузер на http://localhost:5173.
3. `stop.cmd` — остановить базу (данные сохраняются). Окна backend/frontend закрыть вручную.
4. `demo-offline.cmd` — запасной вариант без Python, Docker и сети: один HTML со встроенным мок-сервером.

Новый прогон с начала — кнопка «Сброс» в интерфейсе.

## Быстрый старт (Linux/macOS, bash)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements-dev.txt
cp .env.example .env            # заменить пароль в POSTGRES_PASSWORD и DATABASE_URL
export PYTHONPATH=$PWD/backend
python -m app.auth generate-secret            # -> SESSION_SECRET в .env
python -m app.auth hash-password              # -> DISPATCHER_PASSWORD_HASH (и VIEWER_/ADMIN_)
docker compose up -d db
python -m app.storage.migrate
python -m app.storage.bootstrap               # начальный план для shared/station.json
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
# второй терминал
cd frontend && npm ci && npm run dev          # http://localhost:5173, прокси /api и /ws на :8000
```

Демо без backend: `cd frontend && npm run build:demo` — один HTML-файл с мок-сервером.

## Запуск из корня репозитория, PowerShell

Нужны Python 3.12 и PostgreSQL 16. Docker нужен только для предлагаемого способа
поднять PostgreSQL; установленная отдельно PostgreSQL тоже подходит.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
Copy-Item .env.example .env
```

В `.env` замените `POSTGRES_PASSWORD` и тот же пароль в `DATABASE_URL`.
Для простоты локальной настройки используйте случайный пароль из латинских букв
и цифр. Специальные символы в пароле URL требуют percent-encoding.
При уже существующей `.env` не выполняйте повторное копирование поверх неё.

```powershell
docker compose up -d db
$env:PYTHONPATH = "$PWD\backend"
.\.venv\Scripts\python.exe -m app.storage.migrate
.\.venv\Scripts\python.exe -m app.storage.bootstrap
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Если Docker не установлен, создайте пустую базу и пользователя в своей PostgreSQL,
запишите подключение в `DATABASE_URL` и пропустите `docker compose up`.
Миграции применяются явно и транзакционно. Повторный bootstrap возвращает
существующий run_id и не сбрасывает состояние. При переходе с этапа 1 bootstrap
архивирует прежний статический run, сохраняет его историю и создаёт живой сценарий.
reset создаёт новый run_id, возвращает исходный проверенный план и ставит модель
на паузу. Для каждого нового действия используйте новый command_id.

- Документация: http://127.0.0.1:8000/docs
- Готовность БД, схемы и запуска: http://127.0.0.1:8000/health
- Начальный снимок: http://127.0.0.1:8000/api/state

Без БД сервер и `/docs` доступны, но `/health` и `/api/state` отвечают 503.
После запуска БД, миграций и bootstrap перезапустите backend.
Не запускайте несколько workers: один владелец симуляции дополнительно защищён
блокировкой PostgreSQL. После аварии сохранения модель останавливается, а после
перезапуска загружается checkpoint и включается пауза; продолжение — start.

## Запустить станцию через API

В другом окне PowerShell после запуска сервера:

```powershell
$B = 'http://127.0.0.1:8000'
$pw = [Net.NetworkCredential]::new('', (Read-Host 'Пароль dispatcher' -AsSecureString)).Password
$login = Invoke-RestMethod "$B/api/login" -Method Post -ContentType 'application/json' -SessionVariable s `
  -Body (@{ username = 'dispatcher'; password = $pw } | ConvertTo-Json)
$h = @{ 'X-CSRF-Token' = $login.csrf_token }
$state = Invoke-RestMethod "$B/api/state" -WebSession $s
$body = @{ command_id = [guid]::NewGuid().ToString(); run_id = $state.snapshot.run_id; action = 'start' } | ConvertTo-Json
Invoke-RestMethod "$B/api/simulation/control" -Method Post -ContentType 'application/json' -Body $body -Headers $h -WebSession $s
```

Для входа сгенерируйте `SESSION_SECRET` и хеши паролей (`python -m app.auth ...`), см. `docs/auth.md`.
Для паузы используйте action=pause, для сброса action=reset.
Для ускорения — action=speed и дополнительное поле speed=10 (также 1 или 5).
Новый command_id нужен для нового действия; для повтора запроса сохраняйте старый.
События идут по `ws://127.0.0.1:8000/ws` — нужны cookie сессии и разрешённый Origin.
Без входа API отвечает 401, viewer на команды получает 403. После reset заново прочитайте run_id.

## Инциденты — этап 3

Поддерживаются опоздание ещё не прибывшего поезда (`delay_train`), закрытие пути
для новых входов (`close_track`) и недоступность свободного локомотива
(`locomotive_unavailable`). Пакет содержит от 1 до 50 инцидентов: либо применяются
все, либо состояние не меняется. Контракт и примеры — `docs/incidents.md`.

После инцидента `snapshot.replan_required=true`. Это сохранённая потребность
в новом плане; автоматический расчёт запускается в отдельном процессе. Старый план
продолжает проверяться реальными правилами движка, запрещённые старты не выполняются.
Для возврата к исходному сценарию используйте reset.

При обновлении остановите backend, примените миграции (включая
`004_planning_history.sql`) и запустите сервер снова. База и история сохраняются.

## Планы, история и CSV — этапы 4–5

- `POST /api/replans`: `{run_id,command_id}` → 202 с job_id; command_id можно
  опустить, но для безопасного сетевого повтора передавайте его явно.
- `GET /api/replans/{job_id}`: статус, plan_ids, stale, identical, ошибка расчёта.
- `GET /api/plans/{plan_id}`: plan, stale, applicable; назначения, показатели,
  объяснения и changes относительно принятого варианта.
- `POST /api/plans/{plan_id}/apply`: `{run_id,command_id,expected_state_version}`.
  Старый, частичный или недопустимый вариант даёт 409; ошибки не меняют станцию.
- `GET /api/history?run_id=...&at_s=...`: снимок с view=history, read_only=true.
- `GET /api/export.csv?run_id=...`: факты последнего сохранённого состояния;
  необязательный at_s выбирает тот же момент, что и просмотр истории.

Закрытие пути или опоздание автоматически создают задачу пересчёта. Об успешном
расчёте сообщает replan_finished; затем диспетчер выбирает и принимает вариант.
Сам расчёт не изменяет назначения. replan_required очищается только после принятия
плана или reset. Если стратегии совпали, identical=true: фиктивный второй результат
с другими назначениями не создаётся.

Подробный протокол, ошибки и ограничения: `docs/planning-history.md`.
Архивы сейчас сохраняются без автоматического удаления. Старые участки истории,
для которых прежняя версия не сохранила достаточных данных, дают HISTORY_UNAVAILABLE.

## Проверки

Все Python-тесты (backend, планировщик, ограничения, симуляция, mock_backend) — одной командой
из корня; `pyproject.toml` задаёт пути импорта:

```bash
python -m pytest -q                                   # PostgreSQL-тесты skipped без TEST_DATABASE_URL
TEST_DATABASE_URL=postgresql://user:password@localhost:5432/uzel12_test python -m pytest -q
PYTHONPATH=backend python -m unittest discover -s tests/simulation -v
cd frontend && npm test && npm run lint && npm run build
```

CI (`.github/workflows/`) запускает всё это на push и pull request в `develop` и `main`.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# Интеграционная проверка на отдельной тестовой базе PostgreSQL:
$env:TEST_DATABASE_URL = "postgresql://user:password@localhost:5432/uzel12_test"
.\.venv\Scripts\python.exe -m pytest -q -m postgres
```

Без `TEST_DATABASE_URL` проверка PostgreSQL помечается skipped. Интеграционный тест
создаёт случайную схему `test_...` и удаляет только её; пользователю нужны права
CREATE SCHEMA. Результаты фактических запусков — в `tests/results.md`.

Генерация примеров и схем после согласованного изменения моделей:

```powershell
.\.venv\Scripts\python.exe scripts/generate_contracts.py
```

## Передача команде

- Порядок работы и критерии завершения: `docs/backend-plan.md`.
- Формат обмена и разделение ответственности: `docs/contracts.md`.
- Принятые решения и открытые вопросы: `docs/decisions.md`.
- Источник типов: `backend/app/domain/models.py`.
- Сигнатуры подключения В и Н: `backend/app/domain/ports.py` (пока не используются кодом).
- Данные для UI: `shared/examples/`.

`shared/station.json` и реализация `backend/app/simulation` остаются за В;
`backend/app/planner` и правила допустимости — за Н. Их код включён из командной
ветки integration/planner-simulation; происхождение и одна интеграционная правка
описаны в docs/upstream-validation.md. Frontend — `frontend/`, описание — `FRONTEND.md`.
