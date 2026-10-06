@echo off
setlocal
cd /d "%~dp0"
rem Preview only: no database migration, purchases or customer dialing.
set "OASIS_VOICE_CALLS_ENABLED=false"
set "OASIS_VOICE_PROVIDER=clawops"
set "OASIS_VOICE_CLAWOPS_BILLING_CONFIRMED=false"
set "OASIS_VOICE_CLAWOPS_LIVE_VERIFIED=false"
python -c "import streamlit, fastapi, httpx, twilio, websockets" >nul 2>&1
if errorlevel 1 (
  echo Install the pinned dependencies first: python -m pip install -r requirements.txt
  pause
  exit /b 1
)
python -m streamlit run app.py
endlocal
