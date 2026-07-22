@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo OASIS v8.5.6 STOCK PREMIUM AND PROGRESS PATCH
echo PROJECT_ROOT=%CD%
echo Existing patch files will be preserved.
echo ============================================

if not exist "stock_valuation.py" (
    echo UPDATE_FAILED: stock_valuation.py was not found in this folder.
    echo Extract this ZIP directly into the policy-fund automation project folder.
    pause
    exit /b 1
)

python "APPLY_UPDATE.py"

if errorlevel 1 (
    echo.
    echo UPDATE FAILED.
    echo Do not upload to GitHub.
    pause
    exit /b 1
)

echo.
echo UPDATE COMPLETED.
echo Run the commands in GITHUB_UPLOAD_COMMANDS.txt.
pause
