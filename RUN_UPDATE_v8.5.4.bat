@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0\.."
echo ============================================
echo OASIS v8.5.4 누적 안전 패치
echo 기존 패치파일은 삭제하지 않습니다.
echo ============================================
python "%~dp0APPLY_UPDATE.py"
if errorlevel 1 (
  echo.
  echo 업데이트에 실패했습니다. 위 오류와 백업 경로를 확인해주세요.
  pause
  exit /b 1
)
echo.
echo 업데이트가 완료되었습니다.
pause
