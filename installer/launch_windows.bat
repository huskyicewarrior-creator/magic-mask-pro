@echo off
cd /d %~dp0\..
if not exist .venv\Scripts\python.exe (
  echo Virtual environment missing. Run installer\install_windows.ps1 first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
