"""Fixproblem routes added on top of the core app: public site, trends, reports, team,
help, feedback, guided import, issue investigation and review-queue controls."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from flask import (Response, abort, flash, redirect, render_template, request, send_file, session, url_for)

from . import accounts, help_content
from .auth import can, requires
from .config import LAUNCH_OFFER, PLANS, ROOT, TRIAL_DAYS
from .ingest import REQUIRED, OPTIONAL, ImportFormatError, import_csv

FMT = "%Y-%m-%d %H:%M"
UPLOAD_DIR = ROOT / "instance" / "uploads"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
SYNONYMS = {
    "ticket_id": ["ticket_id", "id", "ticket", "ticket number", "case id", "reference"],
    "created_at": ["created_at", "created", "date", "timestamp", "submitted", "opened", "time"],
    "body": ["body", "description", "message", "text", "comment", "content", "details"],
    "subject": ["subject", "title", "summary"], "channel": ["channel", "source", "via"],
    "customer_id": ["customer_id", "customer", "requester id", "user id"],
    "customer_email": ["customer_email", "email", "requester email"],
    "customer_tier": ["customer_tier", "tier", "plan", "segment"], "agent_tag": ["agent_tag", "category", "tag", "type"],
}
DATE_ORDERS = {"auto": None, "dmy": ["%d/%m/%Y %H:%M", "%d/%m/%Y"], "mdy": ["%m/%d/%Y %H:%M", "%m/%d/%Y"],
               "iso": ["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]}
CATEGORY_SEVERITY_NOTE = "Severity weights are set by the business in configuration."
NEXT_STEPS = {
    "technical_app": "Check recent app releases and crash reports around the first-seen date; ask engineering to reproduce with the example tickets.",
    "connectivity": "Compare affected customers’ locations and times with network monitoring before contacting customers.",
    "billing": "Sample affected accounts with the billing team to see whether one charge run or price change explains the tickets.",
    "account_access": "Test the reset and verification journeys end to end and check email/SMS delivery logs.",
    "delivery": "Compare tickets with courier tracking for the same orders and check stock or address issues.",
    "cancellation": "Read a sample of tickets for the stated reasons before changing retention offers.",
}


def suggest_mapping(headers: list[str]) -> dict:
    out, used = {}, set()
    norm = {h: re.sub(r"[^a-z ]", " ", h.lower().replace("_", " ")).strip() for h in headers}
    for field, names in SYNONYMS.items():
        for h in headers:
            if h in used:
                continue
            if norm[h] in [n.replace("_", " ") for n in names]:
                out[field] = h
                used.add(h)
                break
    return out


def remap(text: str, mapping: dict, date_order: str) -> str:
    """Rewrite an uploaded CSV into Fixproblem's column names and date format."""
    reader = csv.DictReader(io.StringIO(text))
    out = io.StringIO()
    fields = list(REQUIRED + OPTIONAL)
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    fmts = DATE_ORDERS.get(date_order)
    for row in reader:
        new = {f: (row.get(mapping[f], "") if mapping.get(f) else "") for f in fields}
        if fmts and new["created_at"]:
            for f in fmts:
                try:
                    new["created_at"] = datetime.strptime(new["created_at"].strip(), f).strftime(FMT)
                    break
                except ValueError:
                    continue
        w.writerow(new)
    return out.getvalue()


