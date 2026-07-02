@echo off
cd /d %~dp0
docker compose -f docker-compose.yml -f docker-compose.windows.yml up -d --build
pause
