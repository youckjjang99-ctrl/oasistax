@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.6.0 COPILOT CONTINUITY POLICY PDF FIX
echo PROJECT_ROOT=%CD%
echo Existing customer, CRM, policy, audio and valuation data are preserved.
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
echo Verify the three features before uploading to GitHub.
pause
