@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0app-0.5.0\CCTControl.exe" (
  start "" /D "%~dp0app-0.5.0" "%~dp0app-0.5.0\CCTControl.exe"
  exit /b 0
)
if exist "%~dp0app\CCTControl.exe" (
  start "" /D "%~dp0app" "%~dp0app\CCTControl.exe"
  exit /b 0
)
if not exist ".venv\Scripts\pythonw.exe" (
  echo CCT Control needs its one-time setup.
  echo Run "Setup CCT Control.cmd" in this folder, then open this launcher again.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m cct.app
