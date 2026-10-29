@echo off
rem ===========================================================================================
rem  AP Coder for a demo or a rehearsal: the same AP Coder as APProcessor.bat, with a data folder
rem  of its own (APCoder-demo in your user folder), so a demo never mixes with your own invoices,
rem  approvals and settings, and the sample invoices can be imported again however often.
rem
rem  Close the usual AP Coder window first (one AP Coder runs at a time). To start the demo afresh,
rem  close this window and delete the APCoder-demo folder; your own data folder is never touched.
rem  The run sheet: docs\DEMO_RUN_SHEET.md.
rem ===========================================================================================
setlocal
set "AP_PRIVATE_DIR=%USERPROFILE%\APCoder-demo"
call "%~dp0APProcessor.bat" %*
exit /b %errorlevel%
