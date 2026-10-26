"""Wrap screenshots in a browser-window frame on a soft brand gradient (marketing style).

    python scripts/frame_screenshots.py      # after scripts/take_screenshots.py
Writes docs/screenshots/framed/*.png
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "screenshots"
OUT = SRC / "framed"
SHOTS = {
    "09-overview.png": ("Overview", "See what changed and what deserves attention"),
    "05-issue-investigation.png": ("Investigate", "Evidence, suggestion and confirmation, kept separate"),
    "15-import-validate.png": ("Guided import", "Know exactly what will be imported before saving"),
    "22-assistant-in-app.png": ("Assistant", "Answers from your data, actions with one click"),
    "08-report.png": ("Reports", "A management report for your next meeting"),
    "06-issue-board.png": ("Issue board", "Owners and status that follow the problem"),
}
TEMPLATE = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:1600px;height:1060px;font-family:Inter,-apple-system,"Segoe UI",Roboto,Arial,sans-serif}}
body{{background:radial-gradient(900px 600px at 10% 0%,#E0E3FF 0,transparent 60%),radial-gradient(900px 700px at 100% 100%,#F1E6FF 0,transparent 60%),#F6F4EF;
display:flex;flex-direction:column;align-items:center;justify-content:center;gap:26px}}
.lab{{text-align:center;color:#1B2440}}.lab b{{display:block;font-size:30px;letter-spacing:-.02em}}.lab span{{font-size:18px;color:#3E4766}}
.win{{width:1360px;border-radius:16px;overflow:hidden;background:#fff;box-shadow:0 40px 90px rgba(27,36,64,.28),0 0 0 1px rgba(27,36,64,.08)}}
.bar{{height:40px;background:#F3F2EE;display:flex;align-items:center;gap:8px;padding:0 16px;border-bottom:1px solid #E7E4DD}}
.bar i{{width:12px;height:12px;border-radius:50%;display:inline-block}}.bar .t{{margin-left:16px;font-size:13px;color:#6B7287;background:#fff;border:1px solid #E7E4DD;border-radius:8px;padding:4px 14px}}
img{{display:block;width:1360px}}
</style></head><body><div class="lab"><b>{title}</b><span>{sub}</span></div>
<div class="win"><div class="bar"><i style="background:#FF5F57"></i><i style="background:#FEBC2E"></i><i style="background:#28C840"></i><span class="t">Fixproblem</span></div><img src="{src}"></div></body></html>"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    exe = os.getenv("CHROMIUM_PATH")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1060}, device_scale_factor=1)
        for name, (title, sub) in SHOTS.items():
            if not (SRC / name).exists():
                continue
            html = OUT / "_frame.html"
            html.write_text(TEMPLATE.format(title=title, sub=sub, src=(SRC / name).resolve().as_uri()))
            page.goto(html.as_uri())
            page.wait_for_timeout(250)
            page.screenshot(path=str(OUT / name))
            print("framed", name)
        (OUT / "_frame.html").unlink(missing_ok=True)
        b.close()


if __name__ == "__main__":
    main()
