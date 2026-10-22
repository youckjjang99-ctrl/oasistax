@echo off
setlocal
cd /d "%~dp0"
echo OASIS v9.14.11 - offline compatibility verification only
echo No credential changes, database writes, Git push, or deployment.
python --version >nul 2>nul
if errorlevel 1 goto missing_python
python -m compileall -q auth.py cloud_db.py consultation_audio_storage.py prospect_db_repository.py runtime_error_log.py supabase_api_config.py
if errorlevel 1 goto failed
python -m pytest -q tests/test_auth_security.py tests/test_auth_session_key_isolation.py tests/test_supabase_key_rotation_compat.py tests/test_runtime_error_log.py tests/test_stage1_ui_redaction.py tests/test_key_rotation_data_preservation.py tests/test_customer_asset_storage.py tests/test_consultation_audio_owner_scope.py tests/test_prospect_outreach_repository.py tests/test_sales_outreach_repository.py tests/test_claim_sales_application.py tests/test_sync_outbox.py tests/test_company_sales_assignments.py tests/test_crm_versioned_cloud.py
if errorlevel 1 goto failed
python -m pytest -q tests/test_scheduled_employment_phone_pipeline.py tests/test_scheduled_license_phone_enrichment.py tests/test_upload_progress.py tests/test_registry_upload_progress.py tests/test_upload_workflow_feedback.py
if errorlevel 1 goto failed
python -m pytest -q tests/test_voice_calling.py tests/test_voice_calling_gateway.py tests/test_voice_calling_preflight.py tests/test_voice_calling_provider.py tests/test_voice_calling_clawops.py tests/test_voice_calling_clawops_gateway.py tests/test_voice_calling_worker.py
if errorlevel 1 goto failed
echo PASS. Set a dedicated APP_SESSION_SECRET before deployment.
echo Verify every service with replacement API keys before revoking old keys.
pause
exit /b 0
:missing_python
echo Python not found. Activate the existing project environment first.
pause
exit /b 1
:failed
echo Verification failed. Do not rotate credentials or deploy.
pause
exit /b 1
