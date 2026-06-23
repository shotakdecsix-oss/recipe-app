@echo off
cd /d "%~dp0"
git add -A
git commit -m "feat: add GitHub Actions keepalive for Render"
git push
pause
