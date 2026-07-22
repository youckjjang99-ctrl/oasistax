@echo off
setlocal
cd /d "%~dp0"

echo =================================================
echo OASIS v8.5.8 AI COPILOT COMPANY CONTEXT PATCH
echo PROJECT_ROOT=%CD%
echo Existing CRM, audio, journals and valuation data are preserved.
echo =================================================

if not exist "consulting_copilot.py" (
    echo UPDATE_FAILED: consulting_copilot.py was not found.
    pause
    exit /b 1
)

if not exist "consultation_journal.py" (
    echo UPDATE_FAILED: consultation_journal.py was not found.
    pause
    exit /b 1
)

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
echo Run commands in GITHUB_UPLOAD_COMMANDS.txt.
pause
