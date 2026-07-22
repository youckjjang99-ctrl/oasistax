@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo OASIS v8.5.7 PERFORMANCE AND STOCK HISTORY
echo PROJECT_ROOT=%CD%
echo ============================================
if not exist "stock_valuation.py" (echo UPDATE_FAILED: stock_valuation.py not found.& pause & exit /b 1)
if not exist "cloud_sync.py" (echo UPDATE_FAILED: cloud_sync.py not found.& pause & exit /b 1)
python "APPLY_UPDATE.py"
if errorlevel 1 (echo UPDATE FAILED. Do not upload to GitHub.& pause & exit /b 1)
echo UPDATE COMPLETED.
echo Run GITHUB_UPLOAD_COMMANDS.txt
pause
