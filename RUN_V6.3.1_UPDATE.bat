@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ================================================
echo OASIS 정책자금자동화 v6.3.1 업데이트
echo ================================================

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 update_v6.3.1.py
) else (
    python update_v6.3.1.py
)

if errorlevel 1 (
    echo.
    echo [실패] 업데이트 로그를 확인해주세요.
    pause
    exit /b 1
)

echo.
echo [성공] v6.3.1 업데이트가 완료되었습니다.
echo Supabase SQL Editor에서 supabase_v631_upgrade.sql을 실행해주세요.
echo.
echo GitHub 반영 명령어:
echo git add .
echo git commit -m "v6.3.1 녹음파일 클라우드 캐시 복원 및 중복분석 방지"
echo git push origin main
pause
