@echo off
rem ===========================================================================================
rem  AP Coder - DOUBLE-CLICK THIS FILE. It is the only one you need.
rem
rem  The first time it sets up whatever is missing (a few minutes), checks that everything works
rem  and opens the dashboard in your browser. After that it starts in seconds. Keep this window
rem  open while you use AP Coder; close it to stop.
rem
rem  Offline build: nothing here connects to the internet. An installed Python 3.12 or 3.11
rem  (3.13 too) is used as it is, the .venv folder is made once, and only missing packages are
rem  installed, from the wheelhouse\ folder of the offline bundle.
rem
rem  Developers only: set AP_ALLOW_INTERNET=1 in this window first to let it download packages,
rem  the OCR models, and - when no Python is found - offer to install Python 3.12 with winget.
rem
rem  install.bat and start.bat run this same file; they are kept for older shortcuts.
rem  The steps themselves are in scripts\launch.py.
rem ===========================================================================================
setlocal
rem pushd, not cd: from a network share (\\server\share\AP Coder) it maps a drive letter first.
pushd "%~dp0" || goto :nofolder

rem --yes, or AP_NO_PAUSE=1 as in automated runs: no questions.
set "AP_YES="
if defined AP_NO_PAUSE set "AP_YES=1"
set "AP_ONLINE="
for %%V in (1 true yes on) do if /i "%AP_ALLOW_INTERNET%"=="%%V" set "AP_ONLINE=1"
for %%A in (%*) do if /i "%%~A"=="--yes" set "AP_YES=1"

set "PY="
rem Set up before: AP Coder's own Python in .venv starts it straight away.
if exist ".venv\Scripts\python.exe" call :venvpython
if not defined PY call :findpython
if not defined PY if defined AP_ONLINE call :installpython
if not defined PY goto :nopython

%PY% scripts\launch.py %*
if errorlevel 1 goto :fail
popd
exit /b 0

:venvpython
".venv\Scripts\python.exe" -c "import encodings, pip" >nul 2>&1
if not errorlevel 1 set PY=".venv\Scripts\python.exe"
exit /b 0

:findpython
rem A Python already on this computer: 3.12 or 3.11 first (the offline bundle has packages for them),
rem then 3.13. The py launcher, PATH, or the usual folders.
for %%V in (3.12 3.11 3.13) do if not defined PY call :trypy %%V
if defined PY exit /b 0
python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY exit /b 0
for %%V in (312 311 313) do if not defined PY call :tryfolder "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for %%V in (312 311 313) do if not defined PY call :tryfolder "%ProgramFiles%\Python%%V\python.exe"
exit /b 0

:trypy
py -%1 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY=py -%1"
exit /b 0

:tryfolder
if not exist "%~1" exit /b 0
"%~1" -c "import sys" >nul 2>&1
if not errorlevel 1 set PY="%~1"
exit /b 0

:installpython
rem Developers only (AP_ALLOW_INTERNET=1): offer to install Python 3.12, for this user only,
rem with winget (part of Windows). The offline build never does this.
where winget >nul 2>&1
if errorlevel 1 exit /b 0
echo.
echo AP Coder needs Python - 3.12, 3.11 or 3.13 - and this computer has none of them.
set "ANSWER=Y"
if not defined AP_YES set /p "ANSWER=Install Python 3.12 now from the internet, just for you? It is free and takes about 2 minutes. [Y/n] "
if /i "%ANSWER:~0,1%"=="N" exit /b 0
echo Installing Python 3.12 with winget...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 echo winget could not install Python - see the message above.
call :findpython
exit /b 0

:nopython
echo.
echo AP Coder needs Python 3.11, 3.12 or 3.13 (3.12 is best), and it is not installed.
echo What to do: this is an offline build, so it does not download Python. Ask IT to install Python 3.12 -
echo the installer from https://www.python.org/downloads/ with "Add python.exe to PATH" ticked - then
echo double-click APProcessor.bat again.
goto :fail

:nofolder
echo.
echo AP Coder could not open its own folder: %~dp0
goto :fail

:fail
echo.
echo AP Coder stopped. The message above says what to do.
rem AP_NO_PAUSE=1 skips the pause (automated runs).
if not defined AP_NO_PAUSE pause
popd 2>nul
exit /b 1
