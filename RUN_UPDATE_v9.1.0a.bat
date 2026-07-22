@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================
python APPLY_UPDATE_v9.1.0a.py
if errorlevel 1 (
  echo.
  echo UPDATE FAILED OR ROLLED BACK.
  echo Do not upload to GitHub.
) else (
  echo.
  echo UPDATE COMPLETE.
)
pause
