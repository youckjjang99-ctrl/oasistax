@echo off
setlocal
cd /d "%~dp0"
echo [OASIS] Starting update v9.4.2...

if not exist "APPLY_UPDATE_v9.4.2.py" (
  echo [ERROR] APPLY_UPDATE_v9.4.2.py not found.
  pause
  exit /b 1
)
if not exist "payload\employee_status.py" (
  echo [ERROR] payload files not found.
  pause
  exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import msoffcrypto" >nul 2>nul
  if errorlevel 1 (
    echo [OASIS] Installing encrypted Excel support...
    py -3 -m pip install msoffcrypto-tool==6.0.0
    if errorlevel 1 (
      echo [ERROR] Failed to install msoffcrypto-tool.
      pause
      exit /b 1
    )
  )
  py -3 "%CD%\APPLY_UPDATE_v9.4.2.py"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found.
    pause
    exit /b 1
  )
  python -c "import msoffcrypto" >nul 2>nul
  if errorlevel 1 (
    echo [OASIS] Installing encrypted Excel support...
    python -m pip install msoffcrypto-tool==6.0.0
    if errorlevel 1 (
      echo [ERROR] Failed to install msoffcrypto-tool.
      pause
      exit /b 1
    )
  )
  python "%CD%\APPLY_UPDATE_v9.4.2.py"
)

if errorlevel 1 (
  echo [ERROR] Update failed. Existing files were rolled back.
  pause
  exit /b 1
)

echo [OK] OASIS v9.4.2 update completed.
echo [NEXT] Test employee upload, then push to GitHub for Railway deployment.
pause
endlocal
