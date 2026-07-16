@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo OASIS DEPLOY PRECHECK
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (py -3 system_precheck.py) else (python system_precheck.py)
set EXIT_CODE=%errorlevel%
if not %EXIT_CODE%==0 (echo DEPLOY BLOCKED) else (echo DEPLOY READY)
pause
exit /b %EXIT_CODE%
