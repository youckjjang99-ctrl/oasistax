@echo off
setlocal
cd /d "%~dp0"
echo =====================================================
echo OASIS v8.5.1 SUPABASE USER APPROVAL STORAGE UPDATE
echo =====================================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 update_v8.5.1.py
) else (
  python update_v8.5.1.py
)
if not %errorlevel%==0 (
  echo UPDATE FAILED OR ROLLED BACK
  pause
  exit /b 1
)
endlocal
