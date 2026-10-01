# Узел 12 — backend

Бэкенд учебной цифровой станции, этапы 1–3. Ветка разработки: `backend`.
Python 3.12, FastAPI, Pydantic, PostgreSQL 16, psycopg.

## Что уже реализовано

- Общие модели станции, состояния, операций, планов, команд и событий.
- Примеры JSON и схемы для параллельной разработки команды.
- FastAPI: `GET /health`, `GET /api/state`, `POST /api/simulation/control`, `/ws`, `/docs`.
- PostgreSQL: миграции, ограниченный пул соединений и начальное заполнение.
- Проверки контрактов, HTTP API и отдельный интеграционный тест PostgreSQL.
- Настоящие движок В и планировщик Н; один поезд T01 от приёма до отправления.
- Последовательные команды start/pause/speed/reset, постоянная идемпотентность.
- WebSocket: snapshot, state_updated, clock_sync, simulation_error.
- Сохранение очереди движка и восстановление после перезапуска в паузе.
- Инциденты через `POST /api/incidents` и атомарные пакеты `POST /api/incidents/batch`.
- Постоянные результаты принятых и отклонённых команд; одна ожидающая заявка
  перепланирования на запуск, сохранённая вместе с эффектом инцидента.

Сейчас работает **вертикальный сценарий одного поезда**. Начальный план реально
рассчитывается Н в отдельном процессе во время bootstrap, проверяется и сохраняется
до start. Полный сценарий 15 поездов пока не проходит валидатор Н; причины
зафиксированы в `docs/upstream-validation.md`. Поэтому SCENARIO_PATH по умолчанию
указывает на shared/scenarios/one_train.json.

Ещё не реализованы фоновое перепланирование/принятие вариантов, история и CSV.
Доступ (этап 6): вход по cookie-сессии в PostgreSQL, роли viewer/dispatcher/admin,
CSRF и Origin — `docs/auth.md`. Запуск по-прежнему привязан к `127.0.0.1`.

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

Для входа сгенерируйте секрет и хеши паролей (ввод скрыт) и запишите их в `.env`
в `SESSION_SECRET` и `*_PASSWORD_HASH`. Подробности — `docs/auth.md`.

```powershell
$env:PYTHONPATH = "$PWD\backend"
.\.venv\Scripts\python.exe -m app.auth generate-secret
.\.venv\Scripts\python.exe -m app.auth hash-password   # для dispatcher, viewer, admin
```

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

## Запустить один поезд

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

Без входа API отвечает 401, viewer на команды получает 403. Выход:
`Invoke-RestMethod "$B/api/logout" -Method Post -Headers $h -WebSession $s`.

Для паузы используйте action=pause, для сброса action=reset.
Для ускорения — action=speed и дополнительное поле speed=10 (также 1 или 5).
Новый command_id нужен для нового действия; для повтора запроса сохраняйте старый.
События идут по `ws://127.0.0.1:8000/ws` — нужны cookie сессии и разрешённый Origin.
После reset заново прочитайте run_id.

## Инциденты — этап 3

Поддерживаются опоздание ещё не прибывшего поезда (`delay_train`), закрытие пути
для новых входов (`close_track`) и недоступность свободного локомотива
(`locomotive_unavailable`). Пакет содержит от 1 до 50 инцидентов: либо применяются
все, либо состояние не меняется. Контракт и примеры — `docs/incidents.md`.

После инцидента `snapshot.replan_required=true`. Это сохранённая потребность
в новом плане; автоматический расчёт будет подключён на этапе 4. Старый план
продолжает проверяться реальными правилами движка, запрещённые старты не выполняются.
Для возврата к исходному сценарию используйте reset.

При обновлении с этапа 2 остановите backend, примените миграции (включая
`003_replan_requests.sql`) и запустите сервер снова. База и история сохраняются.

## Проверки

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
- Сигнатуры подключения В и Н: `backend/app/domain/ports.py`.
- Данные для UI: `shared/examples/`.

`shared/station.json` и реализация `backend/app/simulation` остаются за В;
`backend/app/planner` и правила допустимости — за Н. Их код включён из командной
ветки integration/planner-simulation; происхождение и одна интеграционная правка
описаны в docs/upstream-validation.md. Ветка backend не содержит frontend.
