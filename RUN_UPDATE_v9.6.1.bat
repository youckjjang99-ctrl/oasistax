@echo off
setlocal
cd /d "%~dp0"
echo [OASIS] Starting update v9.6.1...

if not exist "APPLY_UPDATE_v9.6.1.py" (
  echo [ERROR] APPLY_UPDATE_v9.6.1.py not found.
  pause
  exit /b 1
)
if not exist "payload\public_data_api.py" (
  echo [ERROR] payload files not found.
  pause
  exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%CD%\APPLY_UPDATE_v9.6.1.py"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found.
    pause
    exit /b 1
  )
  python "%CD%\APPLY_UPDATE_v9.6.1.py"
)

if errorlevel 1 (
  echo [ERROR] Update failed. Existing files were rolled back.
  pause
  exit /b 1
)

echo [OK] OASIS v9.6.1 update completed.
echo [NEXT] Push to GitHub and test with 10 businesses first.
pause
endlocal

