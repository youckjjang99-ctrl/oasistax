@echo off
setlocal
cd /d "%~dp0"
call RUN_v9.14.16-tax-years-files.bat
if errorlevel 1 exit /b 1
set "OASIS_VERIFY_PY=python"
where py >nul 2>nul
if not errorlevel 1 set "OASIS_VERIFY_PY=py -3"
echo OASIS v9.14.17 - original registry and capital-history valuation checks
echo Synthetic tests only. No production writes, migrations, publication, or deployment.
%OASIS_VERIFY_PY% -B -m pytest -q -p no:cacheprovider tests/test_registry_capital_history.py tests/test_stock_capital.py tests/test_registry_reanalysis.py tests/test_stock_capital_integration.py --basetemp=tmp/v17
if errorlevel 1 exit /b 1
echo Verification passed. Publishing and deployment require separate approval.
exit /b 0
