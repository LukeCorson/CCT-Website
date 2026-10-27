@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
if errorlevel 1 (
  echo Setup failed. Read the message above for the next step.
  pause
  exit /b 1
)
echo Setup complete. Open "Start CCT Control.cmd" to launch the app.
pause
