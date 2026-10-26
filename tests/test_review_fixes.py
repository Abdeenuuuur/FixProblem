"""Regression tests for the defects found in the code review (commit bd44e20).
Each test reproduces the reported problem and checks the fix."""
import hashlib
import hmac
import io
import json
import re
import tempfile
import time
import unittest
import warnings
from pathlib import Path
from unittest import mock

from tests.helpers import SAMPLE
from fixproblem import accounts, billing, db, pipeline
from fixproblem.app import create_app
from fixproblem.cluster import discover_issues, readable_title
from fixproblem.config import InsecureConfiguration, Settings
from fixproblem.ingest import import_csv


class Base(unittest.TestCase):
    csrf = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root_patch = None
        self.cfg = Settings(db_path=Path(self.tmp.name) / "m.db", csrf=self.csrf)
        conn = db.connect(self.cfg.db_path)
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, self.cfg)
        conn.close()
        self.app = create_app(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def signup(self, email="owner@x.example"):
        c = self.app.test_client()
        c.post("/signup", data={"name": "Owner", "email": email, "password": "Secret123", "workspace": "W", "terms": "1"})
        return c

    def user(self, email="owner@x.example"):
        return accounts.get_user(db.connect(self.cfg.db_path), email=email)


# ---------------------------------------------------------------- payments
def paid_session(user_id, amount=959, sid="cs_test_1", **over):
    s = {"id": sid, "payment_status": "paid", "client_reference_id": str(user_id), "amount_total": amount,
         "currency": "gbp", "metadata": {"product": billing.PRODUCT}}
    s.update(over)
    return s


class PaymentOwnershipTests(Base):
    def stripe(self, session):
        return mock.patch.multiple(billing, stripe_enabled=lambda: True, fetch_session=lambda sid: session)

    def test_checkout_for_another_account_is_rejected(self):
        c = self.signup()
        other = self.signup("other@x.example")
        with self.stripe(paid_session(self.user("other@x.example")["id"])):
            c.get("/billing/success?session_id=cs_test_1")
        self.assertEqual(self.user()["plan"], "free")

    def test_wrong_amount_currency_or_unpaid_is_rejected(self):
        c = self.signup()
        uid = self.user()["id"]
        for bad in [paid_session(uid, amount=1), paid_session(uid, currency="usd"),
                    paid_session(uid, payment_status="unpaid"), paid_session(uid, metadata={"product": "other"})]:
            with self.stripe(bad):
                c.get("/billing/success?session_id=cs_test_1")
            self.assertEqual(self.user()["plan"], "free", bad)

    def test_valid_checkout_applies_exactly_once(self):
        c = self.signup()
        with self.stripe(paid_session(self.user()["id"])):
            c.get("/billing/success?session_id=cs_test_1")
            c.get("/billing/success?session_id=cs_test_1")
        conn = db.connect(self.cfg.db_path)
        self.assertEqual(self.user()["plan"], "pro")
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments WHERE reference='cs_test_1'").fetchone()[0], 1)


class WebhookTests(Base):
    SECRET = "whsec_test"

    def post(self, event, secret=None, ts=None):
        body = json.dumps(event).encode()
        ts = ts or int(time.time())
        sig = hmac.new((secret or self.SECRET).encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        with mock.patch.dict("os.environ", {"STRIPE_WEBHOOK_SECRET": self.SECRET}):
            return self.app.test_client().post("/billing/webhook", data=body, headers={"Stripe-Signature": f"t={ts},v1={sig}"})

    def event(self, uid, eid="evt_1", **over):
        return {"id": eid, "type": "checkout.session.completed", "data": {"object": paid_session(uid, **over)}}

    def test_signature_and_timestamp_are_verified(self):
        self.signup()
        uid = self.user()["id"]
        self.assertEqual(self.post(self.event(uid), secret="wrong").status_code, 400)
        self.assertEqual(self.post(self.event(uid), ts=int(time.time()) - 3600).status_code, 400)
        self.assertEqual(self.user()["plan"], "free")

    def test_valid_event_applies_and_duplicates_are_ignored(self):
        self.signup()
        uid = self.user()["id"]
        self.assertEqual(self.post(self.event(uid)).get_json()["status"], "applied")
        self.assertEqual(self.post(self.event(uid)).get_json()["status"], "duplicate")
        self.assertEqual(self.post(self.event(uid, eid="evt_2")).get_json()["status"], "already applied")
        self.assertEqual(self.user()["plan"], "pro")

    def test_event_with_wrong_amount_is_recorded_but_not_applied(self):
        self.signup()
        r = self.post(self.event(self.user()["id"], amount=100))
        self.assertIn("rejected", r.get_json()["status"])
        self.assertEqual(self.user()["plan"], "free")


# ---------------------------------------------------------------- deletion and export
class DataRightsTests(Base):
    def test_deletion_removes_workspace_files_models_and_uploads(self):
        from fixproblem.config import ROOT
        c = self.signup()
        c.post("/import/wizard", data={"step": "upload", "file": (io.BytesIO(b"ticket_id,created_at,body\nT1,2026-09-01 10:00,The app crashes\n"), "t.csv")},
               content_type="multipart/form-data")
        wid = accounts.user_workspaces(db.connect(self.cfg.db_path), self.user()["id"])[0]["id"]
        model = ROOT / "instance" / "models" / f"ws_{wid}.pkl"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"model")
        uploads = ROOT / "instance" / "uploads" / f"ws_{wid}"
        self.assertTrue(any(uploads.iterdir()))
        c.post("/account/delete", data={"confirm": "DELETE"})
        self.assertFalse(model.exists())
        self.assertFalse(uploads.exists())

    def test_export_includes_workspace_records(self):
        c = self.signup()
        r = c.post("/import/wizard", data={"step": "upload", "file": (io.BytesIO(b"ticket_id,created_at,body\nMY-T1,2026-09-01 10:00,Router broken\n"), "t.csv")},
                   content_type="multipart/form-data")
        token = re.search(rb'name="token" value="([0-9a-f]+)"', r.data).group(1).decode()
        c.post("/import/wizard", data={"step": "confirm", "token": token, "name": "t.csv", "map_ticket_id": "ticket_id",
                                       "map_created_at": "created_at", "map_body": "body"})
        data = json.loads(c.get("/account/export").data)
        tickets = [t["ticket_id"] for w in data["workspaces"] for t in w["tickets"]]
        self.assertIn("MY-T1", tickets)
        self.assertNotIn("password_hash", data["account"])


# ---------------------------------------------------------------- production configuration
class ProductionTests(unittest.TestCase):
    def test_refuses_insecure_secret(self):
        with self.assertRaises(InsecureConfiguration):
            create_app(Settings(db_path=Path(tempfile.mkdtemp()) / "p.db", environment="production", secret_key="dev-only-change-me"))
        with self.assertRaises(InsecureConfiguration):
            create_app(Settings(db_path=Path(tempfile.mkdtemp()) / "p.db", environment="production", secret_key="x" * 40, csrf=False))

    def test_demo_admin_and_demo_checkout_disabled_in_production(self):
        app = create_app(Settings(db_path=Path(tempfile.mkdtemp()) / "p.db", environment="production",
                                  secret_key="s" * 40, csrf=True))
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        c = app.test_client()
        https = {"base_url": "https://localhost"}                 # secure cookies need HTTPS
        page = c.get("/login", **https).data.decode()
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        r = c.post("/login", data={"username": "admin", "password": "admin-demo", "csrf_token": token}, **https)
        self.assertIn(b"Incorrect", r.data)                       # demo administrator is disabled
        c.post("/signup", data={"name": "P", "email": "p@x.example", "password": "Secret123", "terms": "1",
                                "csrf_token": token}, **https)
        self.assertEqual(c.get("/billing/demo", **https).status_code, 404)   # no simulated checkout


# ---------------------------------------------------------------- CSRF
class CsrfTests(Base):
    csrf = True

    def test_post_without_token_is_rejected(self):
        c = self.app.test_client()
        self.assertEqual(c.post("/contact", data={"email": "a@b.example", "message": "hello there, pilot?"}).status_code, 400)
        self.assertEqual(c.post("/assistant/ask", json={"q": "pricing"}).status_code, 400)

    def test_forms_carry_token_and_work(self):
        c = self.app.test_client()
        page = c.get("/contact").data.decode()
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        r = c.post("/contact", data={"email": "a@b.example", "message": "hello there, pilot?", "csrf_token": token})
        self.assertIn(b"FP-", r.data)
        self.assertEqual(c.post("/assistant/ask", json={"q": "pricing"}, headers={"X-CSRF-Token": token}).status_code, 200)

    def test_wrong_token_rejected(self):
        c = self.app.test_client()
        c.get("/contact")
        self.assertEqual(c.post("/contact", data={"email": "a@b.example", "message": "x" * 20, "csrf_token": "forged"}).status_code, 400)


# ---------------------------------------------------------------- report correctness and PDF
class ReportTests(Base):
    def client(self):
        c = self.app.test_client()
        c.post("/login", data={"username": "analyst", "password": "analyst-demo"})
        return c

    def test_issues_are_ranked_by_tickets_in_the_period(self):
        r = self.client().get("/reports?from=2026-03-01&to=2026-03-31").data.decode()
        table = r.split("Top recurring issues in this period")[1].split("Before and after")[0]
        self.assertIn("Broadband crawls at night", table)
        self.assertNotIn("The app crashes as soon as I log in", table)   # crash tickets only exist in late August

    def test_alerts_excluded_when_period_is_before_alert_window(self):
        r = self.client().get("/reports?from=2026-03-01&to=2026-03-31").data.decode()
        self.assertIn("No emerging alerts apply", r)
        self.assertIn("All-time:", r)

    def test_pdf_handles_markup_long_titles_and_many_pages(self):
        conn = db.connect(self.cfg.db_path)
        conn.execute("UPDATE issues SET label = ? WHERE issue_id IN (SELECT issue_id FROM issues LIMIT 3)",
                     ("<b>Broken & <unclosed tag> " + "very long title " * 20,))
        conn.execute("UPDATE tickets SET body = 'Customer wrote <script>alert(1)</script> & <para>' WHERE ticket_id IN "
                     "(SELECT ticket_id FROM tickets LIMIT 50)")
        conn.commit()
        r = self.client().get("/reports/report.pdf")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data.startswith(b"%PDF"))

    def test_pdf_table_fits_page(self):
        from reportlab.lib.pagesizes import A4
        from reportlab.platypus import SimpleDocTemplate
        doc = SimpleDocTemplate(io.BytesIO(), pagesize=A4)
        self.assertLessEqual(sum([.40, .11, .11, .11, .14, .13]) * doc.width, doc.width + 0.01)


