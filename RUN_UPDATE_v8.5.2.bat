@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 update_v8.5.2.py
) else (
  python update_v8.5.2.py
)
echo.
pause
