"""Accounts, guest access, onboarding, plans, payments, tracking and data rights."""
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from tests.helpers import SAMPLE
from fixproblem import accounts, db, pipeline
from fixproblem.app import create_app
from fixproblem.config import Settings
from fixproblem.ingest import import_csv

GOOD = {"name": "Amina Khan", "email": "amina@example.com", "password": "secret123", "terms": "1"}


class SaaSBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = Settings(db_path=Path(cls.tmp.name) / "s.db")
        conn = db.connect(cls.cfg.db_path)
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, cls.cfg)
        conn.close()
        cls.app = create_app(cls.cfg)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        c = db.connect(self.cfg.db_path)
        c.execute("DELETE FROM users"); c.execute("DELETE FROM payments"); c.execute("DELETE FROM activity")
        c.commit()

    def db(self):
        return db.connect(self.cfg.db_path)

    def signup(self, **over):
        c = self.app.test_client()
        r = c.post("/signup", data={**GOOD, **over})
        return c, r


class AccountTests(SaaSBase):
    def test_password_is_hashed_never_stored(self):
        self.signup()
        row = self.db().execute("SELECT password_hash FROM users").fetchone()[0]
        self.assertNotIn("secret123", row)
        self.assertTrue(row.startswith(("scrypt:", "pbkdf2:")))

    def test_validation(self):
        for bad in [{"email": "not-an-email"}, {"password": "short1"}, {"password": "lettersonly"}, {"terms": ""}]:
            _, r = self.signup(**bad)
            self.assertEqual(r.status_code, 400, bad)
        self.signup()
        _, r = self.signup()
        self.assertIn(b"already exists", r.data)

    def test_sign_in_with_email_and_wrong_password(self):
        self.signup()
        c = self.app.test_client()
        self.assertEqual(c.post("/login", data={"username": "AMINA@example.com", "password": "secret123"}).status_code, 302)
        self.assertIn(b"Incorrect", self.app.test_client().post("/login", data={"username": "amina@example.com",
                                                                              "password": "nope12345"}).data)

    def test_brute_force_lockout(self):
        c = self.app.test_client()
        for _ in range(5):
            c.post("/login", data={"username": "x@example.com", "password": "wrong123"},
                   environ_base={"REMOTE_ADDR": "10.9.9.9"})
        r = c.post("/login", data={"username": "x@example.com", "password": "wrong123"},
                   environ_base={"REMOTE_ADDR": "10.9.9.9"})
        self.assertEqual(r.status_code, 429)

    def test_open_redirect_blocked(self):
        c = self.app.test_client()
        r = c.post("/login?next=//evil.example", data={"username": "agent", "password": "agent-demo"})
        self.assertTrue(r.headers["Location"].endswith("/dashboard"))


class GuestAndTourTests(SaaSBase):
    def test_guest_is_read_only_and_tracked(self):
        c = self.app.test_client()
        c.post("/guest")
        self.assertEqual(c.get("/dashboard").status_code, 200)
        self.assertEqual(c.post("/issues/1/update", data={"comment": "hi"}).status_code, 403)
        self.assertEqual(c.post("/review/NWB-00001", data={"label": "billing"}).status_code, 403)
        actors = [r[0] for r in self.db().execute("SELECT DISTINCT actor FROM activity")]
        self.assertTrue(any(a.startswith("guest:") for a in actors))

    def test_tour_shown_once_for_new_user(self):
        c, _ = self.signup()
        self.assertIn(b"tour-back", c.get("/dashboard").data)
        c.post("/tour/done")
        self.assertNotIn(b"tour-back", c.get("/dashboard").data)
        self.assertEqual(self.db().execute("SELECT onboarded FROM users").fetchone()[0], 1)
        c2 = self.app.test_client()
        c2.post("/login", data={"username": GOOD["email"], "password": GOOD["password"]})
        self.assertNotIn(b"tour-back", c2.get("/dashboard").data)


