@echo off
setlocal
cd /d "%~dp0"

echo [OASIS] Starting update v9.3.1...

if not exist "APPLY_UPDATE_v9.3.1.py" (
    echo [ERROR] APPLY_UPDATE_v9.3.1.py was not found in:
    echo %CD%
    echo.
    pause
    exit /b 1
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%CD%\APPLY_UPDATE_v9.3.1.py"
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found.
        echo Install Python or add it to PATH.
        echo.
        pause
        exit /b 1
    )
    python "%CD%\APPLY_UPDATE_v9.3.1.py"
)

if errorlevel 1 (
    echo.
    echo [ERROR] Update failed.
    echo Check the error message and rollback path above.
    pause
    exit /b 1
)

echo.
echo [OK] OASIS v9.3.1 update completed.
pause
endlocal
