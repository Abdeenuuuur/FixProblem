"""Fixproblem features: public site, guided import, issue investigation, review controls,
reports, feedback and honest product claims."""
import io
import re
import tempfile
import unittest
from pathlib import Path

from tests.helpers import SAMPLE
from fixproblem import db, pipeline
from fixproblem.app import create_app
from fixproblem.config import LAUNCH_OFFER, Settings
from fixproblem.ingest import import_csv
from fixproblem.pages import remap, suggest_mapping

FIELDS = ["ticket_id", "created_at", "body", "subject", "channel", "customer_id", "customer_email", "customer_tier", "agent_tag"]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = Settings(db_path=Path(cls.tmp.name) / "f.db")
        conn = db.connect(cls.cfg.db_path)
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, cls.cfg)
        conn.close()
        cls.app = create_app(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def client(self, user=None):
        c = self.app.test_client()
        if user:
            c.post("/login", data={"username": user, "password": f"{user}-demo"})
        return c

    def db(self):
        return db.connect(self.cfg.db_path)


class PublicSiteTests(Base):
    def test_visitor_can_understand_and_inspect_without_registering(self):
        c = self.client()
        home = c.get("/").data
        self.assertIn(b"Turn customer complaints into", home)
        self.assertIn(b"SAMPLE DATA", home)
        self.assertIn(b"Evidence", home)
        c.get("/demo")
        self.assertEqual(c.get("/issues/1").status_code, 200)       # inspect an issue and its evidence

    def test_every_public_page_and_footer_link_works(self):
        c = self.client()
        page = c.get("/").data.decode()
        links = set(re.findall(r'href="(/[^"#]*)', page)) - {"/logout"}
        for path in links | {"/healthz", "/help/billing"}:
            self.assertIn(c.get(path).status_code, (200, 302), path)

    def test_no_fake_claims(self):
        c = self.client()
        status = c.get("/status").data.lower()
        self.assertNotIn(b"all systems operational", status)
        home = c.get("/").data.lower()
        for claim in [b"testimonial", b"trusted by", b"iso 27001", b"soc 2", b"live chat"]:
            self.assertNotIn(claim, home)
        self.assertIn(b"planned", home)                            # connectors labelled as future
        self.assertFalse(LAUNCH_OFFER["enabled"])
        self.assertIn(b"no promotional offers", c.get("/pricing").data)

    def test_evaluation_page_labels_synthetic_results(self):
        self.assertIn(b"SYNTHETIC BENCHMARK", self.client().get("/evaluation").data)

    def test_contact_form_returns_reference(self):
        c = self.client()
        self.assertEqual(c.post("/contact", data={"email": "bad", "message": "short"}).status_code, 400)
        r = c.post("/contact", data={"email": "a@example.com", "message": "We would like a pilot please", "kind": "sales"})
        ref = re.search(rb"FP-[0-9A-F]{6}", r.data).group(0).decode()
        self.assertEqual(self.db().execute("SELECT kind FROM support_requests WHERE ref=?", (ref,)).fetchone()[0], "sales")

    def test_help_search(self):
        r = self.client().get("/help?q=timezone")
        self.assertIn(b"Preparing a CSV", r.data)
        self.assertNotIn(b"Managing team access", r.data)


class ImportWizardTests(Base):
    def test_column_suggestions(self):
        m = suggest_mapping(["Case ID", "Opened", "Message", "Requester email", "Tag"])
        self.assertEqual(m["ticket_id"], "Case ID")
        self.assertEqual(m["created_at"], "Opened")
        self.assertEqual(m["body"], "Message")
        self.assertEqual(m["customer_email"], "Requester email")

    def test_day_month_order_conversion(self):
        out = remap("id,date,msg\nA1,03/04/2026 10:00,hi\n", {"ticket_id": "id", "created_at": "date", "body": "msg"}, "dmy")
        self.assertIn("2026-04-03 10:00", out)
        out = remap("id,date,msg\nA1,03/04/2026 10:00,hi\n", {"ticket_id": "id", "created_at": "date", "body": "msg"}, "mdy")
        self.assertIn("2026-03-04 10:00", out)

    def run_wizard(self, csv_text):
        c = self.client("admin")
        r = c.post("/import/wizard", data={"step": "upload", "file": (io.BytesIO(csv_text.encode()), "my.csv")},
                   content_type="multipart/form-data")
        token = re.search(rb'name="token" value="([0-9a-f]+)"', r.data).group(1).decode()
        return c, token

    def test_validate_is_a_dry_run_and_explains_rejections(self):
        before = self.db().execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        c, token = self.run_wizard("Case ID,Opened,Message\nN1,2026-09-01 10:00,Router broken\nN2,not a date,Hi\nN3,2026-09-02 10:00,\n")
        m = {"map_ticket_id": "Case ID", "map_created_at": "Opened", "map_body": "Message"}
        r = c.post("/import/wizard", data={"step": "validate", "token": token, "name": "my.csv", **m})
        self.assertIn(b"Rejected rows", r.data)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM tickets").fetchone()[0], before)   # nothing written
        report = c.get(f"/import/errors/{token}.csv").data.decode()
        self.assertIn("unrecognised timestamp", report)
        self.assertIn("how_to_fix", report)
        r = c.post("/import/wizard", data={"step": "confirm", "token": token, "name": "my.csv", **m})
        self.assertIn(b"Data imported", r.data)
        self.assertIn(b"Analysis complete", r.data)
        self.assertEqual(self.db().execute("SELECT COUNT(*) FROM tickets").fetchone()[0], before + 1)

    def test_required_mapping_and_expired_token(self):
        c, token = self.run_wizard("a,b,c\n1,2026-09-01 10:00,x\n")
        r = c.post("/import/wizard", data={"step": "validate", "token": token, "name": "x.csv", "map_ticket_id": "a"})
        self.assertEqual(r.status_code, 400)
        r = c.post("/import/wizard", data={"step": "validate", "token": "0" * 24, "name": "x.csv"}, follow_redirects=True)
        self.assertIn(b"expired", r.data)
        r = c.post("/import/wizard", data={"step": "validate", "token": "../../etc", "name": "x.csv"}, follow_redirects=True)
        self.assertIn(b"expired", r.data)

    def test_import_requires_permission(self):
        self.assertEqual(self.client("analyst").post("/import/wizard", data={"step": "upload", "sample": "1"}).status_code, 403)
        g = self.client(); g.get("/demo")
        self.assertEqual(g.get("/import").status_code, 200)            # page explains, but cannot import
        self.assertEqual(g.post("/import", data={"sample": "1"}).status_code, 403)


class InvestigationTests(Base):
    def test_issue_shows_data_suggestion_and_confirmation_separately(self):
        r = self.client("analyst").get("/issues/1").data
        for part in [b"What the data shows", b"What Fixproblem suggests", b"What the team has confirmed", b"Priority"]:
            self.assertIn(part, r)

    def test_confirmed_notes_require_triage_and_persist(self):
        self.assertEqual(self.client("agent").post("/issues/1/notes", data={"confirmed": "x"}).status_code, 403)
        self.client("analyst").post("/issues/1/notes", data={"confirmed": "Root cause: v5.2 login bug", "description": "Crash on login"})
        conn = self.db()
        key = conn.execute("SELECT key FROM issues WHERE issue_id=1").fetchone()[0]
        pipeline.run(conn, self.cfg)                                       # survives re-analysis
        row = conn.execute("SELECT confirmed, description FROM issue_meta WHERE key=?", (key,)).fetchone()
        self.assertEqual(tuple(row), ("Root cause: v5.2 login bug", "Crash on login"))

    def test_review_undo_restores_previous_prediction(self):
        conn = self.db()
        tid, cat, conf = conn.execute("SELECT ticket_id, pred_category, pred_confidence FROM tickets WHERE needs_review=1 LIMIT 1").fetchone()
        other = "billing" if cat != "billing" else "delivery"
        c = self.client("agent")
        c.post(f"/review/{tid}", data={"label": other})
        c.post(f"/review/{tid}", data={"label": "cancellation"})              # second correction keeps the original
        c.post(f"/review/{tid}/undo")
        row = self.db().execute("SELECT pred_category, pred_confidence, needs_review FROM tickets WHERE ticket_id=?", (tid,)).fetchone()
        self.assertEqual((row[0], round(row[1], 3), row[2]), (cat, round(conf, 3), 1))

    def test_review_skip(self):
        c = self.client("agent")
        tid = self.db().execute("SELECT ticket_id FROM tickets WHERE needs_review=1 LIMIT 1").fetchone()[0]
        c.post(f"/review/{tid}/skip")
        self.assertNotIn(tid.encode(), c.get("/review").data)


class ReportTests(Base):
    def test_csv_and_pdf_reflect_selected_dates(self):
        c = self.client("analyst")
        csv_text = c.get("/reports/export.csv?from=2026-08-01&to=2026-08-31").data.decode()
        self.assertIn("2026-08-01 to 2026-08-31", csv_text)
        total = self.db().execute("SELECT COUNT(*) FROM tickets WHERE substr(created_at,1,10) BETWEEN '2026-08-01' AND '2026-08-31'").fetchone()[0]
        self.assertIn(f"tickets {total}", csv_text)
        pdf = c.get("/reports/report.pdf?from=2026-08-01&to=2026-08-31")
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_before_after_is_an_observation(self):
        r = self.client("analyst").get("/reports?issue=1&date=2026-08-01").data
        self.assertIn(b"not proof", r)

    def test_exports_need_permission(self):
        self.assertEqual(self.client("agent").get("/reports/export.csv").status_code, 403)


class FeedbackTests(Base):
    def test_feedback_reference_and_consent_for_diagnostics(self):
        c = self.client("agent")
        r = c.post("/feedback", data={"category": "Import problem", "description": "The wizard rejected my dates"})
        ref = re.search(rb"FP-[0-9A-F]{6}", r.data).group(0).decode()
        row = self.db().execute("SELECT diagnostics FROM support_requests WHERE ref=?", (ref,)).fetchone()
        self.assertIsNone(row[0])                                          # nothing attached without permission
        r = c.post("/feedback", data={"category": "Other", "description": "Please attach my details", "diagnostics": "1", "page": "/inbox"})
        ref = re.search(rb"FP-[0-9A-F]{6}", r.data).group(0).decode()
        diag = self.db().execute("SELECT diagnostics FROM support_requests WHERE ref=?", (ref,)).fetchone()[0]
        self.assertIn("/inbox", diag)
        self.assertNotIn("crash", diag.lower())                            # no ticket text

    def test_screenshot_validation(self):
        c = self.client("agent")
        r = c.post("/feedback", data={"description": "With a fake image file", "screenshot": (io.BytesIO(b"not an image"), "x.png")},
                   content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
