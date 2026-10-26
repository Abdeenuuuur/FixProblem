@echo off
REM Double-click this file to start Fixproblem. First run installs everything (about a minute).
cd /d "%~dp0"
where python >nul 2>nul || (echo Python is not installed. Get it from https://www.python.org/downloads/ ^(tick "Add Python to PATH"^). & pause & exit /b 1)
if not exist .venv (echo First run: setting up... & python -m venv .venv)
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt || (echo Install failed. Check your internet connection. & pause & exit /b 1)
if not exist instance\fixproblem.db python scripts\seed_demo.py
start "" http://127.0.0.1:5001
echo Fixproblem is running at http://127.0.0.1:5001 - keep this window open. Close it to stop.
python -m fixproblem.app
