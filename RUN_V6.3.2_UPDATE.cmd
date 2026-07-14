@echo off
setlocal
cd /d "%~dp0"

echo ================================================
echo OASIS v6.3.2 UPDATE
echo ================================================

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 update_v6.3.2.py
) else (
    python update_v6.3.2.py
)

if errorlevel 1 (
    echo.
    echo UPDATE FAILED
    echo Check the messages above.
    pause
    exit /b 1
)

echo.
echo UPDATE COMPLETE
echo Run supabase_v632_upgrade.sql once in Supabase SQL Editor.
echo Restart Streamlit after GitHub deployment finishes.
pause
