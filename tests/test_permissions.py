import tempfile
import unittest
from pathlib import Path

from tests.helpers import SAMPLE
from fixproblem import db, pipeline
from fixproblem.app import create_app
from fixproblem.auth import can, mask_email
from fixproblem.config import Settings
from fixproblem.ingest import import_csv


class PermissionRules(unittest.TestCase):
    def test_role_matrix(self):
        self.assertTrue(can("agent", "view"))
        self.assertFalse(can("agent", "view_pii"))
        self.assertFalse(can("agent", "import"))
        self.assertTrue(can("analyst", "export"))
        self.assertFalse(can("analyst", "import"))
        self.assertTrue(can("admin", "import"))
        self.assertFalse(can(None, "view"))
        self.assertFalse(can("admin", "unknown_permission"))

    def test_mask_email(self):
        self.assertEqual(mask_email("jane@example.com"), "j•••@example.com")
        self.assertEqual(mask_email(""), "•••")


class WebPermissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cfg = Settings(db_path=Path(cls.tmp.name) / "t.db")
        conn = db.connect(cfg.db_path)
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, cfg)
        conn.close()
        cls.app = create_app(cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def client(self, user=None):
        c = self.app.test_client()
        if user:
            c.post("/login", data={"username": user, "password": f"{user}-demo"})
        return c

    def test_anonymous_sees_landing_but_not_the_app(self):
        self.assertEqual(self.client().get("/").status_code, 200)
        for path in ["/dashboard", "/inbox", "/issues", "/api/issues"]:
            self.assertEqual(self.client().get(path).status_code, 302, path)

    def test_wrong_password_rejected(self):
        c = self.app.test_client()
        r = c.post("/login", data={"username": "admin", "password": "nope"})
        self.assertIn(b"Incorrect", r.data)
        self.assertEqual(c.get("/dashboard").status_code, 302)

    def test_agent_cannot_import_or_export(self):
        c = self.client("agent")
        self.assertEqual(c.post("/import", data={"sample": "1"}).status_code, 403)
        self.assertEqual(c.get("/export/issues.csv").status_code, 403)

    def test_agent_sees_masked_emails(self):
        data = self.client("agent").get("/api/issues/1/tickets").get_json()
        self.assertTrue(all("•••@" in t["customer_email"] for t in data))

    def test_analyst_sees_full_emails_and_can_export(self):
        c = self.client("analyst")
        data = c.get("/api/issues/1/tickets").get_json()
        self.assertTrue(all("•••" not in t["customer_email"] for t in data))
        self.assertEqual(c.get("/export/issues.csv").status_code, 200)

    def test_admin_upload_validation(self):
        c = self.client("admin")
        r = c.post("/import", data={"file": (__import__("io").BytesIO(b"a,b\n1,2\n"), "bad.csv")},
                   content_type="multipart/form-data")
        self.assertIn(b"missing required columns", r.data)
        r = c.post("/import", data={"file": (__import__("io").BytesIO(b"x"), "notes.txt")},
                   content_type="multipart/form-data")
        self.assertIn(b"Only .csv", r.data)

    def test_unknown_issue_is_404(self):
        self.assertEqual(self.client("agent").get("/issues/99999").status_code, 404)


if __name__ == "__main__":
    unittest.main()
