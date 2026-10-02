# Узел 12 — первичная настройка на Windows (запускать один раз; повторный запуск безопасен).
# Делает: venv + зависимости backend, .env с секретами и паролями, npm ci, PostgreSQL в Docker, миграции, bootstrap.
# Ошибки внешних программ проверяем по коду возврата ($LASTEXITCODE): в Windows PowerShell 5.1
# 'Stop' превращает любую запись в stderr (например, предупреждение pip) в аварийную остановку.
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n[ОШИБКА] $text" -ForegroundColor Red; Read-Host 'Нажмите Enter, чтобы закрыть'; exit 1 }

# Порт PostgreSQL на этом компьютере берётся из DATABASE_URL в .env и передаётся docker compose
# (DB_HOST_PORT), поэтому compose.yaml и backend всегда смотрят на один и тот же порт.
function Get-DbPort {
  $line = Get-Content "$root\.env" -Encoding UTF8 | Where-Object { $_ -match '^DATABASE_URL=' } | Select-Object -First 1
  if ($line -match '@[^@/]+:(\d+)/') { return [int]$Matches[1] }
  return 5432
}
function Test-PortFree([int]$port) {
  try { $l = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $port); $l.Start(); $l.Stop(); return $true } catch { return $false }
}
function Test-DbContainerUp {
  $id = (& docker compose ps -q --status running db 2>$null)
  return [bool]$id
}

# Демо-пароли для презентации (минимум 12 символов — требование backend). Поменяйте при желании до первого запуска.
$Passwords = @{ DISPATCHER = 'dispatcher2026'; VIEWER = 'viewer-2026-demo'; ADMIN = 'admin-2026-demo' }

Step 'Проверяю Python'
$py = $null
foreach ($v in '3.12', '3.13', '3.14') {
  try { $global:LASTEXITCODE = 1; & py "-$v" -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { $py = @('py', "-$v"); break } } catch {}
}
if (-not $py) {
  try {
    $ver = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null)
    if ($ver -in '3.12', '3.13', '3.14') { $py = @('python', '-I') }
  } catch {}
}
if (-not $py) { Fail 'Не найден Python 3.12–3.14. Установите Python 3.12 с python.org (галочка "Add to PATH") и запустите снова.' }
Write-Host "Python: $($py -join ' ')"

Step 'Проверяю Node.js'
try { $node = (& node --version) } catch { Fail 'Не найден Node.js. Установите Node.js 22 LTS с nodejs.org и запустите снова.' }
Write-Host "Node: $node"

Step 'Проверяю Docker'
$global:LASTEXITCODE = 1
try { & docker info *> $null } catch {}
if ($LASTEXITCODE -ne 0) { Fail 'Docker не запущен. Установите/запустите Docker Desktop (иконка кита должна стать зелёной) и запустите снова.' }

Step 'Виртуальное окружение и зависимости backend'
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { & $py[0] $py[1] -m venv "$root\.venv"; if ($LASTEXITCODE) { Fail 'Не удалось создать .venv' } }
$python = "$root\.venv\Scripts\python.exe"
& $python -m pip install --upgrade pip -q
& $python -m pip install -q -r "$root\backend\requirements-dev.txt"
if ($LASTEXITCODE) { Fail 'pip install не прошёл' }
$env:PYTHONPATH = "$root\backend"

Step 'Файл .env'
if (Test-Path "$root\.env") {
  Write-Host '.env уже есть — оставляю как есть (пароли и база не меняются).'
} else {
  $chars = [char[]]'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  $dbpw = -join (1..24 | ForEach-Object { $chars | Get-Random })
  $secret = (& $python -m app.auth generate-secret).Trim()
  $hash = @{}
  foreach ($k in $Passwords.Keys) { $hash[$k] = ($Passwords[$k] | & $python -m app.auth hash-password --stdin).Trim() }
  # Первый свободный порт: 5432 часто занят локальным PostgreSQL или зарезервирован Windows (Hyper-V/WinNAT).
  $dbport = $null
  foreach ($p in 5432, 55432, 55433, 56432, 57432) { if (Test-PortFree $p) { $dbport = $p; break } }
  if (-not $dbport) { Fail 'Нет свободного порта для PostgreSQL (пробовал 5432, 55432, 55433, 56432, 57432).' }
  Write-Host "PostgreSQL будет на 127.0.0.1:$dbport"
  $envText = @"
POSTGRES_USER=uzel12
POSTGRES_DB=uzel12
POSTGRES_PASSWORD=$dbpw
DB_HOST_PORT=$dbport
DATABASE_URL=postgresql://uzel12:$dbpw@127.0.0.1:$dbport/uzel12
ALLOWED_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
DB_POOL_MAX_SIZE=4
DB_TIMEOUT_S=10
SCENARIO_PATH=shared/station.json
PLANNER_TIMEOUT_S=5
PLANNER_BUDGET_S=2
SESSION_SECRET=$secret
ADMIN_PASSWORD_HASH=$($hash.ADMIN)
DISPATCHER_PASSWORD_HASH=$($hash.DISPATCHER)
VIEWER_PASSWORD_HASH=$($hash.VIEWER)
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=strict
SESSION_TTL_S=28800
WS_SESSION_RECHECK_S=10
"@
  [IO.File]::WriteAllText("$root\.env", $envText, (New-Object Text.UTF8Encoding($false)))
  $creds = "Учётные записи (локальный стенд)`r`n dispatcher / $($Passwords.DISPATCHER)`r`n viewer     / $($Passwords.VIEWER)`r`n admin      / $($Passwords.ADMIN)`r`n"
  [IO.File]::WriteAllText("$root\ПАРОЛИ-ДЕМО.txt", $creds, (New-Object Text.UTF8Encoding($true)))
  Write-Host '.env создан, пароли записаны в ПАРОЛИ-ДЕМО.txt'
}

Step 'Зависимости frontend (npm ci)'
Push-Location "$root\frontend"; & npm ci --no-audit --no-fund; $code = $LASTEXITCODE; Pop-Location
if ($code) { Fail 'npm ci не прошёл' }

Step 'PostgreSQL в Docker'
$env:DB_HOST_PORT = Get-DbPort
if (-not (Test-DbContainerUp) -and -not (Test-PortFree $env:DB_HOST_PORT)) {
  Fail "Порт $($env:DB_HOST_PORT) занят другой программой или зарезервирован Windows. Удалите .env и запустите setup.cmd снова — будет выбран свободный порт (пароли создадутся заново)."
}
& docker compose up -d db
if ($LASTEXITCODE) { Fail "docker compose up не прошёл (порт $($env:DB_HOST_PORT)). Смотрите сообщение выше." }

Step 'Миграции и начальный план (15 поездов)'
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  & $python -m app.storage.migrate 2>$null
  if ($LASTEXITCODE -eq 0) { $ok = $true; break }
  Start-Sleep -Seconds 2
}
if (-not $ok) { Fail "База не отвечает на 127.0.0.1:$($env:DB_HOST_PORT). Проверьте Docker Desktop и DATABASE_URL в .env, затем запустите снова." }
& $python -m app.storage.bootstrap
if ($LASTEXITCODE) { Fail 'bootstrap не прошёл' }

Write-Host "`nГотово. Теперь запускайте start.cmd" -ForegroundColor Green
Read-Host 'Нажмите Enter, чтобы закрыть'
