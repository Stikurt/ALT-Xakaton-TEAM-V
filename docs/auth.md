# Доступ: вход, роли и сессии — этап 6

Ветка: `feat/backend-auth` поверх `backend`. Код: `backend/app/auth/`, миграция
`004_auth_sessions.sql`, тесты `tests/test_auth.py`. Регистрации, внешнего
провайдера, второго сервера и SQLite нет.

## Роли

Три фиксированные учётные записи; имя пользователя совпадает с ролью.

| Роль | Права (`permissions`) | Доступ |
|---|---|---|
| viewer | `state:read` | `GET /api/state`, `GET /api/me`, `/ws` |
| dispatcher | + `simulation:control` | + `POST /api/simulation/control`, `/api/incidents`, `/api/incidents/batch` |
| admin | + `config:write` | всё, что dispatcher, + будущие настройки через `require_role("admin")` |

Публичные: `GET /health`, `/docs`, `/openapi.json`, `POST /api/login`, `POST /api/logout`.

**Расширение ТЗ.** ТЗ называет `ADMIN_PASSWORD_HASH` и `DISPATCHER_PASSWORD_HASH`.
Добавлен `VIEWER_PASSWORD_HASH`, чтобы наблюдатель входил отдельной записью и
физически не мог управлять моделью. Пустой хеш отключает соответствующую запись.

## Настройка

Из корня репозитория, PowerShell:

```powershell
$env:PYTHONPATH = "$PWD\backend"
.\.venv\Scripts\python.exe -m app.auth generate-secret   # строка для SESSION_SECRET
.\.venv\Scripts\python.exe -m app.auth hash-password     # для каждой роли: пароль дважды, ввод скрыт
.\.venv\Scripts\python.exe -m app.storage.migrate        # применяет 004_auth_sessions.sql
```

Результаты записываются в `.env`, которая исключена из Git (см. `.env.example`):

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `SESSION_SECRET` | нет | ≥ 32 символов; ключ HMAC для токенов и CSRF. Без него вход отвечает 503 |
| `ADMIN_/DISPATCHER_/VIEWER_PASSWORD_HASH` | нет | вывод `hash-password` |
| `SESSION_COOKIE_SECURE` | `true` | `false` только для локального HTTP |
| `SESSION_COOKIE_SAMESITE` | `strict` | `strict`/`lax`/`none` (`none` требует Secure) |
| `SESSION_TTL_S` | `28800` | абсолютный срок сессии, 8 ч |
| `WS_SESSION_RECHECK_S` | `10` | период перепроверки сессии открытого WebSocket |
| `ALLOWED_ORIGINS` | 5173 localhost/127.0.0.1 | точный список; `*` и `null` отклоняются при старте |

Неверный формат хеша или короткий секрет останавливают запуск с сообщением,
в котором названа переменная, но не её значение. После изменения `.env`
перезапустите backend. Смена хеша пароля или `SESSION_SECRET` делает все прежние
сессии этой записи (или всех) недействительными.

Отзыв без перезапуска: `python -m app.auth revoke-sessions --user dispatcher`
или `--all`. HTTP-доступ прекращается сразу, открытые WebSocket закрываются в
течение `WS_SESSION_RECHECK_S`. Для автоматизации есть `hash-password --stdin`.

## Как устроено

- **Пароли.** scrypt из стандартной библиотеки (RFC 7914), N=2^15, r=8, p=3 —
  профиль OWASP, около 32 МиБ и 0,3 с на проверку. Формат
  `scrypt:<logN>:<r>:<p>:<salt>:<key>` без `$` безопасен для `.env`, PowerShell и
  compose. Сервер отклоняет параметры слабее нижнего профиля OWASP. Проверка
  идёт в рабочем потоке (не блокирует цикл симуляции), не более двух одновременно.
  Для неизвестного имени выполняется та же работа над случайным хешем.
- **Сессия.** Cookie содержит 256-битный случайный токен. PostgreSQL хранит только
  `HMAC-SHA256(SESSION_SECRET, token)`, роль, срок и признак отзыва. Срок решает
  `now()` PostgreSQL. Cookie: HttpOnly, SameSite, Path=/, Max-Age; при
  `SESSION_COOKIE_SECURE=true` — Secure и имя `__Host-uzel12_session`, иначе
  `uzel12_session`. Повторный вход выдаёт новый токен и отзывает старый.
  Строки истёкших/отозванных сессий удаляются через сутки при следующем входе.
