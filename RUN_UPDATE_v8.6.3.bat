@echo off
setlocal
cd /d "%~dp0"

echo ======================================================
echo OASIS v8.6.3 FFMPEG AND EMPLOYEE JPG PATCH
echo PROJECT_ROOT=%CD%
echo Existing customer, employee, audio and journal data are preserved.
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
echo IMPORTANT: GitHub push and a NEW Railway build are required.
pause
