"""Record the tutorial videos from the real application.

Each video is a genuine browser session driven by Playwright: real clicks, typing and page loads.
A presentation overlay (scripts/tutorial_overlay.js) adds an animated cursor, click ripples, a
spotlight and step captions. Branded title cards are added with ffmpeg crossfades.

    pip install playwright pillow && playwright install chromium   # plus ffmpeg on PATH
    python scripts/seed_demo.py && python scripts/record_tutorials.py
Outputs: fixproblem/static/videos/{tour,import,investigate}.mp4 and matching poster images.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from fixproblem.app import create_app  # noqa: E402

OUT = ROOT / "fixproblem" / "static" / "videos"
PORT, W, H = 5101, 1440, 900
BASE = f"http://127.0.0.1:{PORT}"
OVERLAY = (ROOT / "scripts" / "tutorial_overlay.js").read_text()


class Director:
    """Small helper that moves the fake cursor, clicks, types and shows captions."""

    def __init__(self, page, total):
        self.page, self.total, self.last = page, total, None

    def wait(self, ms):
        self.page.wait_for_timeout(ms)

    def caption(self, n, title, text):
        self.last = (n, title, text)
        self.page.evaluate("([n,t,a,b]) => __fp.caption(n,t,a,b)", [n, self.total, title, text])
        self.wait(400)

    def restore(self):
        self.page.wait_for_load_state("networkidle")
        if self.last:
            self.page.evaluate("([n,t,a,b]) => __fp.caption(n,t,a,b)", [self.last[0], self.total, *self.last[1:]])
        self.wait(500)

    def box(self, sel):
        loc = self.page.locator(sel).first
        loc.scroll_into_view_if_needed()
        self.wait(250)
        return loc, loc.bounding_box()

    def point(self, sel, ms=900):
        loc, b = self.box(sel)
        self.page.evaluate("([x,y]) => __fp.moveXY(x,y)", [b["x"] + min(b["width"] / 2, 140), b["y"] + b["height"] / 2])
        self.wait(ms)
        return loc

    def spot(self, sel, ms=2200, pad=10):
        _, b = self.box(sel)
        self.page.evaluate("([x,y,w,h,p]) => __fp.spotBox(x,y,w,h,p)", [b["x"], b["y"], b["width"], b["height"], pad])
        self.wait(ms)
        self.page.evaluate("() => __fp.unspot()")
        self.wait(300)

    def click(self, sel, nav=False):
        loc = self.point(sel)
        self.page.evaluate("() => __fp.ripple()")
        self.wait(250)
        if nav:
            with self.page.expect_navigation():
                loc.click()
            self.restore()
        else:
            loc.click()
            self.wait(600)

    def type(self, sel, text):
        loc = self.point(sel, 700)
        loc.click()
        loc.fill("")
        loc.type(text, delay=45)
        self.wait(500)

    def select(self, sel, value, nav=False):
        loc = self.point(sel, 800)
        self.page.evaluate("() => __fp.ripple()")
        if nav:
            with self.page.expect_navigation():
                loc.select_option(value)
            self.restore()
        else:
            loc.select_option(value)
            self.wait(700)

    def scroll(self, px, steps=12):
        for _ in range(steps):
            self.page.mouse.wheel(0, px / steps)
            self.wait(45)
        self.wait(500)


# ----------------------------------------------------------------------------- scripts for each video
def tour(d: Director):
    p = d.page
    p.goto(BASE + "/"); d.restore()
    d.caption(1, "Know what to fix next", "Fixproblem turns support tickets into recurring issues, evidence and actions.")
    d.spot("section.hero h1", 2600)
    d.scroll(520)
    d.spot("#product .card", 2600)
    d.caption(2, "Try it without an account", "The interactive demo uses clearly labelled synthetic tickets.")
    d.scroll(-520)
    d.click("a:has-text('Try the interactive demo')", nav=True)
    d.caption(3, "See what changed", "The overview flags issues whose volume is statistically unusual.")
    d.spot(".alert-card", 3000)
    d.caption(4, "Inspect the evidence", "Every alert links to the customers’ own words, so the team can verify it.")
    d.click("a:has-text('Inspect the')", nav=True)
    d.spot(".card:has(h2:has-text('supporting tickets'))", 2600)
    d.caption(5, "Investigate one issue", "Data, system suggestions and team-confirmed findings stay separate.")
    d.click("tr.row-link >> nth=0", nav=True)
    d.spot(".three", 3200)
    d.caption(6, "Track work on the board", "Issues move from New to Investigating to Resolved, with owners.")
    d.click("nav a:has-text('Issues')", nav=True)
    d.spot(".board", 2600)
    p.evaluate("() => __fp.place('top')")
    d.caption(7, "Ask the assistant", "It answers from the docs and your data, and suggests one-click actions.")
    d.click("#ai-fab")
    d.click("button.ai-chip:has-text('What should we fix first?')")
    d.wait(1200)
    d.spot("#ai-panel", 2600, pad=6)
    d.click("#ai-close")
    p.evaluate("() => __fp.place('bottom')")
    d.caption(8, "Share a management report", "Period figures, evidence and methodology, ready for your next meeting.")
    d.click("nav a:has-text('Reports')", nav=True)
    d.spot(".card:has(h2:has-text('Executive summary'))", 2800)


def import_video(d: Director):
    p = d.page
    p.goto(BASE + "/dashboard"); d.restore()
    d.caption(1, "Open Imports & integrations", "Upload a CSV export from any helpdesk. Nothing is saved until you confirm.")
    d.click("nav a:has-text('Imports')", nav=True)
    d.spot("label.drop", 1800)
    p.set_input_files("input[name=file]", str(ROOT / "data" / "sample" / "example_upload.csv"))
    d.wait(600)
    d.click("button:has-text('Next: map columns')", nav=True)
    d.caption(2, "Map your columns", "Fixproblem suggests a match for each field. Check them and choose the date order.")
    d.spot(".card:has(h2:has-text('Fields'))", 2400)
    d.select("select[name=date_order]", "dmy")
    d.spot(".card:has(h2:has-text('Preview'))", 2000)
    d.click("button:has-text('Next: validate')", nav=True)
    d.caption(3, "Check before importing", "See exactly what will be imported, skipped as duplicates or rejected, and why.")
    d.spot(".grid.g4", 2400)
    d.spot(".card:has(h2:has-text('rejected'))", 2600)
    d.caption(4, "Confirm and analyse", "Data is imported first, then analysed. Both steps are shown separately.")
    d.click("button:has-text('Confirm')", nav=True)
    d.spot(".grid.g2e", 2600)
    d.caption(5, "Explore the result", "Your tickets are now part of the overview, issues and reports.")
    d.click("a:has-text('See the overview')", nav=True)
    d.spot(".grid.g4", 2200)


def investigate(d: Director):
    p = d.page
    p.goto(BASE + "/dashboard"); d.restore()
    d.caption(1, "Start from what deserves attention", "Issues are ranked by volume, severity, customer tier and growth.")
    d.spot(".card:has(h2:has-text('deserve attention'))", 2400)
    d.click(".card:has(h2:has-text('deserve attention')) tr.row-link >> nth=0", nav=True)
    d.caption(2, "Assign an owner and status", "Ownership and status follow the problem across every re-analysis.")
    d.select("select[name=owner]", "analyst", nav=True)
    d.select("select[name=status]", "investigating", nav=True)
    d.caption(3, "Record what the team confirmed", "Keep confirmed findings separate from what the data shows.")
    d.type("textarea[name=confirmed]", "Engineering reproduced the crash in v5.2 login. Fix in QA.")
    d.click(".k-conf button", nav=True)
    d.spot(".three", 2400)
    d.caption(4, "Teach the model", "Check tickets the model was unsure about. Corrections can be undone.")
    d.click("nav a:has-text('Review queue')", nav=True)
    d.spot("tbody tr >> nth=0", 1800)
    d.click("form.label-form button >> nth=0")
    d.wait(800)
    d.caption(5, "Compare before and after a fix", "An observation, not proof of cause, shown with ticket share.")
    d.click("nav a:has-text('Reports')", nav=True)
    d.type("input[name=date]", "2026-08-17")
    d.click("button:has-text('Compare')", nav=True)
    d.spot(".card:has(h2:has-text('Before and after'))", 3000)
    d.caption(6, "Export for your manager", "Download the management report as PDF or CSV.")
    d.spot("a:has-text('PDF report')", 2200, pad=6)


VIDEOS = [
    ("tour", None, tour, 8, "Know what to fix next", "A 90-second tour of Fixproblem"),
    ("import", "admin", import_video, 5, "Import your tickets", "From CSV to analysis in five steps"),
    ("investigate", "analyst", investigate, 6, "Investigate and act", "From an alert to an owner, a fix and a report"),
]


def card(browser, title, sub, pill, out: Path):
    html = (ROOT / "scripts" / "title_card.html").read_text()
    icon = (ROOT / "fixproblem" / "static" / "brand" / "icon.svg").resolve().as_uri()
    html = html.replace("ICON", icon).replace("TITLE", title).replace("SUB", sub).replace("PILL", pill)
    tmp = out.with_suffix(".html")
    tmp.write_text(html)
    pg = browser.new_page(viewport={"width": W, "height": H})
    pg.goto(tmp.as_uri())
    pg.wait_for_timeout(300)
    pg.screenshot(path=str(out))
    pg.close()
    tmp.unlink()


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return float(out)


def finish(raw: Path, intro: Path, outro: Path, dest: Path):
    work = raw.with_name(raw.stem + "_main.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.8", "-i", str(raw), "-vf", f"scale={W}:{H},fps=30,format=yuv420p",
                    "-c:v", "libx264", "-crf", "18", "-preset", "medium", str(work)], check=True)
    m, fade, ci, co = duration(work), 0.6, 3.0, 3.5
    off2 = ci + m - fade - fade
    flt = (f"[0]scale={W}:{H},fps=30,format=yuv420p[a];[1]fps=30,format=yuv420p[b];[2]scale={W}:{H},fps=30,format=yuv420p[c];"
           f"[a][b]xfade=transition=fade:duration={fade}:offset={ci - fade}[ab];"
           f"[ab][c]xfade=transition=fade:duration={fade}:offset={off2:.2f}[v]")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-t", str(ci), "-i", str(intro), "-i", str(work),
                    "-loop", "1", "-t", str(co), "-i", str(outro), "-filter_complex", flt, "-map", "[v]",
                    "-c:v", "libx264", "-crf", "20", "-preset", "medium", "-movflags", "+faststart", str(dest)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "6", "-i", str(dest), "-frames:v", "1",
                    str(dest.with_suffix(".jpg"))], check=True)
    return duration(dest)


def main():
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    OUT.mkdir(parents=True, exist_ok=True)
    server = make_server("127.0.0.1", PORT, create_app())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.5)
    exe = os.getenv("CHROMIUM_PATH")
    only = set(sys.argv[1:])
    with sync_playwright() as pw, tempfile.TemporaryDirectory() as tmp:
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        for name, user, script, steps, title, sub in VIDEOS:
            if only and name not in only:
                continue
            state = None
            if user:                                          # sign in off camera
                c = browser.new_context()
                pg = c.new_page()
                pg.goto(BASE + "/login")
                pg.fill("input[name=username]", user)
                pg.fill("input[name=password]", f"{user}-demo")
                with pg.expect_navigation():
                    pg.click("form#login button[type=submit]")
                with pg.expect_response("**/tour/done") if pg.locator("#tour-skip").count() else _noop():
                    if pg.locator("#tour-skip").count():
                        pg.click("#tour-skip")
                state = c.storage_state()
                c.close()
            ctx = browser.new_context(viewport={"width": W, "height": H}, storage_state=state,
                                      record_video_dir=tmp, record_video_size={"width": W, "height": H})
            ctx.add_init_script("localStorage.setItem('fp-theme','light'); sessionStorage.removeItem('fp-pos');")
            ctx.add_init_script(OVERLAY)
            page = ctx.new_page()
            page.set_default_timeout(15000)
            script(Director(page, steps))
            page.evaluate("() => __fp.hide()")
            page.wait_for_timeout(700)
            video = page.video.path()
            ctx.close()
            intro, outro = Path(tmp) / f"{name}_in.png", Path(tmp) / f"{name}_out.png"
            card(browser, title, sub, f"{steps} steps", intro)
            card(browser, "Know what to fix next", "Try the interactive demo, then bring your own tickets.", "Fixproblem", outro)
            secs = finish(Path(video), intro, outro, OUT / f"{name}.mp4")
            print(f"recorded {name}.mp4 ({secs:.0f} s)")
        browser.close()
    server.shutdown()


class _noop:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


if __name__ == "__main__":
    main()
