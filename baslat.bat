@echo off
cd /d "%~dp0"
start "kule" cmd /k python -m uvicorn app.main:app --port 8790
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8790
