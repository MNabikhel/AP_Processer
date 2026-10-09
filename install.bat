@echo off
rem Most people don't need this file: double-click APProcessor.bat instead (it sets up and starts AP Coder).
rem install.bat is kept for older instructions and scripts. It runs the same setup as APProcessor.bat
rem (through it, so Python is found and reused the same way) plus the installer's questions: the data
rem folder, the Azure settings and the desktop shortcut. It does not start the dashboard unless you say so.
rem Options: --yes --no-update --skip-tests --no-start --fresh-start --data-dir PATH --no-shortcut --reinstall
setlocal
call "%~dp0APProcessor.bat" --installer %*
set "RC=%ERRORLEVEL%"
rem APProcessor.bat already paused on a failure; AP_NO_PAUSE=1 skips the pause (automated runs).
if "%RC%"=="0" if not defined AP_NO_PAUSE pause
exit /b %RC%
