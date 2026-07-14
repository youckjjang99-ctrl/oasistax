@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo OASIS v6.3.3 UPDATE
echo ================================================

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 update_v6.3.3.py
) else (
    python update_v6.3.3.py
)

if errorlevel 1 (
    echo.
    echo UPDATE FAILED
    echo Check the messages above.
    pause
    exit /b 1
)

echo.
echo UPDATE COMPLETE - v6.3.3
echo 1. Run supabase_v633_upgrade.sql in Supabase SQL Editor.
echo 2. Run the Git commands in README_v6.3.3.
echo.
pause
