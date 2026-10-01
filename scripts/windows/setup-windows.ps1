# Узел 12 — первичная настройка на Windows (запускать один раз; повторный запуск безопасен).
# Делает: venv + зависимости backend, .env с секретами и паролями, npm ci, PostgreSQL в Docker, миграции, bootstrap.
# Ошибки внешних программ проверяем по коду возврата ($LASTEXITCODE): в Windows PowerShell 5.1
# 'Stop' превращает любую запись в stderr (например, предупреждение pip) в аварийную остановку.
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root

function Step($text) { Write-Host "`n=== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`n[ОШИБКА] $text" -ForegroundColor Red; Read-Host 'Нажмите Enter, чтобы закрыть'; exit 1 }

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
  $envText = @"
POSTGRES_USER=uzel12
POSTGRES_DB=uzel12
POSTGRES_PASSWORD=$dbpw
DATABASE_URL=postgresql://uzel12:$dbpw@localhost:5432/uzel12
ALLOWED_ORIGINS=["http://localhost:5173","http://127.0.0.1:5173"]
DB_POOL_MAX_SIZE=4
DB_TIMEOUT_S=3
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
& docker compose up -d db
if ($LASTEXITCODE) { Fail 'docker compose up не прошёл' }

Step 'Миграции и начальный план (15 поездов)'
$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  & $python -m app.storage.migrate 2>$null
  if ($LASTEXITCODE -eq 0) { $ok = $true; break }
  Start-Sleep -Seconds 2
}
if (-not $ok) { Fail 'База не отвечает. Проверьте Docker Desktop и запустите снова.' }
& $python -m app.storage.bootstrap
if ($LASTEXITCODE) { Fail 'bootstrap не прошёл' }

Write-Host "`nГотово. Теперь запускайте start.cmd" -ForegroundColor Green
Read-Host 'Нажмите Enter, чтобы закрыть'
