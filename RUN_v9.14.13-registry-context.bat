@echo off
setlocal
cd /d "%~dp0"
set "OASIS_VERIFY_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "OASIS_VERIFY_PY=py -3"
echo OASIS v9.14.13 - registration registry upload verification
echo This script does not deploy, migrate, call providers, or modify customer data.
%OASIS_VERIFY_PY% -m pytest -q -p no:cacheprovider tests/test_registry_registration_context.py tests/test_registry_upload_progress.py tests/test_stock_valuation_registry_linking.py tests/test_stock_valuation_ui_layout.py tests/test_enterprise_information_registration.py tests/test_cretop_registration_ui.py tests/test_stock_tax_adjustment_ui.py tests/test_tax_adjustment_valuation.py tests/test_enterprise_document_batch.py tests/test_upload_workflow_feedback.py
if errorlevel 1 (
    echo Verification failed. Review the errors and installed test dependencies.
    exit /b 1
)
echo Verification passed. Production deployment requires separate approval.
exit /b 0
