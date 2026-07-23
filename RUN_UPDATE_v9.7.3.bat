@echo off
setlocal EnableExtensions
python APPLY_UPDATE_v9.7.3.py
set "UPDATE_EXIT=%ERRORLEVEL%"
if not "%UPDATE_EXIT%"=="0" goto :failed
echo.
echo OASIS CRM v9.7.3 update completed.
pause
exit /b 0

:failed
echo.
echo Update failed. Check the error above and the backup folder.
pause
exit /b %UPDATE_EXIT%
