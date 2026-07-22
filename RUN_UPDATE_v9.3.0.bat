@echo off
chcp 65001 >nul
cd /d "%~dp0"
python APPLY_UPDATE_v9.3.0.py
echo.
pause
