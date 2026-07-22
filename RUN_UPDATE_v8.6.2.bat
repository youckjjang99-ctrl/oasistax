@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.6.2 COPILOT FREE COMPANY SELECTION
echo PROJECT_ROOT=%CD%
echo Existing customer and consulting data are preserved.
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
echo Test enterprise handoff and free company selection.
pause