- **Порядок проверок** `require_role`: Origin (403) → сессия (401) → CSRF (403)
  → роль (403); всё до разбора тела, поэтому без сессии ответ 401, а не 422.
- **CSRF.** Изменяющим запросам нужен заголовок `X-CSRF-Token`, привязанный к
  сессии (HMAC токена), и разрешённый Origin. Без Origin проверяется Referer;
  без обоих (скрипт, curl) достаточно верного CSRF-токена.
- **CORS.** Точный список origin, `credentials=true`, заголовки `Content-Type`
  и `X-CSRF-Token`, открыт `Retry-After`.
- **WebSocket.** До `accept` проверяются обязательный Origin и сессия; отказ —
  закрытие 1008 (браузер видит неудачное рукопожатие). После `accept` соединение
  закрывается кодом **4401** при истечении срока или отзыве. Logout в этом процессе
  закрывает его сразу, внешний отзыв — за `WS_SESSION_RECHECK_S`. Если сессию
  нельзя проверить (БД недоступна), соединение закрывается 1013.
- **Перебор паролей.** 5 ошибок на пару (клиент, имя) и 20 на клиента за 5 минут,
  затем 429 `LOGIN_RATE_LIMITED` с `Retry-After`. Атака на admin не блокирует
  вход viewer.
- **Секреты.** `SESSION_SECRET`, хеши, пароли и токены не попадают в ответы,
  OpenAPI, ошибки 422 и логи. В лог пишутся только известные имена записей,
  роль и открытый ID сессии.

## Ошибки

Формат прежний: `{code, message, details: []}`.

| HTTP | code | Когда |
|---|---|---|
| 401 | `AUTH_REQUIRED` | нет cookie сессии |
| 401 | `SESSION_EXPIRED` | сессия истекла, отозвана или пароль сменён; сервер удаляет cookie |
| 401 | `INVALID_CREDENTIALS` | неверное имя или пароль (одинаково для обоих случаев) |
| 403 | `FORBIDDEN` | роль недостаточна |
| 403 | `CSRF_FAILED` | нет или неверен `X-CSRF-Token` |
| 403 | `ORIGIN_FORBIDDEN` | Origin/Referer не из `ALLOWED_ORIGINS` (раньше `HTTP_403`, текст тот же) |
| 429 | `LOGIN_RATE_LIMITED` | слишком много неудачных входов |
| 503 | `AUTH_NOT_CONFIGURED` | нет `SESSION_SECRET` или ни одного хеша |

## Frontend

Страница и API должны быть на **одном сайте**: `http://localhost:5173` →
`http://localhost:8000` подходит, а `127.0.0.1` ↔ `localhost` — это разные сайты,
cookie с SameSite=Strict не будет отправлена. Проще всего проксировать `/api` и
`/ws` через dev-сервер Vite. Если UI отдаётся самим backend, добавьте его origin
в `ALLOWED_ORIGINS`. CSRF-токен храните в памяти страницы, не в localStorage;
после перезагрузки получайте его из `GET /api/me`.

```js
const API = 'http://localhost:8000';
let csrf = null;

export async function login(username, password) {
  const r = await fetch(`${API}/api/login`, { method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) });
  if (!r.ok) throw await r.json();                 // {code, message, details}
  const me = await r.json();                       // {username, role, permissions, expires_at, csrf_token}
  csrf = me.csrf_token;
  return me;
}

export async function restoreSession() {           // при загрузке страницы
  const r = await fetch(`${API}/api/me`, { credentials: 'include' });
  if (r.status === 401) return null;
  const me = await r.json(); csrf = me.csrf_token; return me;
}

export async function command(path, body) {        // control, incidents, incidents/batch
  const r = await fetch(`${API}${path}`, { method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify(body) });
  if (r.status === 401) { csrf = null; showLogin(); }
  if (!r.ok) throw await r.json();
  return r.json();
}
// command('/api/simulation/control', { command_id: crypto.randomUUID(), run_id, action: 'speed', speed: 5 })

export async function logout() {
  await fetch(`${API}/api/logout`, { method: 'POST', credentials: 'include',
    headers: { 'X-CSRF-Token': csrf ?? '' } });
  csrf = null;
}

const ws = new WebSocket('ws://localhost:8000/ws'); // cookie и Origin браузер добавит сам
ws.onclose = async (e) => {
  if (e.code === 4401 || e.code === 1006) {          // 1006: отказ рукопожатия (нет сессии)
    if (!(await restoreSession())) showLogin();
  }
};
```

