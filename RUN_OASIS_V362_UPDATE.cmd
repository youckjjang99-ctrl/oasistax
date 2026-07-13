@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "PYEXE="
where py >nul 2>&1
if %errorlevel%==0 set "PYEXE=py -3"
if not defined PYEXE (
  where python >nul 2>&1
  if !errorlevel!==0 set "PYEXE=python"
)
if not defined PYEXE (
  echo [ERROR] Python was not found.
  pause
  exit /b 1
)
if "!PYEXE!"=="py -3" (
  py -3 update_v362.py
) else (
  python update_v362.py
)
