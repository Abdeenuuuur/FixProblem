"""Tests for the product features: search, issue workflow, human labelling, settings and alerts."""
import tempfile
import unittest
from pathlib import Path

from tests.helpers import SAMPLE
from fixproblem import db, notify, pipeline
from fixproblem.app import _fts_query, create_app
from fixproblem.config import Settings, validate_setting, with_overrides
from fixproblem.ingest import import_csv


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Settings(db_path=Path(self.tmp.name) / "t.db")
        conn = db.connect(self.cfg.db_path)
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, self.cfg)
        conn.close()
        self.app = create_app(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, user):
        c = self.app.test_client()
        c.post("/login", data={"username": user, "password": f"{user}-demo"})
        return c

    def db(self):
        return db.connect(self.cfg.db_path)


class SearchTests(Base):
    def test_search_finds_tickets_and_issues(self):
        data = self.client("agent").get("/api/search?q=crash").get_json()
        self.assertTrue(data["tickets"])
        self.assertTrue(any("crash" in i["title"] for i in data["issues"]))

    def test_search_input_cannot_inject_fts_syntax(self):
        self.assertEqual(_fts_query('app" OR body:*'), '"app"* "OR"* "body"*')
        r = self.client("agent").get('/inbox?q=" NEAR( ) *')
        self.assertEqual(r.status_code, 200)

    def test_inbox_filters_and_pagination(self):
        c = self.client("agent")
        self.assertIn(b"Page 1 of", c.get("/inbox").data)
        r = c.get("/inbox?category=billing&tier=business")
        self.assertEqual(r.status_code, 200)
        rows = r.data.split(b"<tbody>")[1]
        self.assertIn(b"</span>Billing</span>", rows)
        self.assertNotIn(b"</span>Connectivity</span>", rows)


class WorkflowTests(Base):
    def test_agent_can_comment_but_not_triage(self):
        c = self.client("agent")
        c.post("/issues/1/update", data={"comment": "Seen this on the phones today"})
        self.assertEqual(c.post("/issues/1/update", data={"status": "resolved"}).status_code, 403)
        conn = self.db()
        key = conn.execute("SELECT key FROM issues WHERE issue_id=1").fetchone()[0]
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM issue_comments WHERE key=?", (key,)).fetchone()[0], 1)

    def test_analyst_changes_status_and_owner_with_history(self):
        c = self.client("analyst")
        c.post("/issues/1/update", data={"status": "investigating"})
        c.post("/issues/1/update", data={"owner": "analyst"})
        conn = self.db()
        key = conn.execute("SELECT key FROM issues WHERE issue_id=1").fetchone()[0]
        m = conn.execute("SELECT status, owner FROM issue_meta WHERE key=?", (key,)).fetchone()
        self.assertEqual(tuple(m), ("investigating", "analyst"))
        log = [r[0] for r in conn.execute("SELECT body FROM issue_comments WHERE key=?", (key,))]
        self.assertTrue(any("investigating" in b for b in log))

    def test_invalid_status_or_owner_rejected(self):
        c = self.client("analyst")
        self.assertEqual(c.post("/issues/1/update", data={"status": "done"}).status_code, 400)
        self.assertEqual(c.post("/issues/1/update", data={"owner": "mallory"}).status_code, 400)

    def test_status_survives_reanalysis(self):
        self.client("analyst").post("/issues/1/update", data={"status": "resolved"})
        conn = self.db()
        key = conn.execute("SELECT key FROM issues WHERE issue_id=1").fetchone()[0]
        pipeline.run(conn, self.cfg)
        self.assertEqual(conn.execute("SELECT status FROM issue_meta WHERE key=?", (key,)).fetchone()[0], "resolved")


class LabellingTests(Base):
    def test_human_label_is_final_and_used_in_training(self):
        conn = self.db()
        tid = conn.execute("SELECT ticket_id FROM tickets WHERE needs_review=1 LIMIT 1").fetchone()[0]
        r = self.client("agent").post(f"/review/{tid}", data={"label": "billing"}, headers={"X-Requested-With": "fetch"})
        self.assertEqual(r.get_json()["label"], "billing")
        pipeline.run(conn, self.cfg)                      # retrain: the correction must stick
        row = conn.execute("SELECT pred_category, needs_review FROM tickets WHERE ticket_id=?", (tid,)).fetchone()
        self.assertEqual(tuple(row), ("billing", 0))

    def test_unknown_label_or_ticket_rejected(self):
        c = self.client("agent")
        self.assertEqual(c.post("/review/NWB-00001", data={"label": "made_up"}).status_code, 400)
        self.assertEqual(c.post("/review/NOPE", data={"label": "billing"}).status_code, 404)

    def test_only_admin_can_retrain(self):
        self.assertEqual(self.client("analyst").post("/retrain").status_code, 403)
        self.assertEqual(self.client("admin").post("/retrain").status_code, 302)


class SettingsAndAlertTests(Base):
    def test_validation(self):
        self.assertEqual(validate_setting("review_threshold", "0.7"), 0.7)
        for key, bad in [("review_threshold", "2"), ("recent_days", "abc"), ("webhook_url", "http://insecure"),
                         ("nope", "1")]:
            with self.assertRaises(ValueError):
                validate_setting(key, bad)

    def test_saved_settings_change_the_analysis(self):
        self.client("admin").post("/settings", data={"review_threshold": "0.9"})
        conn = self.db()
        self.assertEqual(with_overrides(conn, self.cfg).review_threshold, 0.9)
        before = conn.execute("SELECT COUNT(*) FROM tickets WHERE needs_review=1").fetchone()[0]
        pipeline.run(conn, self.cfg)
        after = conn.execute("SELECT COUNT(*) FROM tickets WHERE needs_review=1").fetchone()[0]
        self.assertGreater(after, before)

    def test_settings_admin_only(self):
        self.assertEqual(self.client("analyst").get("/settings").status_code, 403)

    def test_alerts_sent_once_and_failures_logged(self):
        conn = self.db()
        sent = []
        self.assertEqual(notify.notify_new_alerts(conn, "https://example.test/hook", lambda u, p, t: sent.append(p)), 1)
        self.assertEqual(notify.notify_new_alerts(conn, "https://example.test/hook", lambda u, p, t: sent.append(p)), 0)
        self.assertIn("emerging issue", sent[0]["text"])

        def broken(*_):
            raise TimeoutError("chat is down")
        conn.execute("DELETE FROM notifications")
        self.assertEqual(notify.notify_new_alerts(conn, "https://example.test/hook", broken), 0)
        self.assertEqual(conn.execute("SELECT ok FROM notifications").fetchone()[0], 0)

    def test_no_webhook_means_nothing_sent(self):
        self.assertEqual(notify.notify_new_alerts(self.db(), None), 0)


class OnboardingTests(unittest.TestCase):
    def test_empty_workspace_then_load_sample(self):
        tmp = tempfile.TemporaryDirectory()
        app = create_app(Settings(db_path=Path(tmp.name) / "e.db"))
        c = app.test_client()
        c.post("/login", data={"username": "admin", "password": "admin-demo"})
        self.assertIn(b"No tickets yet", c.get("/dashboard").data)
        c.post("/import", data={"sample": "1"})
        self.assertIn(b"Emerging", c.get("/dashboard").data)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
