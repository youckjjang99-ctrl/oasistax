@echo off
setlocal
cd /d "%~dp0"
echo OASIS v9.12.1 - local verification only
echo No database changes, package installs, Git push, or deployment will run.
python --version >nul 2>nul
if errorlevel 1 goto missing_python
python -m compileall -q app.py cretop_runner.py cretop_worker.py enterprise_registration_ui.py enterprise_documents.py utils.py matching_preferences.py
if errorlevel 1 goto failed
python -m pytest -q tests/test_cretop_registration_ui.py tests/test_cretop_registration_parser.py tests/test_enterprise_information_registration.py tests/test_customer_information_integration.py tests/test_cloud_sync_business_no_linking.py tests/test_enterprise_data_linking.py tests/test_stock_valuation_cretop_mapping.py tests/test_upload_security.py tests/test_crm_performance.py tests/test_representative_pdf.py tests/test_company_sales_assignment_integration.py tests/test_customer_lifecycle.py tests/test_enterprise_consulting_read_only_tabs.py
if errorlevel 1 goto failed
python tools/privacy_guard.py --paths enterprise_registration_ui.py cretop_worker.py cretop_runner.py tests/test_cretop_registration_ui.py tests/test_cretop_registration_parser.py docs/registration-upload-v9.12.1.md RUN_v9.12.1-registration-upload.bat
if errorlevel 1 goto failed
echo PASS. Review the PC/mobile UI before approving deployment.
pause
exit /b 0
:missing_python
echo Python was not found. Use the existing project Python environment.
pause
exit /b 1
:failed
echo Verification failed. Do not deploy. Review the error above.
pause
exit /b 1
