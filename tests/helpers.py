import os
import sys

os.environ.setdefault("FIXPROBLEM_CSRF", "0")   # CSRF has dedicated tests in test_review_fixes.py
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SAMPLE = ROOT / "data" / "sample" / "tickets.csv"
