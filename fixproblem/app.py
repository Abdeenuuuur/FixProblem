"""Flask web application."""
from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template, request, session,
                   url_for)

import hmac
import json
import os
import re
import secrets

from . import accounts, billing as pay, db, notify, pipeline
from .accounts import AccountError
from .auth import can, mask_email, requires
from .config import DEMO_USERS, EDITABLE, ROOT, Settings, settings as default_settings, validate_setting
from .ingest import ImportFormatError, import_csv
from .trends import weekly_counts

FMT = "%Y-%m-%d %H:%M"
STATUSES = ("new", "investigating", "resolved")
CATEGORY_COLOURS = {"connectivity": "#6366f1", "billing": "#0ea5e9", "account_access": "#10b981",
                    "cancellation": "#f59e0b", "delivery": "#ec4899", "technical_app": "#ef4444"}
PAGE_SIZE = 25


def create_app(cfg: Settings | None = None) -> Flask:
    from .config import check_production
    cfg = cfg or default_settings
    check_production(cfg)
    production = cfg.environment == "production"
    app = Flask(__name__)
    if production:
        app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.config.update(SECRET_KEY=cfg.secret_key, MAX_CONTENT_LENGTH=20 * 1024 * 1024, FIXPROBLEM=cfg)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def ws_id() -> int:
        return int(session.get("ws", accounts.DEMO_WORKSPACE))

    def ws_path(w: int) -> Path:
        """Database file for a workspace. Workspace 1 (shared demo) uses the main database."""
        return Path(cfg.db_path) if w == accounts.DEMO_WORKSPACE else Path(cfg.db_path).parent / "workspaces" / f"ws_{int(w)}.db"

    def pconn():
        """Platform database: accounts, workspaces, memberships, payments, activity, support."""
        if "pconn" not in g:
            g.pconn = db.connect(cfg.db_path)
        return g.pconn

    def conn():
        """The current workspace's own database. Customer records never share a file.
        The demo workspace lives in the platform database, so it shares that connection
        (two connections to one SQLite file would block each other's writes)."""
        if ws_id() == accounts.DEMO_WORKSPACE:
            return pconn()
        if "conn" not in g:
            g.conn = db.connect(ws_path(ws_id()))
        return g.conn

    @app.teardown_appcontext
    def close(_exc):
        for k in ("conn", "pconn"):
            c = g.pop(k, None)
            if c is not None:
                c.close()

    limiter = accounts.LoginLimiter()
    CSRF_EXEMPT = {"billing_webhook", "static", "healthz"}

    def csrf_token() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def csrf_protect():
        """Every state-changing request must carry the session's CSRF token (form field or header)."""
        if not cfg.csrf or request.method in ("GET", "HEAD", "OPTIONS") or request.endpoint in CSRF_EXEMPT:
            return None
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
        if not session.get("csrf") or not hmac.compare_digest(sent, session["csrf"]):
            return render_template("error.html", code=400,
                                   message="This form has expired. Please go back, refresh the page and try again."), 400
        return None

    @app.after_request
    def csrf_inject(resp):
        """Add the CSRF token to every POST form on HTML pages, so no form can forget it."""
        if cfg.csrf and resp.mimetype == "text/html" and not resp.direct_passthrough:
            html = resp.get_data(as_text=True)
            if 'method="post"' in html:
                field = f'<input type="hidden" name="csrf_token" value="{csrf_token()}">'
                html = re.sub(r'(<form\b[^>]*method="post"[^>]*>)', r"\1" + field, html)
                resp.set_data(html)
        return resp

    def current_user():
        if session.get("uid"):
            if "user_row" not in g:
                g.user_row = accounts.get_user(pconn(), session["uid"])
                if g.user_row is None:          # account deleted elsewhere
                    session.clear()
            return g.user_row
        return None

    def actor() -> str | None:
        if session.get("uid"):
            u = current_user()
            return u["email"] if u else None
        if session.get("role") == "guest":
            return f"guest:{session.get('guest_id')}"
        return session.get("user")

    @app.before_request
    def sync_plan():
        u = current_user()
        if u:
            session["plan"] = u["plan"]
            # server-side workspace check on every request: never trust the session alone
            if not accounts.membership(pconn(), u["id"], ws_id()):
                mine = accounts.user_workspaces(pconn(), u["id"])
                session["ws"] = mine[0]["id"] if mine else accounts.DEMO_WORKSPACE
                g.pop("conn", None)
        elif session.get("role"):
            session["ws"] = accounts.DEMO_WORKSPACE        # demo users and guests: shared sample only

    @app.after_request
    def track(resp):
        """Record every operation by a signed-in user or guest (see the privacy notice)."""
        if request.endpoint and request.endpoint != "static" and not request.path.startswith("/api/search"):
            who = actor()
            if who:
                action = request.form.get("action") or request.form.get("status") or ""
                try:
                    accounts.log_activity(pconn(), who, request.method, request.full_path.rstrip("?"),
                                          resp.status_code, action)
                except Exception:      # tracking must never break the product
                    pass
        return resp

    @app.context_processor
    def inject():
        role, plan = session.get("role"), session.get("plan")
        counts = {}
        if role:
            c = conn()
            counts = {"review": c.execute("SELECT COUNT(*) FROM tickets WHERE needs_review=1").fetchone()[0],
                      "alerts": c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]}
        u = current_user()
        trial_left = None
        if u and u["plan"] == "trial" and u["trial_ends"]:
            trial_left = max(0, (datetime.strptime(u["trial_ends"], accounts.FMT) - accounts.now()).days + 1)
        wsrow = accounts.workspace(pconn(), ws_id()) if role else None
        return {"role": role, "user": session.get("user"), "plan": plan, "account": u, "trial_left": trial_left,
                "ws": wsrow, "my_workspaces": accounts.user_workspaces(pconn(), u["id"]) if u else [],
                "can": lambda p: can(role, p, plan), "role_can": lambda p: can(role, p, "pro"),
                "counts": counts, "colours": CATEGORY_COLOURS, "users": list(DEMO_USERS),
                "show_tour": session.get("show_tour"), "price": accounts.gbp(accounts.PRO_PRICE_PENCE)}

    def start_session(user=None, demo=None, guest=False):
        session.clear()
        if user:
            mine = accounts.user_workspaces(pconn(), user["id"])
            session.update(uid=user["id"], user=user["name"].split()[0].lower(), role="owner", plan=user["plan"],
                           show_tour=not user["onboarded"], ws=mine[0]["id"] if mine else accounts.DEMO_WORKSPACE)
        elif demo:
            session.update(user=demo, role=DEMO_USERS[demo]["role"], plan="pro", show_tour=False)
        elif guest:
            session.update(user="guest", role="guest", plan="free", guest_id=accounts.new_guest_id(), show_tour=True)

    @app.template_filter("email")
    def email_filter(value):
        return value if can(session.get("role"), "view_pii") else mask_email(value)

    @app.template_filter("pretty")
    def pretty(value):
        return (value or "").replace("_", " ").capitalize()

    def as_of():
        row = conn().execute("SELECT MAX(created_at) FROM tickets").fetchone()[0]
        return datetime.strptime(row, FMT) if row else datetime.now()

    def spark(where: str, params: tuple) -> list[int]:
        stamps = [datetime.strptime(r[0], FMT) for r in conn().execute(
            f"SELECT created_at FROM tickets WHERE {where}", params)]
        return weekly_counts(stamps, as_of())

    def meta(key: str) -> dict:
        row = conn().execute("SELECT * FROM issue_meta WHERE key=?", (key,)).fetchone()
        return dict(row) if row else {"status": "new", "owner": None}

    def reanalyse():
        result = pipeline.run(conn(), cfg, ROOT / "instance" / "models" / f"ws_{ws_id()}.pkl")
        url = conn().execute("SELECT value FROM app_settings WHERE key='webhook_url'").fetchone()
        sent = notify.notify_new_alerts(conn(), url[0] if url else None)
        return result, sent

    # ---------- public ----------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            ip = request.remote_addr or "?"
            if limiter.blocked(ip):
                flash("Too many failed attempts. Wait 10 minutes and try again.", "error")
                return render_template("login.html", tab="signin"), 429
            ident, pw = request.form.get("username", "").strip().lower(), request.form.get("password", "")
            user = accounts.authenticate(pconn(), ident, pw) if "@" in ident else None
            demo = None if production else DEMO_USERS.get(ident)
            if user:
                start_session(user=user)
            elif demo and pw == demo["password"]:
                start_session(demo=ident)
            else:
                limiter.fail(ip)
                flash("Incorrect email or password.", "error")
                return render_template("login.html", tab="signin")
            limiter.reset(ip)
            accounts.log_activity(pconn(), actor(), "POST", "/login", 200, "sign in")
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        return render_template("login.html", tab=request.args.get("tab", "signin"))

    @app.route("/signup", methods=["POST"])
    def signup():
        f = request.form
        if not f.get("terms"):
            flash("Please accept the terms and privacy notice to create an account.", "error")
            return render_template("login.html", tab="signup", form=f), 400
        try:
            user = accounts.create_user(pconn(), f.get("email"), f.get("name"), f.get("password"), bool(f.get("marketing")),
                                        f.get("workspace", ""))
        except AccountError as exc:
            flash(str(exc), "error")
            return render_template("login.html", tab="signup", form=f), 400
        accounts.create_workspace(pconn(), f.get("workspace", "") or user.get("workspace") or "", user["id"])
        start_session(user=user)
        accounts.log_activity(pconn(), user["email"], "POST", "/signup", 200, "account created")
        flash(f"Welcome, {user['name'].split()[0]}! Explore the sample data or upload your tickets to get started.", "ok")
        return redirect(url_for("dashboard"))

    @app.route("/guest", methods=["POST"])
    def guest():
        start_session(guest=True)
        return redirect(url_for("dashboard"))

    @app.route("/workspace/switch", methods=["POST"])
    def workspace_switch():
        if not session.get("uid"):
            abort(403)
        try:
            target = int(request.form.get("workspace_id", ""))
        except ValueError:
            abort(400)
        if not accounts.membership(pconn(), session["uid"], target):      # guessed IDs are refused
            abort(403)
        session["ws"] = target
        g.pop("conn", None)
        return redirect(url_for("dashboard"))

    @app.route("/workspace/sample", methods=["POST"])
    def workspace_sample():
        """Create a SEPARATE sample workspace for this user: sample records never enter their own workspace."""
        if not session.get("uid"):
            return redirect(url_for("demo"))
        u = current_user()
        existing = [w for w in accounts.user_workspaces(pconn(), u["id"]) if w["kind"] == "sample"]
        if existing:
            session["ws"] = existing[0]["id"]
        else:
            new = accounts.create_workspace(pconn(), "Sample data (Northwind Broadband)", u["id"], kind="sample")
            session["ws"] = new
            g.pop("conn", None)
            import_csv(conn(), (ROOT / "data" / "sample" / "tickets.csv").read_bytes(), "sample tickets.csv")
            reanalyse()
        flash("You’re now in a separate sample workspace. Switch back to your own workspace from the menu at the bottom left.", "ok")
        return redirect(url_for("dashboard"))

    @app.route("/tour/done", methods=["POST"])
    def tour_done():
        session["show_tour"] = False
        if session.get("uid"):
            accounts.mark_onboarded(pconn(), session["uid"])
        return ("", 204)

    @app.route("/tour")
    def tour():
        session["show_tour"] = True
        return redirect(request.referrer or url_for("dashboard"))

    @app.route("/healthz")
    def healthz():
        try:
            conn().execute("SELECT 1").fetchone()
            return {"status": "ok", "database": "ok"}
        except Exception:
            return {"status": "error", "database": "unavailable"}, 503

    @app.route("/privacy")
    def privacy():
        return render_template("privacy.html")

    # ---------- billing ----------
    def need_account():
        if not session.get("uid"):
            flash("Create a free account to start a trial or subscribe.", "error")
            return redirect(url_for("login", tab="signup"))
        return None

    @app.route("/billing")
    def billing():
        u = current_user()
        amount, discount = accounts.price_for(u if u else {"signup_offer": 1, "offer_used": 0})
        pays = pconn().execute("SELECT * FROM payments WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall() if u else []
        return render_template("billing.html", amount=accounts.gbp(amount), discount=discount, payments=pays,
                               cancelled=accounts.is_cancelled(pconn(), u["id"]) if u else False,
                               stripe=pay.stripe_enabled(), gbp=accounts.gbp, trial_days=accounts.TRIAL_DAYS)

    @app.route("/billing/trial", methods=["POST"])
    def billing_trial():
        if (r := need_account()):
            return r
        try:
            u = accounts.start_trial(pconn(), current_user())
            session["plan"] = u["plan"]
            flash("Your 7-day Pro trial has started. Enjoy every feature!", "ok")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("billing"))

    @app.route("/billing/checkout", methods=["POST"])
    def billing_checkout():
        if (r := need_account()):
            return r
        u = current_user()
        amount, _ = accounts.price_for(u)
        if pay.stripe_enabled():
            try:
                return redirect(pay.create_checkout(u["id"], u["email"], amount, url_for("billing_success", _external=True),
                                                    url_for("billing", _external=True)), 303)
            except Exception as exc:
                flash(f"Payment provider error: {exc}", "error")
                return redirect(url_for("billing"))
        return redirect(url_for("billing_demo"))

    @app.route("/billing/demo", methods=["GET", "POST"])
    def billing_demo():
        if (r := need_account()):
            return r
        if pay.stripe_enabled() or production:          # simulated checkout never exists in production
            abort(404)
        u = current_user()
        amount, discount = accounts.price_for(u)
        if request.method == "POST":
            u = accounts.record_payment(pconn(), u, "demo", f"demo_{accounts.new_guest_id()}")
            session["plan"] = u["plan"]
            flash("Payment successful (demo). Welcome to Fixproblem Pro!", "ok")
            return redirect(url_for("billing"))
        return render_template("checkout_demo.html", amount=accounts.gbp(amount), discount=discount,
                               full=accounts.gbp(accounts.PRO_PRICE_PENCE))

    @app.route("/billing/success")
    def billing_success():
        if (r := need_account()):
            return r
        sid = request.args.get("session_id", "")
        u = current_user()
        amount, _ = accounts.price_for(u)
        if pconn().execute("SELECT 1 FROM payments WHERE reference=?", (sid,)).fetchone():
            flash("This payment has already been applied to your account.", "ok")
            return redirect(url_for("billing"))
        try:
            if not sid:
                raise pay.PaymentError("missing checkout reference")
            pay.check_session(pay.fetch_session(sid), u["id"], amount)   # paid, this account, this price
        except Exception as exc:
            app.logger.warning("checkout not applied: %s", exc)
            flash("We could not confirm that payment for this account. Nothing was activated; contact support if "
                  "you were charged.", "error")
            return redirect(url_for("billing"))
        u = accounts.record_payment(pconn(), u, "stripe", sid)
        session["plan"] = u["plan"]
        flash("Payment confirmed. Your Team pass is active for one month.", "ok")
        return redirect(url_for("billing"))

    @app.route("/billing/webhook", methods=["POST"])
    def billing_webhook():
        """Stripe webhook: the reliable record of payment, independent of the user's browser."""
        secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        if not secret:
            abort(404)
        try:
            event = pay.verify_webhook(request.get_data(), request.headers.get("Stripe-Signature", ""), secret)
        except Exception:
            return {"error": "invalid signature"}, 400
        db_ = pconn()
        if db_.execute("SELECT 1 FROM billing_events WHERE event_id=?", (event.get("id"),)).fetchone():
            return {"status": "duplicate"}, 200                   # each event at most once
        outcome = "ignored"
        if event.get("type") == "checkout.session.completed":
            s_ = event.get("data", {}).get("object", {})
            user = accounts.get_user(db_, int(s_["client_reference_id"])) if str(s_.get("client_reference_id", "")).isdigit() else None
            if user is None:
                outcome = "unknown account"
            elif db_.execute("SELECT 1 FROM payments WHERE reference=?", (s_.get("id"),)).fetchone():
                outcome = "already applied"
            else:
                try:
                    pay.check_session(s_, user["id"], accounts.price_for(user)[0])
                    accounts.record_payment(db_, user, "stripe", s_["id"])
                    outcome = "applied"
                except pay.PaymentError as exc:
                    outcome = f"rejected: {exc}"
        db_.execute("INSERT INTO billing_events (event_id, type, outcome) VALUES (?,?,?)",
                    (event.get("id"), event.get("type"), outcome))
        db_.commit()
        return {"status": outcome}, 200

    @app.route("/billing/cancel", methods=["POST"])
    def billing_cancel():
        if (r := need_account()):
            return r
        try:
            accounts.cancel(pconn(), current_user())
            flash("Renewal cancelled. Pro stays active until the end of your paid month.", "ok")
        except AccountError as exc:
            flash(str(exc), "error")
        return redirect(url_for("billing"))

    # ---------- account and data rights ----------
    @app.route("/account")
    def account():
        if (r := need_account()):
            return r
        return render_template("account.html")

    @app.route("/account/export")
    def account_export():
        if (r := need_account()):
            return r
        data = accounts.export_user_data(pconn(), session["uid"])
        data["workspaces"] = []
        for w in accounts.user_workspaces(pconn(), session["uid"]):        # everything stored in your workspaces
            if w["id"] == accounts.DEMO_WORKSPACE or not ws_path(w["id"]).exists():
                continue
            wc = db.connect(ws_path(w["id"]))
            dump = lambda sql: [dict(r) for r in wc.execute(sql)]
            data["workspaces"].append({"name": w["name"], "kind": w["kind"], "role": w["role"],
                                       "tickets": dump("SELECT * FROM tickets"),
                                       "issue_workflow": dump("SELECT * FROM issue_meta"),
                                       "comments": dump("SELECT * FROM issue_comments"),
                                       "label_corrections": dump("SELECT * FROM ticket_labels"),
                                       "imports": dump("SELECT * FROM import_log"),
                                       "settings": dump("SELECT key, value FROM app_settings")})
            wc.close()
        return Response(json.dumps(data, indent=2, default=str), mimetype="application/json",
                        headers={"Content-Disposition": "attachment; filename=fixproblem-my-data.json"})

    @app.route("/account/delete", methods=["POST"])
    def account_delete():
        if (r := need_account()):
            return r
        if request.form.get("confirm", "").strip().lower() != "delete":
            flash("Type DELETE to confirm.", "error")
            return redirect(url_for("account"))
        import shutil
        for w in accounts.user_workspaces(pconn(), session["uid"]):
            if w["owner_id"] == session["uid"] and w["id"] != accounts.DEMO_WORKSPACE:
                g.pop("conn", None)
                ws_path(w["id"]).unlink(missing_ok=True)                                   # tickets, issues, reports
                (ROOT / "instance" / "models" / f"ws_{w['id']}.pkl").unlink(missing_ok=True)   # trained model
                shutil.rmtree(ROOT / "instance" / "uploads" / f"ws_{w['id']}", ignore_errors=True)  # uploads, error reports
        accounts.delete_user(pconn(), session["uid"])
        session.clear()
        flash("Your account and personal data have been deleted.", "ok")
        return redirect(url_for("landing"))

    @app.route("/admin/analytics")
    @requires("platform")
    def analytics():
        return render_template("analytics.html", a=accounts.analytics(pconn()), gbp=accounts.gbp)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("landing"))

    @app.route("/")
    def landing():
        c = conn()
        top = c.execute("SELECT * FROM issues ORDER BY emerging DESC, priority DESC LIMIT 1").fetchone()
        preview = None
        if top:
            ex = c.execute("SELECT ticket_id, created_at, body FROM tickets WHERE issue_id=? ORDER BY created_at DESC LIMIT 3",
                           (top["issue_id"],)).fetchall()
            others = c.execute("SELECT label, category, size, priority, emerging FROM issues ORDER BY priority DESC LIMIT 5").fetchall()
            preview = {"issue": top, "examples": ex, "others": others, "spark": spark("issue_id=?", (top["issue_id"],))}
        from .help_content import FAQS
        return render_template("landing.html", preview=preview, faqs=FAQS)

    # ---------- dashboard ----------
    @app.route("/dashboard")
    @requires("view")
    def dashboard():
        c = conn()
        total = c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        if total == 0:
            return render_template("empty.html")
        end = as_of()
        weeks = 12
        series = defaultdict(lambda: [0] * weeks)
        for r in c.execute("SELECT created_at, pred_category FROM tickets"):
            age = (end - datetime.strptime(r[0], FMT)).days // 7
            if 0 <= age < weeks:
                series[r[1]][weeks - 1 - age] += 1
        labels = [(end - timedelta(weeks=weeks - 1 - i)).strftime("%d %b") for i in range(weeks)]
        alerts = [dict(a) | {"spark": spark(f"issue_id IN ({a['issue_ids']})", ())}
                  for a in c.execute("SELECT * FROM alerts ORDER BY growth DESC")]
        issues = [dict(i) | {"spark": spark("issue_id=?", (i["issue_id"],)), **meta(i["key"])}
                  for i in c.execute("SELECT * FROM issues ORDER BY priority DESC LIMIT 6")]
        last14 = c.execute("SELECT COUNT(*) FROM tickets WHERE created_at > ?",
                           ((end - timedelta(days=14)).strftime(FMT),)).fetchone()[0]
        prev14 = c.execute("SELECT COUNT(*) FROM tickets WHERE created_at > ? AND created_at <= ?",
                           ((end - timedelta(days=28)).strftime(FMT), (end - timedelta(days=14)).strftime(FMT))).fetchone()[0]
        resolved = c.execute("SELECT COUNT(*) FROM issue_meta m JOIN issues i ON i.key=m.key WHERE m.status='resolved'").fetchone()[0]
        n_issues = c.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        assigned = c.execute("SELECT COUNT(*) FROM issue_meta m JOIN issues i ON i.key=m.key WHERE m.owner IS NOT NULL").fetchone()[0]
        who = actor() or ""
        did = lambda path: pconn().execute("SELECT 1 FROM activity WHERE actor=? AND path LIKE ? LIMIT 1", (who, path)).fetchone() is not None
        checklist = [("Add data", c.execute("SELECT COUNT(*) FROM import_log").fetchone()[0] > 0, url_for("import_view")),
                     ("Review the import", did("/import%"), url_for("import_view")),
                     ("Explore the first analysis", did("/issues/%") or did("/alerts/%"), url_for("issues")),
                     ("Check uncertain classifications", c.execute("SELECT COUNT(*) FROM ticket_labels").fetchone()[0] > 0, url_for("review")),
                     ("Assign an issue", assigned > 0, url_for("issues")),
                     ("Export a report or invite a colleague", did("/reports/%") or did("/export%"), url_for("reports"))]
        return render_template("dashboard.html", total=total, alerts=alerts, issues=issues,
                               series=dict(series), labels=labels, as_of=end.strftime("%d %b %Y"),
                               last14=last14, prev14=prev14, change=(last14 - prev14) / prev14 if prev14 else 0,
                               n_issues=n_issues, resolved=resolved, unassigned=n_issues - assigned,
                               checklist=checklist if session.get("role") != "guest" else None,
                               hide_checklist=session.get("hide_checklist"))

    # ---------- inbox ----------
    @app.route("/inbox")
    @requires("view")
    def inbox():
        q = request.args.get("q", "").strip()
        cat, tier = request.args.get("category", ""), request.args.get("tier", "")
        review = request.args.get("review", "")
        page = max(int(request.args.get("page", 1) or 1), 1)
        where, params = [], []
        if q:
            ids = [r[0] for r in conn().execute(
                "SELECT ticket_id FROM tickets_fts WHERE tickets_fts MATCH ? LIMIT 5000", (_fts_query(q),))]
            where.append(f"ticket_id IN ({','.join('?' * len(ids)) or 'NULL'})")
            params += ids
        if cat:
            where.append("pred_category=?"); params.append(cat)
        if tier:
            where.append("customer_tier=?"); params.append(tier)
        if review:
            where.append("needs_review=1")
        sql = " WHERE " + " AND ".join(where) if where else ""
        total = conn().execute(f"SELECT COUNT(*) FROM tickets{sql}", params).fetchone()[0]
        rows = conn().execute(f"SELECT * FROM tickets{sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                              params + [PAGE_SIZE, (page - 1) * PAGE_SIZE]).fetchall()
        cats = [r[0] for r in conn().execute("SELECT DISTINCT pred_category FROM tickets ORDER BY 1")]
        return render_template("inbox.html", rows=rows, total=total, page=page, pages=max(1, -(-total // PAGE_SIZE)),
                               q=q, cat=cat, tier=tier, review=review, cats=cats)

    # ---------- issues ----------
    @app.route("/issues")
    @requires("view")
    def issues():
        view = request.args.get("view", "board")
        rows = [dict(i) | meta(i["key"]) for i in conn().execute("SELECT * FROM issues ORDER BY priority DESC")]
        columns = {s: [r for r in rows if r["status"] == s] for s in STATUSES}
        return render_template("issues.html", issues=rows, columns=columns, view=view)

    @app.route("/issues/<int:issue_id>")
    @requires("view")
    def issue_detail(issue_id: int):
        issue = conn().execute("SELECT * FROM issues WHERE issue_id=?", (issue_id,)).fetchone()
        if issue is None:
            abort(404)
        tickets = conn().execute("SELECT * FROM tickets WHERE issue_id=? ORDER BY created_at DESC LIMIT 100",
                                 (issue_id,)).fetchall()
        comments = conn().execute("SELECT * FROM issue_comments WHERE key=? ORDER BY id", (issue["key"],)).fetchall()
        conf = conn().execute("SELECT AVG(pred_confidence), MIN(created_at), MAX(created_at) FROM tickets WHERE issue_id=?",
                              (issue_id,)).fetchone()
        related = conn().execute("SELECT issue_id, label, size FROM issues WHERE category=? AND issue_id != ? "
                                 "ORDER BY priority DESC LIMIT 4", (issue["category"], issue_id)).fetchall()
        alert = conn().execute("SELECT * FROM alerts WHERE ','||issue_ids||',' LIKE ?", (f"%,{issue_id},%",)).fetchone()
        tiers = conn().execute("SELECT customer_tier, COUNT(*) n FROM tickets WHERE issue_id=? GROUP BY 1 ORDER BY n DESC",
                               (issue_id,)).fetchall()
        return render_template("issue.html", issue=issue, m=meta(issue["key"]), tickets=tickets, comments=comments,
                               spark=spark("issue_id=?", (issue_id,)), statuses=STATUSES, avg_conf=conf[0] or 0,
                               first_seen=conf[1], last_seen=conf[2], related=related, alert=alert, tiers=tiers,
                               severity=cfg.severity.get(issue["category"], 1.0))

    @app.route("/issues/<int:issue_id>/update", methods=["POST"])
    @requires("comment")
    def issue_update(issue_id: int):
        issue = conn().execute("SELECT key FROM issues WHERE issue_id=?", (issue_id,)).fetchone()
        if issue is None:
            abort(404)
        key, user = issue["key"], session["user"]
        status, owner = request.form.get("status"), request.form.get("owner")
        body = (request.form.get("comment") or "").strip()
        if status is not None or owner is not None:
            if not can(session["role"], "triage"):
                abort(403)
            if status is not None and status not in STATUSES:
                abort(400)
            if owner is not None and owner and owner not in DEMO_USERS:
                abort(400)
            m = meta(key)
            new_status = status if status is not None else m["status"]
            new_owner = (owner or None) if owner is not None else m["owner"]
            conn().execute("INSERT INTO issue_meta (key, status, owner, updated_at) VALUES (?,?,?,CURRENT_TIMESTAMP) "
                           "ON CONFLICT(key) DO UPDATE SET status=excluded.status, owner=excluded.owner, "
                           "updated_at=CURRENT_TIMESTAMP", (key, new_status, new_owner))
            changes = []
            if new_status != m["status"]:
                changes.append(f"changed status to {new_status}")
            if new_owner != m["owner"]:
                changes.append(f"assigned to {new_owner}" if new_owner else "removed the owner")
            if changes:
                conn().execute("INSERT INTO issue_comments (key, author, body) VALUES (?,?,?)",
                               (key, user, "· " + " and ".join(changes)))
                flash("Issue updated.", "ok")
        if body:
            if len(body) > 2000:
                flash("Comment is too long (2,000 characters max).", "error")
            else:
                conn().execute("INSERT INTO issue_comments (key, author, body) VALUES (?,?,?)", (key, user, body))
                flash("Comment added.", "ok")
        conn().commit()
        return redirect(url_for("issue_detail", issue_id=issue_id))

    @app.route("/alerts/<int:alert_id>")
    @requires("view")
    def alert_detail(alert_id: int):
        alert = conn().execute("SELECT * FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
        if alert is None:
            abort(404)
        ids = [int(x) for x in alert["issue_ids"].split(",")]
        marks = ",".join("?" * len(ids))
        tickets = conn().execute(f"SELECT * FROM tickets WHERE issue_id IN ({marks}) ORDER BY created_at DESC", ids).fetchall()
        members = conn().execute(f"SELECT * FROM issues WHERE issue_id IN ({marks})", ids).fetchall()
        return render_template("alert.html", alert=alert, tickets=tickets, members=members,
                               spark=spark(f"issue_id IN ({marks})", tuple(ids)))

    # ---------- human in the loop ----------
    @app.route("/review")
    @requires("view")
    def review():
        skipped = set(session.get("skipped", []))
        rows = [r for r in conn().execute("SELECT * FROM tickets WHERE needs_review=1 ORDER BY pred_confidence")
                if r["ticket_id"] not in skipped]
        cats = [r[0] for r in conn().execute("SELECT DISTINCT pred_category FROM tickets ORDER BY 1")]
        done = conn().execute("SELECT COUNT(*) FROM ticket_labels").fetchone()[0]
        recent = conn().execute("SELECT l.*, t.body FROM ticket_labels l JOIN tickets t ON t.ticket_id=l.ticket_id "
                                "ORDER BY l.at DESC LIMIT 5").fetchall()
        return render_template("review.html", tickets=rows, cats=cats, threshold=cfg.review_threshold, done=done,
                               recent=recent, skipped=len(skipped))

    @app.route("/review/<ticket_id>", methods=["POST"])
    @requires("label")
    def label_ticket(ticket_id: str):
        label = request.form.get("label", "")
        if conn().execute("SELECT 1 FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone() is None:
            abort(404)                                        # not in this workspace
        cats = {r[0] for r in conn().execute("SELECT DISTINCT pred_category FROM tickets")}
        if label not in cats:
            abort(400)
        prev = conn().execute("SELECT pred_category, pred_confidence FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
        old = conn().execute("SELECT prev_category, prev_confidence FROM ticket_labels WHERE ticket_id=?", (ticket_id,)).fetchone()
        prev_cat, prev_conf = (old[0], old[1]) if old else (prev[0], prev[1])
        conn().execute("INSERT OR REPLACE INTO ticket_labels (ticket_id, label, labelled_by, prev_category, prev_confidence) "
                       "VALUES (?,?,?,?,?)", (ticket_id, label, session["user"], prev_cat, prev_conf))
        conn().execute("UPDATE tickets SET pred_category=?, pred_confidence=1.0, needs_review=0 WHERE ticket_id=?",
                       (label, ticket_id))
        conn().commit()
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"ok": True, "ticket_id": ticket_id, "label": label})
        flash(f"{ticket_id} labelled as {label.replace('_', ' ')}. It will be used at the next retrain.", "ok")
        return redirect(url_for("review"))

    @app.route("/retrain", methods=["POST"])
    @requires("retrain")
    def retrain():
        result, sent = reanalyse()
        flash(f"Model retrained on {result['tickets']:,} tickets: {result['issues']} issues, "
              f"{result['alerts']} alert(s){f', {sent} sent to chat' if sent else ''}.", "ok")
        return redirect(request.referrer or url_for("dashboard"))

    # ---------- data ----------
    @app.route("/import", methods=["GET", "POST"])
    @requires("view")
    def import_view():
        result = error = None
        if request.method == "POST":
            if not can(session.get("role"), "import", session.get("plan")):
                abort(403)
            f = request.files.get("file")
            if request.form.get("sample") and ws_id() != accounts.DEMO_WORKSPACE:
                return redirect(url_for("workspace_sample"), code=307)     # never mix sample into customer data
            if request.form.get("sample"):
                data, name = (ROOT / "data" / "sample" / "tickets.csv").read_bytes(), "sample tickets.csv"
            elif not f or not f.filename:
                data = name = None
                error = "Choose a CSV file to upload."
            elif not f.filename.lower().endswith(".csv"):
                data = name = None
                error = "Only .csv files are accepted."
            else:
                data, name = f.read(), f.filename
            if data is not None:
                try:
                    result = import_csv(conn(), data, name)
                    if result.accepted:
                        reanalyse()
                except ImportFormatError as exc:
                    error = str(exc)
        log = conn().execute("SELECT * FROM import_log ORDER BY id DESC LIMIT 10").fetchall()
        return render_template("import.html", result=result, error=error, log=log)

    @app.route("/settings", methods=["GET", "POST"])
    @requires("settings")
    def settings_view():
        if request.method == "POST":
            if request.form.get("action") == "test_webhook":
                url = conn().execute("SELECT value FROM app_settings WHERE key='webhook_url'").fetchone()
                if not url or not url[0]:
                    flash("Save a webhook URL first.", "error")
                else:
                    ok, detail = notify.post(url[0], "Fixproblem test message: your alert webhook works.")
                    conn().execute("INSERT INTO notifications (channel, alert_key, ok, detail) VALUES (?,?,?,?)",
                                   ("webhook", "test", int(ok), detail))
                    conn().commit()
                    flash("Test message sent." if ok else f"Webhook failed: {detail}", "ok" if ok else "error")
                return redirect(url_for("settings_view"))
            try:
                for key in EDITABLE:
                    if key in request.form:
                        v = validate_setting(key, request.form[key])
                        conn().execute("INSERT OR REPLACE INTO app_settings VALUES (?,?)", (key, str(v)))
                conn().commit()
                flash("Settings saved. They apply from the next analysis; use “Retrain now” to apply them immediately.", "ok")
            except ValueError as exc:
                flash(str(exc).capitalize() + ".", "error")
            return redirect(url_for("settings_view"))
        saved = {r[0]: r[1] for r in conn().execute("SELECT key, value FROM app_settings")}
        values = {k: saved.get(k, "" if k == "webhook_url" else "0" if k == "assistant_share_text" else getattr(cfg, k))
                  for k in EDITABLE}
        from .assistant import provider_config
        ai_provider, ai_key, ai_model = provider_config()
        log = conn().execute("SELECT * FROM notifications ORDER BY id DESC LIMIT 10").fetchall()
        return render_template("settings.html", fields=EDITABLE, values=values, log=log,
                               ai_provider=ai_provider if ai_key else None, ai_model=ai_model)

    @app.route("/export/issues.csv")
    @requires("export")
    def export_issues():
        rows = conn().execute("SELECT i.*, m.status, m.owner FROM issues i LEFT JOIN issue_meta m ON m.key=i.key "
                              "ORDER BY priority DESC").fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(rows[0].keys() if rows else ["issue_id"])
        w.writerows([tuple(r) for r in rows])
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=issues.csv"})

    # ---------- API ----------
    @app.route("/api/issues")
    @requires("view")
    def api_issues():
        return jsonify([dict(r) | meta(r["key"]) for r in conn().execute("SELECT * FROM issues ORDER BY priority DESC")])

    @app.route("/api/issues/<int:issue_id>/tickets")
    @requires("view")
    def api_issue_tickets(issue_id: int):
        rows = conn().execute("SELECT ticket_id, created_at, customer_email, customer_tier, body, pred_category,"
                              " pred_confidence FROM tickets WHERE issue_id=?", (issue_id,)).fetchall()
        pii = can(session.get("role"), "view_pii")
        return jsonify([dict(r) | {"customer_email": r["customer_email"] if pii else mask_email(r["customer_email"])}
                        for r in rows])

    @app.route("/api/search")
    @requires("view")
    def api_search():
        q = request.args.get("q", "").strip()
        if len(q) < 2:
            return jsonify({"issues": [], "tickets": []})
        like = f"%{q}%"
        issues = [{"title": r["label"], "sub": r["category"].replace("_", " "), "url": url_for("issue_detail", issue_id=r["issue_id"])}
                  for r in conn().execute("SELECT * FROM issues WHERE label LIKE ? OR summary LIKE ? "
                                          "ORDER BY priority DESC LIMIT 5", (like, like))]
        tickets = [{"title": r["ticket_id"], "sub": r["body"][:90], "url": url_for("inbox", q=r["ticket_id"])}
                   for r in conn().execute("SELECT t.* FROM tickets_fts f JOIN tickets t ON t.ticket_id=f.ticket_id "
                                           "WHERE tickets_fts MATCH ? LIMIT 6", (_fts_query(q),))]
        return jsonify({"issues": issues, "tickets": tickets})

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("error.html", code=403, message="Your role does not have access to this page."), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404, message="That page or record does not exist."), 404

    @app.errorhandler(413)
    def too_large(_e):
        return render_template("error.html", code=413, message="File is larger than the 20 MB limit."), 413

    from .pages import register
    register(app, conn=conn, current_user=current_user, spark=spark, meta=meta, reanalyse=reanalyse,
             as_of=as_of, cfg=cfg, pconn=pconn, ws_id=ws_id)
    return app


def _fts_query(q: str) -> str:
    """Turn user text into a safe FTS5 prefix query (no operators can be injected)."""
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in q).split() if w]
    return " ".join(f'"{w}"*' for w in words[:8]) or '""'


if __name__ == "__main__":
    create_app().run(debug=False, port=5001)
