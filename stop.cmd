@echo off
cd /d "%~dp0"
docker compose stop db
echo PostgreSQL stopped. Data is kept. Close the backend and frontend windows yourself.
pause
