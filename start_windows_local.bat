@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Demo mode without Docker: procedures run on this computer without isolation.
start "" http://127.0.0.1:8765
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 app.py --local-runner
) else (
  python app.py --local-runner
)
pause
