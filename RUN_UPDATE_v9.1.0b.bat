@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ======================================================
echo OASIS v9.1.0b FINANCIAL ANOMALY
python APPLY_UPDATE_v9.1.0b.py
if errorlevel 1 (
  echo.
  echo UPDATE FAILED OR ROLLED BACK.
  echo Do not upload to GitHub.
  pause
  exit /b 1
)
echo.
echo UPDATE COMPLETE.
pause