def register(app, conn, current_user, spark, meta, reanalyse, as_of, cfg, pconn, ws_id):

    def upload_dir() -> Path:
        """Uploads and error reports are stored per workspace, like everything else customers own."""
        d = UPLOAD_DIR / f"ws_{int(ws_id())}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ref(prefix="FP"):
        return f"{prefix}-{secrets.token_hex(3).upper()}"

    @app.context_processor
    def fp_context():
        last = conn().execute("SELECT imported_at FROM import_log ORDER BY id DESC LIMIT 1").fetchone() if session.get("role") else None
        return {"plans": PLANS, "launch_offer": LAUNCH_OFFER, "trial_days": TRIAL_DAYS,
                "last_analysis": last[0] if last else None,
                "help_articles": help_content.ARTICLES}

    # ------------------------------------------------------------------ public site
    @app.route("/demo")
    def demo():
        """Start the read-only interactive demo on clearly labelled sample data: no account needed."""
        session.clear()
        session.update(user="guest", role="guest", plan="free", guest_id=accounts.new_guest_id(), show_tour=False)
        return redirect(url_for("dashboard"))

    @app.route("/pricing")
    def pricing():
        return render_template("public/pricing.html")

    @app.route("/evaluation")
    def evaluation():
        m = json.loads((ROOT / "results" / "metrics.json").read_text()) if (ROOT / "results" / "metrics.json").exists() else {}
        return render_template("public/evaluation.html", m=m)

    @app.route("/changelog")
    def changelog():
        return render_template("public/changelog.html")

    @app.route("/about")
    def about():
        return render_template("public/about.html")

    @app.route("/terms")
    def terms():
        return render_template("public/terms.html")

    @app.route("/security")
    def security():
        return render_template("public/security.html")

    @app.route("/status")
    def status():
        last = conn().execute("SELECT imported_at FROM import_log ORDER BY id DESC LIMIT 1").fetchone()
        return render_template("public/status.html", last=last[0] if last else None)

    @app.route("/csv-template.csv")
    def csv_template():
        body = ("ticket_id,created_at,subject,body,channel,customer_id,customer_email,customer_tier,agent_tag\n"
                "T-1001,2026-09-01 09:30,Charged twice,I was charged twice this month,email,C123,jane@example.com,standard,billing\n"
                "T-1002,2026-09-01 10:05,App crash,The app closes when I log in,chat,C456,sam@example.com,premium,technical_app\n")
        return Response(body, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=fixproblem-template.csv"})

    @app.route("/help")
    def help_centre():
        q = request.args.get("q", "").strip().lower()
        arts = [a for a in help_content.ARTICLES if not q or q in (a[1] + a[3]).lower()]
        return render_template("help.html", articles=arts, q=q, faqs=help_content.FAQS, steps=help_content.TUTORIAL_STEPS)

    @app.route("/help/<slug>")
    def help_article(slug):
        art = next((a for a in help_content.ARTICLES if a[0] == slug), None)
        if art is None:
            abort(404)
        html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", art[3].strip())
        html = re.sub(r"\*(.+?)\*", r"<i>\1</i>", html)
        return render_template("help_article.html", art=art, paragraphs=[p for p in html.split("\n") if p.strip()])

    @app.route("/contact", methods=["GET", "POST"])
    def contact():
        if request.method == "POST":
            f = request.form
            if not accounts.EMAIL_RE.match((f.get("email") or "").strip()) or len((f.get("message") or "").strip()) < 10:
                flash("Please give a valid email and a message of at least 10 characters.", "error")
                return render_template("public/contact.html", form=f), 400
            r = ref("FP")
            pconn().execute("INSERT INTO support_requests (ref, kind, category, name, email, description) VALUES (?,?,?,?,?,?)",
                           (r, f.get("kind", "contact")[:20], f.get("topic", "")[:40], f.get("name", "")[:80],
                            f.get("email").strip()[:120], f.get("message").strip()[:4000]))
            pconn().commit()
            return render_template("public/contact.html", sent=r)
        return render_template("public/contact.html", kind=request.args.get("kind", "contact"))

    # ------------------------------------------------------------------ in-app feedback / support
    @app.route("/feedback", methods=["GET", "POST"])
    @requires("view")
    def feedback():
        if request.method == "POST":
            f = request.form
            desc = (f.get("description") or "").strip()
            if len(desc) < 10:
                flash("Please describe the problem in at least 10 characters.", "error")
                return render_template("feedback.html", form=f), 400
            shot_name = None
            img = request.files.get("screenshot")
            if img and img.filename:
                data = img.read()
                if len(data) > 2 * 1024 * 1024 or not data[:8].startswith((b"\x89PNG", b"\xff\xd8\xff")):
                    flash("Screenshots must be PNG or JPG and under 2 MB.", "error")
                    return render_template("feedback.html", form=f), 400
                (UPLOAD_DIR / "screenshots").mkdir(parents=True, exist_ok=True)   # support data: platform-level
                ext = "png" if data.startswith(b"\x89PNG") else "jpg"
                shot_name = f"{secrets.token_hex(8)}.{ext}"
                (UPLOAD_DIR / "screenshots" / shot_name).write_bytes(data)
            diag = None
            if f.get("diagnostics"):      # only with explicit permission; never ticket text
                diag = json.dumps({"page": f.get("page", "")[:200], "role": session.get("role"), "plan": session.get("plan"),
                                   "browser": request.headers.get("User-Agent", "")[:200]})
            u = current_user()
            r = ref("FP")
            pconn().execute("INSERT INTO support_requests (ref, kind, category, name, email, description, screenshot, diagnostics) "
                           "VALUES (?,?,?,?,?,?,?,?)", (r, "support", f.get("category", "other")[:40],
                                                        u["name"] if u else session.get("user"), u["email"] if u else None,
                                                        desc[:4000], shot_name, diag))
            pconn().commit()
            return render_template("feedback.html", sent=r)
        return render_template("feedback.html", page=request.args.get("page", ""))

    # ------------------------------------------------------------------ trends
    @app.route("/trends")
    @requires("view")
    def trends():
        weeks = int(request.args.get("weeks", 12)) if request.args.get("weeks", "12").isdigit() else 12
        weeks = weeks if weeks in (4, 8, 12, 26) else 12
        end = as_of()
        series = defaultdict(lambda: [0] * weeks)
        for r in conn().execute("SELECT created_at, pred_category FROM tickets"):
            age = (end - datetime.strptime(r[0], FMT)).days // 7
            if 0 <= age < weeks:
                series[r[1]][weeks - 1 - age] += 1
        labels = [(end - timedelta(weeks=weeks - 1 - i)).strftime("%d %b") for i in range(weeks)]
        totals = [sum(v[i] for v in series.values()) for i in range(weeks)]
        return render_template("trends.html", series=dict(series), labels=labels, totals=totals, weeks=weeks)

    # ------------------------------------------------------------------ reports
    def report_data(start: str | None, end_: str | None) -> dict:
        c = conn()
        lo = start or c.execute("SELECT MIN(substr(created_at,1,10)) FROM tickets").fetchone()[0] or "1970-01-01"
        hi = end_ or c.execute("SELECT MAX(substr(created_at,1,10)) FROM tickets").fetchone()[0] or "2999-12-31"
        where = "substr(created_at,1,10) BETWEEN ? AND ?"
        total = c.execute(f"SELECT COUNT(*) FROM tickets WHERE {where}", (lo, hi)).fetchone()[0]
        rejected = c.execute("SELECT COALESCE(SUM(rejected),0) FROM import_log").fetchone()[0]   # all imports, all time
        # Top issues FOR THIS PERIOD: ranked by tickets inside the selected dates (priority breaks ties).
        ranked = c.execute(f"SELECT i.*, COUNT(t.ticket_id) AS in_range FROM issues i JOIN tickets t ON t.issue_id=i.issue_id "
                           f"WHERE substr(t.created_at,1,10) BETWEEN ? AND ? GROUP BY i.issue_id "
                           f"ORDER BY in_range DESC, i.priority DESC LIMIT 10", (lo, hi)).fetchall()
        issues = []
        for i in ranked:
            ex = c.execute(f"SELECT ticket_id, body FROM tickets WHERE issue_id=? AND {where} ORDER BY created_at DESC LIMIT 2",
                           (i["issue_id"], lo, hi)).fetchall()
            issues.append(dict(i) | meta(i["key"]) | {"examples": [dict(e) for e in ex]})
        freshness = c.execute("SELECT MAX(created_at) FROM tickets").fetchone()[0]
        # Alerts describe the latest 14 days of data; only show them if that window overlaps the period.
        window_start = (datetime.strptime(freshness, FMT) - timedelta(days=14)).strftime("%Y-%m-%d") if freshness else hi
        alerts = [dict(a) for a in c.execute("SELECT * FROM alerts")] if hi >= window_start else []
        resolved_now = c.execute("SELECT COUNT(*) FROM issue_meta m JOIN issues i ON i.key=m.key WHERE m.status='resolved'").fetchone()[0]
        return {"from": lo, "to": hi, "total": total, "rejected_all_time": rejected, "issues": issues, "alerts": alerts,
                "alerts_window": window_start, "resolved_now": resolved_now,
                "generated": datetime.now().strftime("%d %b %Y %H:%M"), "freshness": freshness}

    @app.route("/reports")
    @requires("view")
    def reports():
        d = report_data(request.args.get("from"), request.args.get("to"))
        obs = None
        if request.args.get("issue") and request.args.get("date"):
            obs = intervention(int(request.args["issue"]), request.args["date"])
        return render_template("reports.html", d=d, obs=obs,
                               all_issues=conn().execute("SELECT issue_id, label FROM issues ORDER BY priority DESC").fetchall())

    def intervention(issue_id: int, date: str) -> dict | None:
        """Before/after comparison around a recorded intervention date. An observation, not proof of cause."""
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return None
        c, w = conn(), 4
        def count(sql_extra, a, b):
            return c.execute(f"SELECT COUNT(*) FROM tickets WHERE created_at >= ? AND created_at < ? {sql_extra}",
                             (a.strftime(FMT), b.strftime(FMT), *([issue_id] if sql_extra else []))).fetchone()[0]
        before = count("AND issue_id=?", d - timedelta(weeks=w), d)
        after = count("AND issue_id=?", d, d + timedelta(weeks=w))
        tb, ta = count("", d - timedelta(weeks=w), d), count("", d, d + timedelta(weeks=w))
        label = c.execute("SELECT label FROM issues WHERE issue_id=?", (issue_id,)).fetchone()
        return {"issue": label[0] if label else "?", "date": date, "weeks": w, "before": before, "after": after,
                "share_before": before / tb if tb else None, "share_after": after / ta if ta else None,
                "after_complete": d + timedelta(weeks=w) <= as_of()}

    @app.route("/reports/export.csv")
    @requires("export")
    def report_csv():
        d = report_data(request.args.get("from"), request.args.get("to"))
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["# Fixproblem report", f"{d['from']} to {d['to']}", f"tickets {d['total']}", f"generated {d['generated']}"])
        w.writerow(["issue_id", "issue", "category", "tickets_in_range", "tickets_total", "last_14_days", "growth",
                    "priority", "status", "owner", "team_confirmed"])
        for i in d["issues"]:
            w.writerow([i["issue_id"], i["label"], i["category"], i["in_range"], i["size"], i["recent"], i["growth"],
                        i["priority"], i["status"], i["owner"] or "", i.get("confirmed") or ""])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=fixproblem-report-{d['from']}-{d['to']}.csv"})

    @app.route("/reports/report.pdf")
    @requires("export")
    def report_pdf():
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        from xml.sax.saxutils import escape as esc          # customer text must never be read as markup
        d = report_data(request.args.get("from"), request.args.get("to"))
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Fixproblem report {d['from']} to {d['to']}",
                                author="Fixproblem", subject="Customer support issues report")
        st = getSampleStyleSheet()
        story = [Paragraph("Fixproblem management report", st["Title"]),
                 Paragraph(esc(f"Period: {d['from']} to {d['to']} · {d['total']:,} tickets in this period · "
                               f"latest ticket {d['freshness']} · generated {d['generated']}"), st["Normal"]),
                 Paragraph(esc(f"All-time context: {d['rejected_all_time']} records rejected across all imports; "
                               f"{d['resolved_now']} issue(s) currently marked resolved."), st["Normal"]), Spacer(1, 10),
                 Paragraph("Executive summary", st["Heading2"])]
        top = d["issues"][0] if d["issues"] else None
        summary = (f"The issue with most tickets in this period is “{top['label']}” ({top['in_range']} tickets; current "
                   f"status {top['status']}). " if top else "No tickets in this period. ")
        summary += (f"{len(d['alerts'])} emerging alert(s) in the latest 14 days of data." if d["alerts"] else
                    "Emerging alerts cover the latest 14 days of data, which is outside this period.")
        story += [Paragraph(esc(summary), st["Normal"]), Paragraph("Top recurring issues in this period", st["Heading2"])]
        small = st["BodyText"].clone("small", fontSize=8, leading=10)
        rows = [["Issue", "In period", "Growth*", "Priority*", "Status*", "Owner*"]] + [
            [Paragraph(esc(i["label"]), small), i["in_range"], f"{i['growth']}x", f"{i['priority']:.0f}",
             Paragraph(esc(i["status"]), small), Paragraph(esc(i["owner"] or "-"), small)] for i in d["issues"]]
        w = doc.width                                       # fit the usable page width exactly
        t = Table(rows, repeatRows=1, colWidths=[w * .40, w * .11, w * .11, w * .11, w * .14, w * .13])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1B2440")),
                               ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), .3, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [t, Paragraph("* Current values as of the latest analysis, not limited to the period.", small),
                  Paragraph("Evidence from this period", st["Heading2"])]
        for i in d["issues"][:5]:
            for e in i["examples"]:
                story.append(Paragraph(f"<b>{esc(i['label'])}</b> · {esc(e['ticket_id'])}: {esc(e['body'][:180])}", st["Normal"]))
        story += [Paragraph("Methodology and limitations", st["Heading2"]),
                  Paragraph("Tickets are classified by a TF-IDF and logistic-regression model and grouped with k-means. "
                            "Emerging alerts use a Poisson test with a Bonferroni correction. Alerts show changes in demand; "
                            "they do not prove a root cause. Team-confirmed findings are shown separately from system output.", st["Normal"])]
        doc.build(story)
        buf.seek(0)
        return send_file(buf, mimetype="application/pdf", download_name=f"fixproblem-report-{d['from']}-{d['to']}.pdf")

    # ------------------------------------------------------------------ team
    @app.route("/team")
    @requires("view")
    def team():
        from .config import DEMO_USERS
        return render_template("team.html", members=[(u, d["role"]) for u, d in DEMO_USERS.items()])

    # ------------------------------------------------------------------ guided import
    @app.route("/import/wizard", methods=["POST"])
    @requires("import")
    def import_wizard():
        step = request.form.get("step", "upload")
        updir = upload_dir()
        if step == "upload":
            f = request.files.get("file")
            if request.form.get("sample") and int(ws_id()) != 1:
                return redirect(url_for("workspace_sample"), code=307)   # sample data gets its own workspace
            if request.form.get("sample"):
                data, name = (ROOT / "data" / "sample" / "tickets.csv").read_bytes(), "sample tickets.csv"
            elif not f or not f.filename or not f.filename.lower().endswith(".csv"):
                flash("Choose a .csv file.", "error")
                return redirect(url_for("import_view"))
            else:
                data, name = f.read(MAX_UPLOAD_BYTES + 1), f.filename
                if len(data) > MAX_UPLOAD_BYTES:
                    flash("That file is larger than 20 MB. Split it into smaller files.", "error")
                    return redirect(url_for("import_view"))
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                flash("The file is not UTF-8 text. Save it as “CSV UTF-8” and try again.", "error")
                return redirect(url_for("import_view"))
            token = secrets.token_hex(12)
            (updir / f"{token}.csv").write_text(text, encoding="utf-8")
            reader = csv.DictReader(io.StringIO(text))
            headers = reader.fieldnames or []
            if not headers:
                flash("The file is empty.", "error")
                return redirect(url_for("import_view"))
            preview = [r for _, r in zip(range(5), reader)]
            return render_template("import_map.html", token=token, name=name, headers=headers, preview=preview,
                                   mapping=suggest_mapping(headers), required=REQUIRED, optional=OPTIONAL)
        token = request.form.get("token", "")
        if not re.fullmatch(r"[0-9a-f]{24}", token) or not (updir / f"{token}.csv").exists():
            flash("That upload has expired. Please upload the file again.", "error")
            return redirect(url_for("import_view"))
        text = (updir / f"{token}.csv").read_text(encoding="utf-8")
        mapping = {f: request.form.get(f"map_{f}", "") for f in REQUIRED + OPTIONAL}
        missing = [f for f in REQUIRED if not mapping[f]]
        if missing:
            flash(f"Map the required field(s): {', '.join(missing)}.", "error")
            reader = csv.DictReader(io.StringIO(text))
            return render_template("import_map.html", token=token, name=request.form.get("name"), headers=reader.fieldnames,
                                   preview=[r for _, r in zip(range(5), reader)], mapping=mapping, required=REQUIRED,
                                   optional=OPTIONAL), 400
        mapped = remap(text, mapping, request.form.get("date_order", "auto"))
        if step == "validate":
            res = import_csv(conn(), mapped, request.form.get("name", "upload.csv"), dry_run=True)
            (updir / f"{token}.errors.json").write_text(json.dumps(res.rejected))
            return render_template("import_validate.html", token=token, res=res, name=request.form.get("name"),
                                   mapping=mapping, date_order=request.form.get("date_order", "auto"),
                                   tz=request.form.get("tz", "Europe/London"))
        if step == "confirm":
            res = import_csv(conn(), mapped, request.form.get("name", "upload.csv"))
            analysed = None
            if res.accepted:
                analysed, _ = reanalyse()
            (updir / f"{token}.csv").unlink(missing_ok=True)
            return render_template("import_done.html", res=res, analysed=analysed)
        abort(400)

    @app.route("/import/errors/<token>.csv")
    @requires("import")
    def import_errors(token):
        p = upload_dir() / f"{token}.errors.json"
        if not re.fullmatch(r"[0-9a-f]{24}", token) or not p.exists():
            abort(404)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["line", "reason", "how_to_fix"])
        for line, why in json.loads(p.read_text()):
            fix = ("Use a date like 2026-09-01 09:30, or choose the right day/month order in the wizard."
                   if "timestamp" in why else "Fill in this required value." if "missing" in why else "Check the row.")
            w.writerow([line, why, fix])
        return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=import-errors.csv"})

    # ------------------------------------------------------------------ issue investigation
    @app.route("/issues/<int:issue_id>/notes", methods=["POST"])
    @requires("triage")
    def issue_notes(issue_id):
        row = conn().execute("SELECT key FROM issues WHERE issue_id=?", (issue_id,)).fetchone()
        if row is None:
            abort(404)
        desc, conf = request.form.get("description"), request.form.get("confirmed")
        conn().execute("INSERT OR IGNORE INTO issue_meta (key) VALUES (?)", (row["key"],))
        if desc is not None:
            conn().execute("UPDATE issue_meta SET description=? WHERE key=?", (desc.strip()[:2000], row["key"]))
        if conf is not None:
            conn().execute("UPDATE issue_meta SET confirmed=? WHERE key=?", (conf.strip()[:2000], row["key"]))
            conn().execute("INSERT INTO issue_comments (key, author, body) VALUES (?,?,?)",
                           (row["key"], session["user"], "· recorded what the team has confirmed"))
        conn().commit()
        flash("Saved.", "ok")
        return redirect(url_for("issue_detail", issue_id=issue_id))

    @app.route("/issues/<int:issue_id>/export.csv")
    @requires("export")
    def issue_export(issue_id):
        rows = conn().execute("SELECT ticket_id, created_at, channel, customer_tier, pred_category, pred_confidence, body "
                              "FROM tickets WHERE issue_id=? ORDER BY created_at", (issue_id,)).fetchall()
        if not rows:
            abort(404)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(rows[0].keys())
        w.writerows([tuple(r) for r in rows])
        return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=issue-{issue_id}.csv"})

    # ------------------------------------------------------------------ review queue: skip and undo
    @app.route("/review/<ticket_id>/skip", methods=["POST"])
    @requires("label")
    def review_skip(ticket_id):
        skipped = set(session.get("skipped", []))
        skipped.add(ticket_id)
        session["skipped"] = sorted(skipped)[-500:]
        return redirect(url_for("review"))

    @app.route("/review/<ticket_id>/undo", methods=["POST"])
    @requires("label")
    def review_undo(ticket_id):
        row = conn().execute("SELECT * FROM ticket_labels WHERE ticket_id=?", (ticket_id,)).fetchone()
        if row is None:
            abort(404)
        conn().execute("UPDATE tickets SET pred_category=?, pred_confidence=?, needs_review=1 WHERE ticket_id=?",
                       (row["prev_category"], row["prev_confidence"], ticket_id))
        conn().execute("DELETE FROM ticket_labels WHERE ticket_id=?", (ticket_id,))
        conn().commit()
        flash(f"Correction to {ticket_id} undone. It is back in the review queue.", "ok")
        return redirect(url_for("review"))

    @app.route("/checklist/dismiss", methods=["POST"])
    def checklist_dismiss():
        session["hide_checklist"] = True
        return ("", 204)

    # ------------------------------------------------------------------ assistant
    @app.route("/assistant/ask", methods=["POST"])
    def assistant_ask():
        from . import assistant
        from flask import jsonify
        # simple per-session rate limit: 30 questions per 10 minutes
        now = datetime.now().timestamp()
        recent = [t for t in session.get("ai_times", []) if now - t < 600]
        if len(recent) >= 30:
            return jsonify({"text": "You’ve asked a lot of questions in a short time. Please wait a few minutes.",
                            "sources": [], "actions": [], "suggestions": [], "provider": "built-in",
                            "escalate": {"label": "Talk to a person", "url": "/feedback"}}), 429
        session["ai_times"] = recent + [now]
        q = ((request.get_json(silent=True) or {}).get("q") or request.form.get("q") or "")[:500]
        role, plan = session.get("role"), session.get("plan")
        c = conn() if role else None                       # visitors get documentation answers only
        share = False
        if c is not None:
            row = c.execute("SELECT value FROM app_settings WHERE key='assistant_share_text'").fetchone()
            share = bool(row and row[0] == "1")
        reply = assistant.answer(q, conn=c, can=lambda p: can(role, p, plan), user=session.get("user") if role != "guest" else None,
                                 share_ticket_text=share)
        out = reply.as_dict()
        if not role:
            out["escalate"] = {"label": "Contact us", "url": "/contact"}
        return jsonify(out)

    app.jinja_env.globals.update(next_steps=NEXT_STEPS, severity_note=CATEGORY_SEVERITY_NOTE)
