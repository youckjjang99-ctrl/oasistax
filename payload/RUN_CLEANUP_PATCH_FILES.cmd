@echo off
setlocal
cd /d "%~dp0"
echo ================================================
echo OASIS LEGACY PATCH CLEANUP
echo ================================================
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 cleanup_legacy_patches.py
) else (
  python cleanup_legacy_patches.py
)
endlocal
