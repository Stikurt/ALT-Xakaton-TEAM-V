@echo off
rem Backup for the presentation: one HTML file with a built-in mock server. No Python, Docker or internet needed.
cd /d "%~dp0frontend"
if not exist node_modules call npm ci --no-audit --no-fund || goto :fail
call npm run build:demo || goto :fail
start "" "%~dp0frontend\dist-demo\uzel12-demo.html"
exit /b 0
:fail
echo [FAILED] Send the text above to the chat.
pause
