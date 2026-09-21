@echo off
setlocal
cd /d "%~dp0"
echo OASIS v9.13.3 local verification only. No deployment or database changes.
python -m pytest tests -q
if errorlevel 1 goto failed
python tools/privacy_guard.py --working-tree
if errorlevel 1 goto failed
git diff --check
if errorlevel 1 goto failed
echo Verification completed. Production deployment requires separate approval.
pause
exit /b 0
:failed
echo Verification failed. No deployment was performed.
pause
exit /b 1
