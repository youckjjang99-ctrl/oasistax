@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo OASIS UPDATE
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 update.py
) else (
  python update.py
)
if not %errorlevel%==0 (
  echo UPDATE FAILED OR ROLLED BACK
  pause
  exit /b 1
)
endlocal
