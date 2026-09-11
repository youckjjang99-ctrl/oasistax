@echo off
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo OASIS CRM v9.13.2 - local verification only
echo This script does not modify customer data, push Git, or deploy.
where python >nul 2>nul
if errorlevel 1 goto failed
python -m pytest tests/test_cretop_registration_parser.py tests/test_cretop_financial_signs.py tests/test_cretop_identity_verification.py tests/test_cretop_ocr_tables.py tests/test_cretop_financial_snapshot_preservation.py tests/test_cretop_company_field_persistence.py tests/test_cretop_registration_ui.py -q --tb=short
if errorlevel 1 goto failed
python tools/privacy_guard.py --working-tree
if errorlevel 1 goto failed
git diff --check
if errorlevel 1 goto failed
echo Verification passed. Read docs\cretop-financial-identity-v9.13.2.md.
pause
exit /b 0
:failed
echo Verification failed. Do not deploy. Use the project's existing Python environment.
pause
exit /b 1
