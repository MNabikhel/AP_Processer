@echo off
rem ===========================================================================================
rem  AP Coder - DOUBLE-CLICK THIS FILE. It is the only one you need.
rem
rem  The first time it sets up whatever is missing (a few minutes), checks that everything works
rem  and opens the dashboard in your browser. After that it starts in seconds. Keep this window
rem  open while you use AP Coder; close it to stop.
rem
rem  Nothing already on this computer is installed twice: an installed Python 3.11-3.13 is used
rem  as it is (only when there is none does it offer to install Python 3.12 with winget), the
rem  .venv folder is made once, and only missing packages are installed. With a wheelhouse\
rem  folder next to this file (the offline bundle) it installs without internet.
rem
rem  install.bat and start.bat run this same file; they are kept for older shortcuts.
rem  The steps themselves are in scripts\launch.py.
rem ===========================================================================================
setlocal
cd /d "%~dp0"

rem --yes, or AP_NO_PAUSE=1 as in automated runs: no questions.
set "AP_YES="
if defined AP_NO_PAUSE set "AP_YES=1"
for %%A in (%*) do if /i "%%~A"=="--yes" set "AP_YES=1"

set "PY="
rem Set up before: AP Coder's own Python in .venv starts it straight away.
if exist ".venv\Scripts\python.exe" call :venvpython
if not defined PY call :findpython
if not defined PY call :installpython
if not defined PY goto :nopython

%PY% scripts\launch.py %*
if errorlevel 1 goto :fail
exit /b 0

:venvpython
".venv\Scripts\python.exe" -c "import encodings, pip" >nul 2>&1
if not errorlevel 1 set PY=".venv\Scripts\python.exe"
exit /b 0

:findpython
rem A Python 3.11 to 3.13 already on this computer: the py launcher, PATH, or the usual folders.
for %%V in (3.12 3.13 3.11) do if not defined PY call :trypy %%V
if defined PY exit /b 0
python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if defined PY exit /b 0
for %%V in (312 313 311) do if not defined PY call :tryfolder "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for %%V in (312 313 311) do if not defined PY call :tryfolder "%ProgramFiles%\Python%%V\python.exe"
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
rem No Python yet: offer to install Python 3.12, for this user only, with winget (part of Windows).
where winget >nul 2>&1
if errorlevel 1 exit /b 0
echo.
echo AP Coder needs Python - 3.11, 3.12 or 3.13 - and this computer has none of them.
set "ANSWER=Y"
if not defined AP_YES set /p "ANSWER=Install Python 3.12 now, just for you? It is free and takes about 2 minutes. [Y/n] "
if /i "%ANSWER:~0,1%"=="N" exit /b 0
echo Installing Python 3.12 with winget...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 echo winget could not install Python - see the message above.
call :findpython
exit /b 0

:nopython
echo.
echo AP Coder needs Python 3.11, 3.12 or 3.13, and it is not installed.
echo What to do: install Python 3.12 from https://www.python.org/downloads/ - tick "Add python.exe to PATH" -
echo then double-click APProcessor.bat again.
goto :fail

:fail
echo.
echo AP Coder stopped. The message above says what to do.
rem AP_NO_PAUSE=1 skips the pause (automated runs).
if not defined AP_NO_PAUSE pause
exit /b 1
