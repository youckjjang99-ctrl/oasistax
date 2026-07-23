@echo off
setlocal
cd /d "%~dp0"
echo [OASIS] Starting update v9.7.1...

if not exist "APPLY_UPDATE_v9.7.1.py" (
  echo [ERROR] APPLY_UPDATE_v9.7.1.py not found.
  pause
  exit /b 1
)
if not exist "payload\sales_intelligence.py" (
  echo [ERROR] payload files not found.
  pause
  exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%CD%\APPLY_UPDATE_v9.7.1.py"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found.
    pause
    exit /b 1
  )
  python "%CD%\APPLY_UPDATE_v9.7.1.py"
)

if errorlevel 1 (
  echo [ERROR] Update failed. Existing files were rolled back.
  pause
  exit /b 1
)

echo [OK] OASIS v9.7.1 update completed.
echo [NEXT] Register KIPRIS_API_KEY in Railway for patent filtering.
echo [NEXT] No new Supabase SQL is required for v9.7.1.
echo [NEXT] Push to GitHub and verify the Railway deployment.
pause
endlocal
