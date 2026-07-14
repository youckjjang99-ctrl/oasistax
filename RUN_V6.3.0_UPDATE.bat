@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==============================================
echo OASIS 정책자금자동화 v6.3.0 자동 업데이트
echo ==============================================

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 update_v6.3.0.py
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python update_v6.3.0.py
    ) else (
        echo [실패] Python을 찾을 수 없습니다.
        pause
        exit /b 1
    )
)

endlocal
