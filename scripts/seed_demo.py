"""Load the synthetic sample data into the local database and run the analysis."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fixproblem import db, pipeline  # noqa: E402
from fixproblem.config import ROOT, settings  # noqa: E402
from fixproblem.ingest import import_csv  # noqa: E402

if __name__ == "__main__":
    if settings.db_path.exists():
        settings.db_path.unlink()
    conn = db.connect(settings.db_path)
    res = import_csv(conn, (ROOT / "data" / "sample" / "tickets.csv").read_text(encoding="utf-8"), "tickets.csv")
    print(f"Imported {res.accepted} tickets ({res.duplicates} duplicates, {res.rejected_count} rejected)")
    summary = pipeline.run(conn, settings, ROOT / "instance" / "model.pkl")
    print(f"Found {summary['issues']} issues and {summary['alerts']} emerging-issue alert(s); "
          f"{summary['needs_review']} tickets flagged for human review")
