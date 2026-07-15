@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo OASIS v7.3.0 UPDATE
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 update_v7.3.0.py
) else (
  python update_v7.3.0.py
)
if not %errorlevel%==0 (
  echo UPDATE FAILED
  pause
  exit /b 1
)
endlocal
