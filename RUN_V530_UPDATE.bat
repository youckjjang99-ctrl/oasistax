@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 update_v530.py
    exit /b %errorlevel%
)

where python >nul 2>&1
if %errorlevel%==0 (
    python update_v530.py
    exit /b %errorlevel%
)

echo [ERROR] Python was not found.
pause
