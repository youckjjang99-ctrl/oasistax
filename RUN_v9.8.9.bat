@echo off
setlocal
cd /d "%~dp0"
python APPLY_UPDATE_v9.8.9.py
if errorlevel 1 (
  echo UPDATE VALIDATION FAILED
  pause
  exit /b 1
)
echo UPDATE VALIDATION COMPLETED
echo PRODUCTION SQL ORDER:
echo 1. Apply supabase_v1032_company_sales_assignments.sql
echo 2. Then apply supabase_v1032_company_sales_assignments_rls.sql
echo This validator does not apply either SQL file or deploy Railway automatically.
pause
endlocal
