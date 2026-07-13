@echo off
chcp 65001 > nul
cd /d "%~dp0"

where python > nul 2>&1
if %errorlevel% neq 0 (
    echo Python을 찾지 못했습니다.
    echo VS Code 터미널에서 python update_v340.py 를 실행해주세요.
    pause
    exit /b 1
)

python update_v340.py
