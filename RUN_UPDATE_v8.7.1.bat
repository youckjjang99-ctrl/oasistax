@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.7.1 EMPLOYEE SUPABASE LOAD
echo PROJECT_ROOT=%CD%
echo No employee data recovery or deletion will be performed.
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
echo Upload to GitHub and wait for Railway redeployment.
pause