Скрывать кнопки по `permissions` удобно, но защита только на сервере: viewer
получит 403 на любую команду. PowerShell-пример с сессией — в README.

## Подключение будущих маршрутов

```python
from fastapi import Depends
from app.auth import require_role

@router.patch("/api/config", dependencies=[Depends(require_role("admin"))])
```

Изменяющие методы автоматически получают проверку Origin и CSRF. Для PATCH
добавьте `"PATCH"` в `allow_methods` CORS в `main.py`. Тест
`test_every_api_route_is_protected` проходит по всем путям OpenAPI и падает, если
новый `/api/...` маршрут отвечает без сессии не 401.

## Проверки — 2026-10-01

Среда: Linux, Python 3.12.3, PostgreSQL 16.15 (локальный кластер в среде
разработчика, не CI и не ноутбук команды). База ветки: `backend` @ `3fa8fed`.

| Проверка | Результат |
|---|---|
| `origin/backend` без изменений, `pytest -q` | 116 passed, 1 skipped |
| Ветка, `pytest -q` без `TEST_DATABASE_URL` | 158 passed, 3 skipped (пропущены только тесты PostgreSQL) |
| Ветка, `pytest -q` с настоящей PostgreSQL 16.15 | **161 passed, 0 skipped**, 34,5 с |
| Только `tests/test_auth.py` с PostgreSQL | 44 passed |
| `scripts/generate_contracts.py` | повторяем; меняется только openapi.json (новые пути и ответы 401/403) |
| `ruff --select E9,F` по изменённым файлам | новых замечаний нет |

Все 117 тестов базовой ветки сохранены и проходят. Тесты, которые вызывали API без
входа (test_api, test_runtime, test_incidents), теперь входят через тестовую
сессию: `auth_support.login` или `signed_in_client`. Их утверждения не ослаблены
и не помечены skipped. Добавлено 44 теста в test_auth.py.

Тесты с меткой `postgres` (`test_postgres_*` в test_auth.py и test_postgres.py)
используют настоящую PostgreSQL. Остальные тесты авторизации работают с
`MemorySessionStore` — двойником из `tests/auth_support.py`, который приложение
никогда не использует.

Сквозная проверка: Uvicorn + PostgreSQL, migrate + bootstrap, curl и клиент
`websockets`. Результаты: `/health` 200; `/api/state` без сессии 401; неверный
пароль 401; вход 200, cookie HttpOnly; команда без CSRF 403, с CSRF 200; WS с
чужим Origin и без cookie — отказ рукопожатия 403; после logout открытый WS
закрыт 4401 «Session revoked»; `/api/me` 401. В базе 32-байтный хеш, срок 08:00:00.
Секрета и пароля в логе сервера нет. Эту проверку выполнял до перебазирования на
`3fa8fed`; после него она повторена тестом `test_postgres_http_and_websocket_flow`.

## Ограничения

- Только абсолютный срок сессии, без тайм-аута простоя. Нет смены пароля через API
  и регистрации — по ТЗ.
- Ограничение входа хранится в памяти единственного процесса: сбрасывается при
  перезапуске; за обратным прокси клиентом считается прокси (X-Forwarded-For не
  доверяется).
- Сессия проверяется запросом к PostgreSQL на каждый защищённый запрос; пул общий
  с моделью. Нагрузка 5 клиентов этапа 6 не измерялась.
- До `accept` браузер не узнаёт причину отказа WebSocket — frontend вызывает
  `/api/me`. Внешний отзыв доходит до WS с задержкой до `WS_SESSION_RECHECK_S`.
- `/ws` теперь требует Origin; клиенты не из браузера должны его передавать.
- `/health` не проверяет наличие таблицы `auth_sessions`: без миграции 004 вход
  отвечает 503 `DATABASE_UNAVAILABLE`.
- HTTPS не настраивается этим кодом; `/docs` и `/openapi.json` публичны.