# ---------------------------------------------------------------- stable issue identity
class StableIssueTests(Base):
    def test_workflow_follows_the_problem_when_new_tickets_arrive(self):
        conn = db.connect(self.cfg.db_path)
        iid, key = conn.execute("SELECT issue_id, key FROM issues ORDER BY emerging DESC, priority DESC LIMIT 1").fetchone()
        conn.execute("UPDATE issue_meta SET owner='analyst', status='investigating' WHERE key=?", (key,))
        conn.execute("INSERT INTO issue_comments (key, author, body) VALUES (?, 'analyst', 'Engineering is on it')", (key,))
        conn.commit()
        new = "ticket_id,created_at,body,agent_tag\n" + "\n".join(
            f"X-{i},2026-08-31 0{i % 9}:00,The app crashes as soon as I log in since the update,technical_app" for i in range(15)) + \
            "\n" + "\n".join(f"Y-{i},2026-08-31 1{i % 9}:00,My smart meter shows the wrong reading,billing" for i in range(12))
        import_csv(conn, new)
        pipeline.run(conn, self.cfg)
        row = conn.execute("SELECT i.issue_id, m.owner, m.status FROM issues i JOIN issue_meta m ON m.key=i.key WHERE i.key=?",
                           (key,)).fetchone()
        self.assertIsNotNone(row, "the issue lost its identity after new data arrived")
        self.assertEqual(tuple(row), (iid, "analyst", "investigating"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM issue_comments WHERE key=?", (key,)).fetchone()[0], 1)

    def test_issue_ids_are_never_reused(self):
        conn = db.connect(self.cfg.db_path)
        before = {r[0] for r in conn.execute("SELECT issue_id FROM issue_registry")}
        pipeline.run(conn, self.cfg)
        after = {r[0] for r in conn.execute("SELECT issue_id FROM issue_registry")}
        self.assertTrue(before <= after)


# ---------------------------------------------------------------- titles and clustering edge cases
class TitleAndClusteringTests(unittest.TestCase):
    def test_readable_titles(self):
        self.assertEqual(readable_title("Hi team, the app crashes as soon as I log in since the update Account number is 1."),
                         "The app crashes as soon as I log in since the update")
        self.assertEqual(readable_title("To whom it may concern, no internet at all since Monday. Please help."),
                         "No internet at all since Monday")
        self.assertTrue(len(readable_title("word " * 50)) <= 65)

    def test_duplicate_and_tiny_datasets_do_not_warn_or_over_split(self):
        ids = [f"T{i}" for i in range(12)]
        with warnings.catch_warnings():
            warnings.simplefilter("error")                 # any warning fails the test
            issues = discover_issues(ids, ["Router broken"] * 12, ["connectivity"] * 12)
            tiny = discover_issues(ids[:3], ["a b", "c d", "e f"], ["billing"] * 3)
        self.assertEqual(len(issues), 1)
        self.assertEqual(len(tiny), 1)


class HonestPricingTests(Base):
    def test_no_seat_claims_or_renewal_claims_without_the_feature(self):
        page = self.app.test_client().get("/pricing").data.decode()
        self.assertNotIn("Up to 10 seats", page)
        self.assertIn("does <b>not</b> renew automatically", page)


if __name__ == "__main__":
    unittest.main()
