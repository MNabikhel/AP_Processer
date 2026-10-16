@echo off
rem Opens a command prompt in AP Coder's folder, ready for AP Coder commands, e.g.  python -m ap_coder doctor
rem or  APProcessor.bat --installer  (the installer's options). AP Coder's folder is the one above scripts\.
rem The .venv's Scripts folder goes first on PATH. Not activate.bat: it keeps the folder the .venv was made in,
rem so after this folder is moved or copied, "python" would be the system Python.
rem setlocal: run from an open prompt, PATH and the folder are as they were once this prompt is closed.
setlocal
pushd "%~dp0.." || exit /b 1
if not exist ".venv\Scripts\python.exe" (
  echo AP Coder is not installed yet: double-click APProcessor.bat first.
  if not defined AP_NO_PAUSE pause
  popd
  exit /b 1
)
set "VIRTUAL_ENV=%CD%\.venv"
set "PATH=%CD%\.venv\Scripts;%PATH%"
cmd /k "echo AP Coder terminal. APProcessor.bat already checks everything when it starts; for a report to send:  python -m ap_coder doctor --online"
popd
