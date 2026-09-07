@echo off
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Virtual environment not found. Run: py -m venv .venv
  echo Then run: .venv\Scripts\python.exe -m pip install -r requirements.txt
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$processes = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*GabrielHMIS*app.py*' }; $processes | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
"%~dp0.venv\Scripts\python.exe" app.py
