@echo off
setlocal
cd /d "%~dp0"
rem Local CRM only. This launcher never migrates a DB or starts a dialer.
set "OASIS_VOICE_CALLS_ENABLED=false"
python -c "import streamlit, fastapi, httpx, twilio, websockets" >nul 2>&1
if errorlevel 1 (
  echo Install the pinned dependencies first: python -m pip install -r requirements.txt
  pause
  exit /b 1
)
python -m streamlit run app.py
endlocal
