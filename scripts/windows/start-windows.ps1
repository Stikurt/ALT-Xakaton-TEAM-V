# Узел 12 — запуск стенда: PostgreSQL (Docker) + backend :8000 + frontend :5173, затем браузер.
# Ошибки внешних программ проверяем по коду возврата ($LASTEXITCODE): в Windows PowerShell 5.1
# 'Stop' превращает любую запись в stderr (например, предупреждение pip) в аварийную остановку.
$ErrorActionPreference = 'Continue'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $root
$python = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $python) -or -not (Test-Path "$root\.env")) {
  Write-Host 'Сначала один раз запустите setup.cmd' -ForegroundColor Red; Read-Host 'Enter'; exit 1
}
$env:PYTHONPATH = "$root\backend"
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

Write-Host '=== PostgreSQL' -ForegroundColor Cyan
$env:DB_HOST_PORT = Get-DbPort
$global:LASTEXITCODE = 1
try { & docker info *> $null } catch {}
if ($LASTEXITCODE -ne 0) { Fail 'Docker не запущен: откройте Docker Desktop, дождитесь зелёного статуса и повторите.' }
if (-not (Test-DbContainerUp) -and -not (Test-PortFree $env:DB_HOST_PORT)) {
  Fail "Порт $($env:DB_HOST_PORT) (из DATABASE_URL в .env) занят другой программой или зарезервирован Windows. Освободите его или удалите .env и запустите setup.cmd — он выберет свободный порт."
}
& docker compose up -d db
if ($LASTEXITCODE) { Fail "docker compose up не прошёл (порт $($env:DB_HOST_PORT)). Смотрите сообщение выше." }
$ok = $false
for ($i = 0; $i -lt 30; $i++) { & $python -m app.storage.migrate 2>$null; if ($LASTEXITCODE -eq 0) { $ok = $true; break }; Start-Sleep 2 }
if (-not $ok) { Fail "База не отвечает на 127.0.0.1:$($env:DB_HOST_PORT). Проверьте DATABASE_URL в .env и контейнер в Docker Desktop." }
& $python -m app.storage.bootstrap
if ($LASTEXITCODE) { Fail 'bootstrap не прошёл — смотрите сообщение выше.' }

Write-Host '=== backend (окно «backend»), frontend (окно «frontend»)' -ForegroundColor Cyan
$backendCmd = "`$host.UI.RawUI.WindowTitle='backend :8000'; Set-Location '$root'; `$env:PYTHONPATH='$root\backend'; & '$python' -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1"
$frontendCmd = "`$host.UI.RawUI.WindowTitle='frontend :5173'; Set-Location '$root\frontend'; npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"
Start-Process powershell -ArgumentList '-NoExit', '-Command', $backendCmd
Start-Process powershell -ArgumentList '-NoExit', '-Command', $frontendCmd

function Wait-Url($url) {
  for ($i = 0; $i -lt 60; $i++) {
    try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 $url; if ($r.StatusCode -lt 500) { return $true } } catch {}
    Start-Sleep 1
  }
  return $false
}
if (-not (Wait-Url 'http://127.0.0.1:8000/health')) { Write-Host 'backend не поднялся — смотрите окно «backend».' -ForegroundColor Red }
if (-not (Wait-Url 'http://127.0.0.1:5173/')) { Write-Host 'frontend не поднялся — смотрите окно «frontend».' -ForegroundColor Red }
Start-Process 'http://localhost:5173'
Write-Host "`nСтенд запущен: http://localhost:5173  (пароли — в ПАРОЛИ-ДЕМО.txt)." -ForegroundColor Green
Write-Host 'Остановить: закройте окна backend и frontend; базу — stop.cmd.'
Start-Sleep 5
