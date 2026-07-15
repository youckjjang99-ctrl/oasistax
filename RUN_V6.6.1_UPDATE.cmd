@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo OASIS v6.6.1 UPDATE
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 update_v6.6.1.py
) else (
  python update_v6.6.1.py
)
if not %errorlevel%==0 (
  echo UPDATE FAILED
  pause
  exit /b 1
)
endlocal
