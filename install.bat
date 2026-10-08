@echo off
rem AP Coder installer / updater for Windows. Double-click it; it is safe to run again.
setlocal
cd /d "%~dp0"
set "PY="
for %%V in (3.12 3.13 3.11 3.10 3.14) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo.
  echo Python 3.10 or newer was not found on this computer.
  echo Install Python 3.12 from https://www.python.org/downloads/
  echo   - tick "Add python.exe to PATH" on the first screen of the Python installer
  echo   - or, in a terminal:  winget install Python.Python.3.12
  echo Then double-click install.bat again.
  echo.
  pause
  exit /b 1
)
%PY% "%~dp0scripts\install.py" %*
set "RC=%ERRORLEVEL%"
echo.
rem AP_NO_PAUSE=1 skips the final pause (automated runs).
if not defined AP_NO_PAUSE pause
exit /b %RC%
