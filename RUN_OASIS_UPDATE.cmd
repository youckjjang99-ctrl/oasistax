@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title OASIS CRM v3.4.1 Update

echo ==========================================================
echo  OASIS CRM v3.4.1 UPDATE
echo ==========================================================
echo.

set "PYEXE="

rem 1) Python Launcher
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 --version >nul 2>&1
    if !errorlevel!==0 set "PYEXE=py -3"
)

rem 2) python command
if not defined PYEXE (
    where python >nul 2>&1
    if !errorlevel!==0 (
        python --version >nul 2>&1
        if !errorlevel!==0 set "PYEXE=python"
    )
)

rem 3) Common per-user Python install paths
if not defined PYEXE (
    for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
        if exist "%%~fD\python.exe" set "PYEXE=%%~fD\python.exe"
    )
)

rem 4) Common Program Files paths
if not defined PYEXE (
    for /d %%D in ("%ProgramFiles%\Python*") do (
        if exist "%%~fD\python.exe" set "PYEXE=%%~fD\python.exe"
    )
)
if not defined PYEXE (
    for /d %%D in ("%ProgramFiles(x86)%\Python*") do (
        if exist "%%~fD\python.exe" set "PYEXE=%%~fD\python.exe"
    )
)

if not defined PYEXE (
    echo [ERROR] Python was not found automatically.
    echo.
    echo Open the VS Code terminal in this folder and run:
    echo     py -3 update_v341.py
    echo or:
    echo     python update_v341.py
    echo.
    pause
    exit /b 1
)

echo Python command: !PYEXE!
echo Starting update...
echo.

if "!PYEXE!"=="py -3" (
    py -3 update_v341.py
) else if "!PYEXE!"=="python" (
    python update_v341.py
) else (
    "!PYEXE!" update_v341.py
)

set "EXITCODE=!errorlevel!"
echo.
if not "!EXITCODE!"=="0" (
    echo [FAILED] Update exited with code !EXITCODE!.
) else (
    echo [SUCCESS] OASIS CRM v3.4.1 update completed.
)
echo.
pause
exit /b !EXITCODE!
