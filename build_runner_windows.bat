@echo off
cd /d "%~dp0"
docker build -t decision-diff-runner:2 -f Dockerfile.runner .
pause
