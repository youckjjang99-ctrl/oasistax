@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo OASIS v8.5.5 PATCH
echo PROJECT_ROOT=%CD%
echo Existing patch files will be preserved.
echo ============================================

if not exist "stock_valuation.py" (
    echo UPDATE_FAILED: stock_valuation.py was not found in this folder.
    echo Extract this ZIP directly into the policy-fund automation project folder.
    echo CURRENT_FOLDER=%CD%
    pause
    exit /b 1
)

if not exist "APPLY_UPDATE.py" (
    echo UPDATE_FAILED: APPLY_UPDATE.py was not found.
    pause
    exit /b 1
)

python "APPLY_UPDATE.py"

if errorlevel 1 (
    echo.
    echo UPDATE FAILED.
    echo No GitHub upload should be performed.
    pause
    exit /b 1
)

echo.
echo UPDATE COMPLETED.
echo You may now run the commands in GITHUB_UPLOAD_COMMANDS.txt.
pause
