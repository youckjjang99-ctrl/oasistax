@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.6.1 POLICY HISTORY AND PDF REDESIGN
echo PROJECT_ROOT=%CD%
echo Existing data and previous patch files are preserved.
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
echo Verify policy history, Copilot policy UI and PDF download.
pause
