@echo off
setlocal
cd /d "%~dp0"
set "OASIS_VERIFY_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "OASIS_VERIFY_PY=py -3"
echo OASIS v9.14.7 - offline parser and registration verification
echo This script does not upload, deploy, migrate, or edit customer records.
%OASIS_VERIFY_PY% -m pytest -p no:cacheprovider tests/test_cretop_registration_parser.py tests/test_cretop_financial_signs.py tests/test_cretop_reordered_headers.py tests/test_cretop_registration_ui.py tests/test_cretop_financial_snapshot_preservation.py -q
if errorlevel 1 (
    echo Verification failed. Check Python and test dependencies, then review the errors.
    exit /b 1
)
echo Verification passed. Production deployment requires separate approval.
exit /b 0
