@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Virtual environment not found. Run: py -m venv .venv
  echo Then run: .venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" app.py
