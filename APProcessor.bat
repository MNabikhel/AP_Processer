@echo off
rem Double-click to start AP Coder: the review dashboard opens in your browser.
rem The first run sets everything up (a few minutes). Keep this window open while you use it.
rem With a wheelhouse\ folder next to this file (the offline bundle) it installs without internet.
setlocal
cd /d "%~dp0"

set "PIPFROM="
if exist "wheelhouse\" set "PIPFROM=--no-index --find-links wheelhouse"

if not exist ".venv\Scripts\python.exe" (
  call :setup
  if errorlevel 1 goto :fail
)

rem An update can add a package; install it before starting.
".venv\Scripts\python.exe" scripts\check_deps.py >nul 2>&1
if errorlevel 1 (
  echo Installing what this update needs...
  call :packages
  if errorlevel 1 goto :fail
  ".venv\Scripts\python.exe" scripts\first_run.py
)

echo Starting AP Coder. It opens in your browser.
echo Keep this window open while you use it; close it to stop.
".venv\Scripts\python.exe" -m ap_coder dashboard %*
if errorlevel 1 goto :fail
exit /b 0

:setup
echo First run: setting up AP Coder. This takes a few minutes.
call :findpython
if not defined PY (
  echo.
  echo Python 3.11 or newer is needed.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
  echo or in a terminal:  winget install Python.Python.3.12
  echo Then double-click APProcessor again.
  exit /b 1
)
%PY% -m venv .venv
if errorlevel 1 exit /b 1
call :packages
if errorlevel 1 exit /b 1
rem The data folder and the OCR models (an extra: a failure here never stops the setup).
".venv\Scripts\python.exe" scripts\first_run.py
echo Setup finished.
exit /b 0

:packages
if defined PIPFROM echo Installing from the wheelhouse folder (no internet needed).
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check %PIPFROM% --upgrade pip >nul 2>&1
rem The OCR add-on reads scanned PDFs; if it can't install here, AP Coder runs without it.
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check %PIPFROM% -e ".[ocr]" || ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check %PIPFROM% -e .
exit /b %errorlevel%

:findpython
set "PY="
for %%V in (3.12 3.11 3.13 3.14) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1 && set "PY=python"
)
exit /b 0

:fail
echo.
echo AP Coder stopped. The message above says why.
rem AP_NO_PAUSE=1 skips the pause (automated runs).
if not defined AP_NO_PAUSE pause
exit /b 1
