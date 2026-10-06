@echo off
rem Opens a command prompt ready for AP Coder commands, e.g.  python -m ap_coder doctor --online
cd /d "%~dp0"
if not exist ".venv\Scripts\activate.bat" (
  echo AP Coder is not installed yet: double-click install.bat first.
  pause
  exit /b 1
)
cmd /k ".venv\Scripts\activate.bat && echo AP Coder terminal. Try:  python -m ap_coder doctor"
