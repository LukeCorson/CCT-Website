@echo off
setlocal
cd /d "%~dp0"
set "CCT_DATA_DIR=%~dp0data"
if exist "recovery\runtime\Scripts\python.exe" (
  "recovery\runtime\Scripts\python.exe" -m cct.web
) else (
  py -3 -m cct.web
)
if errorlevel 1 pause
