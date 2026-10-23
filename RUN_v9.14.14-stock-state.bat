@echo off
setlocal
cd /d "%~dp0"
set "OASIS_VERIFY_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "OASIS_VERIFY_PY=py -3"
echo OASIS v9.14.14 - registry and company-scoped valuation verification
echo Tests use synthetic data. This script does not deploy or migrate production.
%OASIS_VERIFY_PY% -m pytest -q -p no:cacheprovider tests/test_stock_company_state.py tests/test_stock_history_preservation.py tests/test_registry_snapshot_safety.py tests/test_registry_registration_context.py tests/test_registry_upload_progress.py tests/test_stock_valuation_registry_linking.py tests/test_stock_valuation_ui_layout.py tests/test_stock_valuation_goodwill.py tests/test_stock_valuation_goodwill_ui.py tests/test_stock_valuation_cretop_mapping.py tests/test_stock_tax_adjustment_ui.py tests/test_tax_adjustment_valuation.py tests/test_enterprise_information_registration.py tests/test_cretop_registration_ui.py tests/test_enterprise_document_batch.py tests/test_upload_workflow_feedback.py tests/test_upload_progress.py tests/test_customer_asset_storage.py tests/test_enterprise_navigation_performance.py tests/test_enterprise_submenu_performance.py tests/test_upload_security.py
if errorlevel 1 (
    echo Verification failed. Review the errors and installed test dependencies.
    exit /b 1
)
echo Verification passed. Production deployment requires separate approval.
exit /b 0
