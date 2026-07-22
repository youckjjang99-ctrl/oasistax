@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.8.0 COMPREHENSIVE FINANCIAL DIAGNOSIS
echo PROJECT_ROOT=%CD%
echo Existing customer, financial and consulting data are preserved.
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
