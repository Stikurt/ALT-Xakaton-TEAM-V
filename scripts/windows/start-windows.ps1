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

Write-Host '=== PostgreSQL' -ForegroundColor Cyan
& docker compose up -d db
if ($LASTEXITCODE) { Write-Host 'Docker не запущен: откройте Docker Desktop и повторите.' -ForegroundColor Red; Read-Host 'Enter'; exit 1 }
for ($i = 0; $i -lt 30; $i++) { & $python -m app.storage.migrate 2>$null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2 }
& $python -m app.storage.bootstrap

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
