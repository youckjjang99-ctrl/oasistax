@echo off
setlocal
cd /d "%~dp0"
echo [OASIS] Starting update v9.3.3...
if not exist "APPLY_UPDATE_v9.3.3.py" (
  echo [ERROR] APPLY_UPDATE_v9.3.3.py not found.
  pause
  exit /b 1
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%CD%\APPLY_UPDATE_v9.3.3.py"
) else (
  python "%CD%\APPLY_UPDATE_v9.3.3.py"
)
if errorlevel 1 (
  echo [ERROR] Update failed.
  pause
  exit /b 1
)
echo [OK] OASIS v9.3.3 update completed.
pause
endlocal