class PlanTests(SaaSBase):
    def test_free_plan_redirects_pro_features_to_billing(self):
        c, _ = self.signup()
        for path in ["/settings", "/export/issues.csv"]:
            r = c.get(path)
            self.assertEqual(r.status_code, 302, path)
            self.assertIn("/billing", r.headers["Location"])
        self.assertIn(b"Drop a CSV", c.get("/import").data)          # Starter can import into its own workspace
        self.assertEqual(c.post("/retrain").status_code, 402)

    def test_trial_unlocks_then_expires(self):
        c, _ = self.signup()
        c.post("/billing/trial")
        self.assertEqual(c.get("/settings").status_code, 200)
        self.assertIn(b"already used", c.post("/billing/trial", follow_redirects=True).data)
        conn = self.db()
        conn.execute("UPDATE users SET trial_ends='2000-01-01 00:00:00'")
        conn.commit()
        self.assertEqual(c.get("/settings").status_code, 302)        # downgraded to Free

    def test_signup_offer_applies_once(self):
        accounts.SIGNUP_DISCOUNT_PCT = 10          # the offer is configurable and off by default
        self.addCleanup(setattr, accounts, "SIGNUP_DISCOUNT_PCT", 0)
        c, _ = self.signup()
        u = accounts.get_user(self.db(), email=GOOD["email"])
        self.assertEqual(accounts.price_for(u), (863, 10))             # £9.59 - 10%
        c.post("/billing/demo")
        conn = self.db()
        self.assertEqual(conn.execute("SELECT amount_pence, discount_pct FROM payments").fetchone()[:], (863, 10))
        u = accounts.get_user(conn, email=GOOD["email"])
        self.assertEqual(u["plan"], "pro")
        self.assertEqual(accounts.price_for(u), (959, 0))
        accounts.record_payment(conn, u, "demo", "renewal")             # renewal stacks 30 days
        self.assertGreater(accounts.get_user(conn, email=GOOD["email"])["pro_until"], u["pro_until"])

    def test_cancel_keeps_access_until_period_end(self):
        c, _ = self.signup()
        c.post("/billing/demo")
        c.post("/billing/cancel")
        self.assertEqual(c.get("/settings").status_code, 200)
        conn = self.db()
        conn.execute("UPDATE users SET pro_until='2000-01-01 00:00:00'")
        conn.commit()
        self.assertEqual(c.get("/settings").status_code, 302)

    def test_billing_needs_an_account(self):
        c = self.app.test_client()
        c.post("/guest")
        self.assertIn("/login", c.post("/billing/checkout").headers["Location"])


class DataRightsTests(SaaSBase):
    def test_export_contains_activity_but_never_password(self):
        c, _ = self.signup()
        c.get("/inbox?q=refund")
        data = json.loads(c.get("/account/export").data)
        self.assertEqual(data["account"]["email"], GOOD["email"])
        self.assertNotIn("password_hash", data["account"])
        self.assertTrue(any("/inbox" in a["path"] for a in data["activity"]))

    def test_delete_account_removes_personal_data(self):
        c, _ = self.signup()
        c.get("/inbox")
        self.assertIn(b"Type DELETE", c.post("/account/delete", data={"confirm": "no"}, follow_redirects=True).data)
        c.post("/account/delete", data={"confirm": "DELETE"})
        conn = self.db()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM activity WHERE actor=?", (GOOD["email"],)).fetchone()[0], 0)

    def test_owner_analytics_admin_only(self):
        c, _ = self.signup()
        self.assertEqual(c.get("/admin/analytics").status_code, 403)
        a = self.app.test_client()
        a.post("/login", data={"username": "admin", "password": "admin-demo"})
        r = a.get("/admin/analytics")
        self.assertIn(GOOD["email"].encode(), r.data)
        self.assertNotIn(b"scrypt:", r.data)


if __name__ == "__main__":
    unittest.main()
