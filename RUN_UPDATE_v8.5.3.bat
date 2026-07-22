@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 APPLY_UPDATE.py
) else (
  python APPLY_UPDATE.py
)
if errorlevel 1 (
  echo UPDATE FAILED.
  pause
  exit /b 1
)
echo UPDATE COMPLETED: v8.5.3
echo Old target files were backed up, deleted, and replaced.
echo Obsolete updater files were removed.
pause
