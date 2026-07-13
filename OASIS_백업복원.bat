@echo off
chcp 65001 > nul
cd /d "%~dp0"
python restore_oasis_backup.py
