@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please create .venv and install requirements.txt first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -B app.py
if errorlevel 1 pause
