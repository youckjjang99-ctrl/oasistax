@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title OASIS v3.5.1 Hotfix

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
    echo [ERROR] Python was not found.
    echo Open VS Code terminal and run:
    echo     py -3 verify_v351.py
    pause
    exit /b 1
)

if "!PYEXE!"=="py -3" (
    py -3 verify_v351.py
) else if "!PYEXE!"=="python" (
    python verify_v351.py
) else (
    "!PYEXE!" verify_v351.py
)
