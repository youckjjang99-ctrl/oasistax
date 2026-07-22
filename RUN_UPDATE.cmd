@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo OASIS v8.4.0 AI COPILOT UI AND PDF REPORT UPDATE
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 update_engine.py
) else (
  python update_engine.py
)
if not %errorlevel%==0 (
  echo UPDATE FAILED OR ROLLED BACK
  pause
  exit /b 1
)
endlocal
