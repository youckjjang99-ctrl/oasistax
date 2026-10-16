@echo off
setlocal
cd /d "%~dp0"
set "OASIS_VERIFY_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "OASIS_VERIFY_PY=py -3"
echo OASIS v9.14.8 - offline navigation and data-safety verification
echo This script does not upload, deploy, migrate, or edit customer records.
%OASIS_VERIFY_PY% -m pytest -p no:cacheprovider tests/test_enterprise_navigation_performance.py tests/test_enterprise_submenu_performance.py tests/test_crm_sync_name_batch.py tests/test_crm_performance.py tests/test_crm_cross_device_sync.py tests/test_crm_concurrent_storage.py tests/test_crm_versioned_cloud.py tests/test_auth_security.py tests/test_snapshot_read_cache.py tests/test_sales_read_cache.py tests/test_work_inbox_cache.py tests/test_work_inbox.py tests/test_navigation_visibility.py tests/test_rerun_reductions.py tests/test_enterprise_consulting_read_only_tabs.py tests/test_enterprise_data_linking.py tests/test_enterprise_information_registration.py tests/test_copilot_workflow_ui.py tests/test_upload_security.py tests/test_web_asset_performance.py tests/test_cretop_registration_parser.py tests/test_cretop_financial_signs.py tests/test_cretop_reordered_headers.py tests/test_cretop_registration_ui.py tests/test_cretop_company_field_persistence.py tests/test_cretop_financial_snapshot_preservation.py tests/test_stock_valuation_cretop_mapping.py tests/test_report_artifact_cache.py -q
if errorlevel 1 (
    echo Verification failed. Check Python and test dependencies, then review the errors.
    exit /b 1
)
echo Verification passed. Production deployment requires separate approval.
exit /b 0
