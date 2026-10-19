@echo off
setlocal
cd /d "%~dp0"
set "OASIS_VERIFY_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "OASIS_VERIFY_PY=py -3"
echo OASIS v9.14.9 - offline goodwill and stock valuation verification
echo This script does not upload, deploy, migrate, or edit customer records.
%OASIS_VERIFY_PY% -m pytest -p no:cacheprovider tests/test_stock_valuation_goodwill.py tests/test_stock_valuation_goodwill_ui.py tests/test_stock_valuation_cretop_mapping.py tests/test_stock_valuation_registry_linking.py tests/test_stock_valuation_ui_layout.py tests/test_enterprise_navigation_performance.py tests/test_enterprise_submenu_performance.py tests/test_enterprise_data_linking.py tests/test_cretop_financial_snapshot_preservation.py tests/test_snapshot_read_cache.py tests/test_auth_security.py -q
if errorlevel 1 (
    echo Verification failed. Check Python and test dependencies, then review the errors.
    exit /b 1
)
echo Verification passed. Production deployment requires separate approval.
exit /b 0
