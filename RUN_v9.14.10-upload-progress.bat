@echo off
setlocal
cd /d "%~dp0"
echo OASIS v9.14.10 - local verification only
echo No installs, customer deletion, database changes, Git push, or deployment.
python --version >nul 2>nul
if errorlevel 1 goto missing_python
python -m compileall -q app.py articles_review.py consultation_journal.py employee_status.py enterprise_documents.py enterprise_registration_ui.py registry_runner.py stock_valuation.py ui.py upload_progress.py utils.py
if errorlevel 1 goto failed
python -m pytest -q tests/test_upload_progress.py tests/test_registry_upload_progress.py tests/test_upload_workflow_feedback.py tests/test_upload_security.py tests/test_cretop_registration_ui.py tests/test_stock_valuation_registry_linking.py tests/test_stock_valuation_ui_layout.py tests/test_stock_valuation_goodwill.py tests/test_stock_valuation_goodwill_ui.py tests/test_enterprise_information_registration.py tests/test_enterprise_consulting_read_only_tabs.py tests/test_enterprise_submenu_performance.py tests/test_enterprise_navigation_performance.py tests/test_navigation_visibility.py tests/test_representative_mobile_and_ksic.py tests/test_customer_asset_storage.py tests/test_consultation_audio_owner_scope.py
if errorlevel 1 goto failed
echo PASS. Review PC and mobile UI before approving deployment.
pause
exit /b 0
:missing_python
echo Python not found. Activate the existing project environment first.
pause
exit /b 1
:failed
echo Verification failed. Do not deploy. Review the error above.
pause
exit /b 1
