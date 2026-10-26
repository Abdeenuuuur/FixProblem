#!/bin/bash
# Double-click this file in Finder to start Fixproblem. First run installs everything (about a minute).
cd "$(dirname "$0")" || exit 1
echo "Starting Fixproblem..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Download it from https://www.python.org/downloads/ and try again."
  read -r -p "Press Enter to close."; exit 1
fi
if [ ! -d .venv ]; then
  echo "First run: setting up (this happens once)..."
  python3 -m venv .venv || { read -r -p "Setup failed. Press Enter to close."; exit 1; }
fi
source .venv/bin/activate
pip install -q -r requirements.txt || { read -r -p "Install failed. Check your internet connection. Press Enter to close."; exit 1; }
if [ ! -f instance/fixproblem.db ]; then python scripts/seed_demo.py; fi
( sleep 2; open "http://127.0.0.1:5001" ) &
echo ""
echo "Fixproblem is running at http://127.0.0.1:5001"
echo "Keep this window open while you use it. Close it (or press Ctrl+C) to stop."
python -m fixproblem.app
