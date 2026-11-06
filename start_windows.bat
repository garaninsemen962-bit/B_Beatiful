@echo off
cd /d "%~dp0"
start "" http://127.0.0.1:8765
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 app.py
) else (
  python app.py
)
pause
