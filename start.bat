@echo off
rem Starts the AP Coder dashboard. Keep this window open while you use it; close it to stop.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo AP Coder is not installed yet: double-click install.bat first.
  pause
  exit /b 1
)
echo Starting AP Coder. It opens in your browser.
echo Keep this window open while you use it; close it to stop.
".venv\Scripts\python.exe" -m ap_coder dashboard
if errorlevel 1 pause
