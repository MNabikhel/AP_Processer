@echo off
rem ===========================================================================================
rem  AP Coder - DOUBLE-CLICK THIS FILE. It is the only one you need.
rem
rem  The first time it sets up whatever is missing (a few minutes), checks that everything works
rem  and opens the dashboard in your browser. After that it starts in seconds. Keep this window
rem  open while you use AP Coder; close it to stop.
rem
rem  Offline build: AP Coder itself never connects to the internet. An installed Python 3.11 or
rem  newer (3.12 recommended) is used as it is, the .venv folder is made once, and only missing
rem  packages are installed: from the wheelhouse\ folder of the offline bundle, never the internet.
rem
rem  From GitHub's ZIP (the code alone, no offline bundle) the setup downloads the packages and
rem  the OCR models once, like CloseDesk, and - when no Python is found - offers to install
rem  Python 3.12 with winget. set AP_ALLOW_INTERNET=0 in this window first to forbid that.
rem
rem  The installer's questions (data folder, Azure settings, shortcut): APProcessor.bat --installer,
rem  typed in scripts\terminal.bat. Older versions' install.bat and start.bat are gone: this file does both.
rem  The steps themselves are in scripts\launch.py.
rem ===========================================================================================
setlocal
rem pushd, not cd: from a network share (\\server\share\AP Coder) it maps a drive letter first.
pushd "%~dp0" || goto :nofolder
rem Opened inside the ZIP (Windows unpacks only this file) or copied out of its folder: one plain message.
if not exist "scripts\launch.py" goto :notunzipped

rem --yes, or AP_NO_PAUSE=1 as in automated runs: no questions.
set "AP_YES="
if defined AP_NO_PAUSE set "AP_YES=1"
set "AP_ONLINE="
for %%V in (1 true yes on) do if /i "%AP_ALLOW_INTERNET%"=="%%V" set "AP_ONLINE=1"
rem The code alone (GitHub's ZIP or a git checkout): its setup may download, as scripts\launch.py does.
if not exist "bundle_manifest.json" if not exist "wheelhouse\" if not exist "models\" set "AP_ONLINE=1"
for %%V in (0 false no off) do if /i "%AP_ALLOW_INTERNET%"=="%%V" set "AP_ONLINE="
for %%A in (%*) do if /i "%%~A"=="--yes" set "AP_YES=1"

set "PY="
set "OLDPY="
set "OLDV="
rem Asking py for a version that is not installed must never download it (Python install manager).
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
rem Set up before: AP Coder's own Python in .venv starts it straight away (whatever Python made it),
rem unless scripts\launch.py --venv-ok says to make .venv again (see :venvpython).
if exist ".venv\Scripts\python.exe" call :venvpython
if not defined PY call :findpython
if not defined PY if defined AP_ONLINE call :installpython
if not defined PY goto :nopython

%PY% scripts\launch.py %*
if errorlevel 1 goto :fail
popd
exit /b 0

:venvpython
rem .venv's Python is used when it starts, has pip, and launch.py does not ask for it to be made again
rem (the offline bundle has no packages for its Python, AP Coder is not set up yet, and a Python
rem the bundle has packages for is installed: then that one is found below and .venv is remade).
".venv\Scripts\python.exe" scripts\launch.py --venv-ok >nul 2>&1
if not errorlevel 1 set PY=".venv\Scripts\python.exe"
exit /b 0

:findpython
rem A Python 3.11 or newer already on this computer. 3.12 first, then 3.11, 3.13 and 3.14 (the
rem offline bundle has packages for them), then any newer one: the py launcher, then python and
rem python3 on PATH, then the usual folders.
for %%V in (3.12 3.11 3.13 3.14) do if not defined PY call :try py -%%V
if not defined PY call :try py -3
if not defined PY call :try python
if not defined PY call :try python3
for %%V in (312 311 313 314) do if not defined PY call :tryfolder "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for %%V in (312 311 313 314) do if not defined PY call :tryfolder "%ProgramFiles%\Python%%V\python.exe"
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PY call :tryfolder "%%~D\python.exe"
for /d %%D in ("%ProgramFiles%\Python3*") do if not defined PY call :tryfolder "%%~D\python.exe"
exit /b 0

:try
rem %* is a Python command (py -3.12, python, or a quoted path). It exits with its version as a
rem number (311 = 3.11): 3.11 or newer is used; the newest older one is remembered for the
rem message, which then says it is too old (not that Python is missing).
%* -c "import sys; sys.exit(sys.version_info[0] * 100 + sys.version_info[1])" <nul >nul 2>&1
set /a "PYV=%ERRORLEVEL%"
if %PYV% LSS 200 exit /b 0
if %PYV% GEQ 400 exit /b 0
if %PYV% GEQ 311 set PY=%*
if defined PY exit /b 0
if not defined OLDV set "OLDV=0"
if %PYV% LEQ %OLDV% exit /b 0
set /a "OLDV=PYV, OLDMAJOR=PYV / 100, OLDMINOR=PYV %% 100"
set "OLDPY=%OLDMAJOR%.%OLDMINOR%"
exit /b 0

:tryfolder
if exist "%~1" call :try "%~1"
exit /b 0

:installpython
rem From GitHub's ZIP or with AP_ALLOW_INTERNET=1: offer to install Python 3.12, for this user
rem only, with winget (part of Windows). The offline bundle never does this.
where winget >nul 2>&1
if errorlevel 1 exit /b 0
echo.
if defined OLDPY echo Python %OLDPY% is installed, but AP Coder needs Python 3.11 or newer.
if not defined OLDPY echo AP Coder needs Python 3.11 or newer, and this computer has none.
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
if defined OLDPY echo Python %OLDPY% is installed, but AP Coder needs Python 3.11 or newer (3.12 is best).
if not defined OLDPY echo AP Coder needs Python 3.11 or newer (3.12 is best), and it is not installed.
if defined AP_ONLINE echo What to do: install Python 3.12 - the installer from https://www.python.org/downloads/
if defined AP_ONLINE echo with "Add python.exe to PATH" ticked - then double-click APProcessor.bat again.
if defined AP_ONLINE goto :fail
echo What to do: this is an offline build, so it does not download Python. Ask IT to install Python 3.12 -
echo the installer from https://www.python.org/downloads/ with "Add python.exe to PATH" ticked - then
echo double-click APProcessor.bat again.
goto :fail

:nofolder
echo.
echo AP Coder could not open its own folder: "%~dp0"
goto :fail

:notunzipped
echo.
echo AP Coder can't find its files next to APProcessor.bat: there is no scripts\launch.py in "%CD%".
echo What to do: the ZIP was opened without unzipping it, or APProcessor.bat was copied out of its folder.
echo Right-click the ZIP, choose Extract All, then double-click APProcessor.bat in the extracted folder
echo (or use the AP Coder shortcut on the desktop).
goto :fail

:fail
echo.
echo AP Coder stopped. The message above says what to do.
rem AP_NO_PAUSE=1 skips the pause (automated runs).
if not defined AP_NO_PAUSE pause
popd 2>nul
exit /b 1
