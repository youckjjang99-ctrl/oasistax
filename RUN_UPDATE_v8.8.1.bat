@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.8.1 PRIORITY ENGINE CORRECTION
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
echo Upload the modified files to GitHub and wait for Railway.
pause
