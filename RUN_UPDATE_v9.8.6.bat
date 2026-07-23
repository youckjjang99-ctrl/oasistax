@echo off
setlocal
cd /d "%~dp0"
python APPLY_UPDATE_v9.8.6.py
if errorlevel 1 (
  echo UPDATE FAILED
  pause
  exit /b 1
)
echo UPDATE COMPLETED
pause
endlocal
