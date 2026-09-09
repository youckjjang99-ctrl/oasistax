@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo OASIS CRM v9.13.0 - local verification only
echo This script does not migrate databases, upload data, push Git, or deploy.
where python >nul 2>nul
if errorlevel 1 goto missing_python
python -m pytest tests -q --tb=short
if errorlevel 1 goto failed
python tools/privacy_guard.py --working-tree
if errorlevel 1 goto failed
git diff --check
if errorlevel 1 goto failed
echo Verification passed. Read docs\crm-consistency-v9.13.0.md before deployment.
echo Production migrations and deployment require separate approval.
pause
exit /b 0
:missing_python
echo Python is not available. Use the project's existing Python environment.
pause
exit /b 1
:failed
echo Verification failed. Do not apply production migrations or deploy.
pause
exit /b 1
