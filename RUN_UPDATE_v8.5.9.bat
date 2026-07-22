@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.5.9 REGISTRY AND COPILOT INTEGRATION
echo PROJECT_ROOT=%CD%
echo Existing database, audio and consultation data are preserved.
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
echo Run GITHUB_UPLOAD_COMMANDS.txt after checking the app.
pause
