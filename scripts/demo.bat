@echo off
rem ===========================================================================================
rem  AP Coder for a demo or a rehearsal: the same AP Coder as APProcessor.bat, with a data folder
rem  of its own (APCoder-demo in your user folder), so a demo never mixes with your own invoices,
rem  approvals and settings, and the sample invoices can be imported again however often.
rem
rem  Double-click it here, in the scripts folder. Close the usual AP Coder window first (one AP Coder
rem  runs at a time). To start the demo afresh, close this window and delete the APCoder-demo folder;
rem  your own data folder is never touched. The run sheet: docs\DEMO_RUN_SHEET.md.
rem ===========================================================================================
setlocal
set "AP_PRIVATE_DIR=%USERPROFILE%\APCoder-demo"
rem  The per-user settings (the reviewer name, the installs) as a copy for the demo, kept beside the real
rem  file: a name saved in Settings during the demo never renames your own AP Coder (B-1505).
if not exist "%USERPROFILE%\.ap_coder\demo_settings.json" if exist "%USERPROFILE%\.ap_coder\settings.json" copy /y "%USERPROFILE%\.ap_coder\settings.json" "%USERPROFILE%\.ap_coder\demo_settings.json" >nul
set "AP_USER_SETTINGS=%USERPROFILE%\.ap_coder\demo_settings.json"
call "%~dp0..\APProcessor.bat" %*
exit /b %errorlevel%
