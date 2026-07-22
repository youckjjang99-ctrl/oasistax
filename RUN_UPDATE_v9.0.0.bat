@echo off
setlocal
cd /d "%~dp0"
echo ======================================================
echo OASIS v9.0.0 AI CFO FOUNDATION
echo PROJECT_ROOT=%CD%
echo Existing DB, uploads, Supabase and user data are preserved.
echo ======================================================
python "APPLY_UPDATE.py"
if errorlevel 1 (
 echo.
 echo UPDATE FAILED OR ROLLED BACK.
 echo Do not upload to GitHub.
 pause
 exit /b 1
)
echo.
echo UPDATE COMPLETED.
echo Check UPDATE_OK and VERSION=v9.0.0 above.
pause
