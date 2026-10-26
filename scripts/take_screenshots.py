"""Capture README screenshots with Playwright (pip install playwright && playwright install chromium).
Run scripts/seed_demo.py first."""
from __future__ import annotations

import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from fixproblem.app import create_app  # noqa: E402

OUT = ROOT / "docs" / "screenshots"
PORT = 5097
BASE = f"http://127.0.0.1:{PORT}"


def main():
    server = make_server("127.0.0.1", PORT, create_app())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.5)
    OUT.mkdir(parents=True, exist_ok=True)
    exe = os.getenv("CHROMIUM_PATH")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()

        def session(user=None, theme="light"):
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script(f"localStorage.setItem('fp-theme', '{theme}')")
            page = ctx.new_page()
            page.set_default_timeout(10000)
            if user:
                page.goto(f"{BASE}/login")
                page.fill("input[name=username]", user)
                page.fill("input[name=password]", f"{user}-demo")
                with page.expect_navigation():
                    page.click("form#login button[type=submit]")
            return ctx, page

        def snap(page, name, full=False):
            page.wait_for_timeout(300)
            page.screenshot(path=str(OUT / name), full_page=full)
            print("saved", name)

        # public website (no account)
        ctx, page = session()
        page.goto(BASE + "/"); snap(page, "01-landing.png")
        page.goto(BASE + "/"); snap(page, "01b-landing-full.png", full=True)
        page.goto(BASE + "/pricing"); snap(page, "02-pricing.png")
        page.goto(BASE + "/help"); snap(page, "03-help-centre.png")
        page.goto(BASE + "/demo"); snap(page, "04-demo-overview.png")
        ctx.close()

        # analyst investigates and acts
        import sqlite3
        from fixproblem.config import settings
        top = sqlite3.connect(settings.db_path).execute(
            "SELECT issue_id FROM issues ORDER BY emerging DESC, priority DESC").fetchone()[0]
        ctx, page = session("analyst")
        page.goto(f"{BASE}/issues/{top}")
        page.fill("textarea[name=description]", "Customers report the mobile app crashing at login after updating.")
        with page.expect_navigation():
            page.click("button:has-text('Save description')")
        page.fill("textarea[name=confirmed]", "Engineering reproduced a crash in v5.2 login. Fix in QA.")
        with page.expect_navigation():
            page.click(".k-conf button")
        with page.expect_navigation():
            page.select_option("select[name=owner]", "analyst")
        snap(page, "05-issue-investigation.png")
        page.goto(BASE + "/issues"); snap(page, "06-issue-board.png")
        page.goto(BASE + "/trends"); snap(page, "07-trends-with-table.png")
        page.goto(f"{BASE}/reports?issue={top}&date=2026-08-17"); snap(page, "08-report.png")
        page.goto(BASE + "/dashboard"); snap(page, "09-overview.png")
        ctx.close()

        ctx, page = session("analyst", theme="dark")
        page.goto(BASE + "/dashboard"); snap(page, "10-overview-dark.png")
        ctx.close()

        # agent reviews uncertain tickets
        ctx, page = session("agent")
        page.goto(BASE + "/review")
        page.locator("form.label-form button").first.click()
        page.wait_for_timeout(500)
        page.goto(BASE + "/review"); snap(page, "11-review-queue.png")
        page.goto(BASE + "/feedback"); snap(page, "12-feedback.png")
        ctx.close()

        # admin: guided import wizard
        ctx, page = session("admin")
        page.goto(BASE + "/import"); snap(page, "13-import-upload.png")
        page.set_input_files("input[name=file]", str(ROOT / "data" / "sample" / "example_upload.csv"))
        with page.expect_navigation():
            page.click("button:has-text('Next: map columns')")
        page.select_option("select[name=date_order]", "dmy")
        snap(page, "14-import-map.png")
        with page.expect_navigation():
            page.click("button:has-text('Next: validate')")
        snap(page, "15-import-validate.png")
        ctx.close()

        # assistant on the website and in the app
        ctx, page = session()
        page.goto(BASE + "/")
        page.fill("#hero-q", "Can I try it without customer data?")
        page.press("#hero-q", "Enter")
        page.wait_for_timeout(1200)
        snap(page, "21-assistant-website.png")
        ctx.close()
        ctx, page = session("analyst")
        page.goto(BASE + "/dashboard")
        page.click("#ai-fab")
        page.click("button.ai-chip:has-text('What should we fix first?')")
        page.wait_for_timeout(1200)
        snap(page, "22-assistant-in-app.png")
        ctx.close()

        # new user: sign-up and first-use tutorial
        ctx, page = session()
        page.goto(BASE + "/login?tab=signup"); snap(page, "16-signup.png")
        page.fill("input[name=name]", "Sara Ahmed"); page.fill("input[name=email]", "sara@acme.example")
        page.fill("input[name=password]", "Demo12345"); page.fill("input[name=workspace]", "Acme Support")
        page.check("input[name=terms]")
        with page.expect_navigation():
            page.click("button:has-text('Start your workspace')")
        snap(page, "17-tutorial.png")
        with page.expect_response("**/tour/done"):
            page.click("#tour-skip")
        page.goto(BASE + "/dashboard"); snap(page, "18-checklist.png")
        ctx.close()
        ctx, page = session()
        page.goto(BASE + "/login?tab=signup")
        page.fill("input[name=name]", "Lee Park"); page.fill("input[name=email]", "lee@beta.example")
        page.fill("input[name=password]", "Demo12345"); page.fill("input[name=workspace]", "Beta Support")
        page.check("input[name=terms]")
        with page.expect_navigation():
            page.click("button:has-text('Start your workspace')")
        with page.expect_response("**/tour/done"):
            page.click("#tour-skip")
        page.goto(BASE + "/dashboard"); snap(page, "19-private-empty-workspace.png")
        ctx.close()
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()
