@echo off
echo Starting Recipe App...
echo Installing required packages...
pip install flask anthropic requests --quiet
echo.
echo Open your browser at: http://localhost:5050
python "%~dp0recipe_server.py"
pause
