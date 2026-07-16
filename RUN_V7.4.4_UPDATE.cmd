@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo OASIS v7.4.4 SAFE UPDATE
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (py -3 update_v7.4.4.py) else (python update_v7.4.4.py)
if not %errorlevel%==0 (
 echo UPDATE FAILED OR ROLLED BACK
 pause
 exit /b 1
)
endlocal
