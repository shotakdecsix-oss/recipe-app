@echo off
cd /d "%~dp0"
git add -A
git commit -m "feat: step timer with beep"
git push
pause
