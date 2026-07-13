@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title OASIS CRM v3.5.0 Update

echo ==========================================================
echo  OASIS CRM v3.5.0 UPDATE
echo ==========================================================
echo.

set "PYEXE="

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 --version >nul 2>&1
    if !errorlevel!==0 set "PYEXE=py -3"
)

if not defined PYEXE (
    where python >nul 2>&1
    if !errorlevel!==0 (
        python --version >nul 2>&1
        if !errorlevel!==0 set "PYEXE=python"
    )
)

if not defined PYEXE (
    for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
        if exist "%%~fD\python.exe" set "PYEXE=%%~fD\python.exe"
    )
)

if not defined PYEXE (
    echo [ERROR] Python was not found automatically.
    echo Run this in VS Code terminal:
    echo     py -3 update_v350.py
    echo or:
    echo     python update_v350.py
    pause
    exit /b 1
)

if "!PYEXE!"=="py -3" (
    py -3 update_v350.py
) else if "!PYEXE!"=="python" (
    python update_v350.py
) else (
    "!PYEXE!" update_v350.py
)

set "EXITCODE=!errorlevel!"
echo.
if not "!EXITCODE!"=="0" (
    echo [FAILED] Update exited with code !EXITCODE!.
) else (
    echo [SUCCESS] OASIS CRM v3.5.0 update completed.
)
pause
exit /b !EXITCODE!
