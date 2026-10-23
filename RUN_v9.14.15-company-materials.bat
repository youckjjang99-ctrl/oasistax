@echo off
setlocal
cd /d "%~dp0"
set "OASIS_VERIFY_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "OASIS_VERIFY_PY=py -3"
echo OASIS v9.14.15 - company material isolation and preservation checks
echo Synthetic tests only. No production migration, deployment, or outbound calls.
%OASIS_VERIFY_PY% -m pytest -q -p no:cacheprovider tests/test_registry_registration_context.py tests/test_registry_snapshot_safety.py tests/test_stock_company_state.py tests/test_stock_history_preservation.py tests/test_stock_history_company_query.py tests/test_company_materials_library.py tests/test_enterprise_document_preservation.py tests/test_company_material_retention.py tests/test_consultation_material_isolation.py tests/test_financial_material_preservation.py tests/test_customer_history_material_scope.py tests/test_registry_upload_progress.py tests/test_stock_valuation_registry_linking.py tests/test_stock_valuation_ui_layout.py tests/test_stock_valuation_goodwill.py tests/test_stock_valuation_goodwill_ui.py tests/test_stock_valuation_cretop_mapping.py tests/test_cretop_financial_snapshot_preservation.py tests/test_stock_tax_adjustment_ui.py tests/test_tax_adjustment_valuation.py tests/test_enterprise_information_registration.py tests/test_cretop_registration_ui.py tests/test_enterprise_document_batch.py tests/test_upload_workflow_feedback.py tests/test_upload_progress.py tests/test_customer_asset_storage.py tests/test_consultation_audio_owner_scope.py tests/test_data_safety_storage.py tests/test_data_safety_admin.py tests/test_privacy_guard.py tests/test_enterprise_navigation_performance.py tests/test_enterprise_submenu_performance.py tests/test_upload_security.py tests/test_snapshot_read_cache.py --basetemp=tmp/test-company-materials-v91415
if errorlevel 1 (
    echo Verification failed. Do not deploy until the failures have been reviewed.
    exit /b 1
)
echo Verification passed. Production deployment requires separate approval.
exit /b 0
