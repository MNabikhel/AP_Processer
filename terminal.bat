@echo off
rem Opens a command prompt ready for AP Coder commands, e.g.  python -m ap_coder doctor --online
rem The .venv's Scripts folder goes first on PATH. Not activate.bat: it keeps the folder the .venv was made in,
rem so after this folder is moved or copied, "python" would be the system Python.
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo AP Coder is not installed yet: double-click APProcessor.bat first.
  pause
  popd
  exit /b 1
)
set "VIRTUAL_ENV=%~dp0.venv"
set "PATH=%~dp0.venv\Scripts;%PATH%"
cmd /k "echo AP Coder terminal. Try:  python -m ap_coder doctor"
popd
