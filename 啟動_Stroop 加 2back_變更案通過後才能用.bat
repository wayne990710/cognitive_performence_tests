@echo off
chcp 65001 > nul
cd /d "%~dp0"
python app.py --with-2back
pause
