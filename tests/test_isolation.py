"""Workspace isolation: every customer has a private workspace (its own database file).
These tests try to reach another customer's data with guessed IDs, search, exports and the API."""
import io
import re
import tempfile
import unittest
from pathlib import Path

from tests.helpers import SAMPLE
from fixproblem import db, pipeline
from fixproblem.app import create_app
from fixproblem.config import Settings
from fixproblem.ingest import import_csv

SECRET_A = "Zebracorn invoice number 77 was charged twice"
CSV_A = ("ticket_id,created_at,body,customer_email\n" +
         "\n".join(f"A-{i},2026-09-{1 + i % 20:02d} 10:00,{SECRET_A} #{i},alice{i}@acompany.example" for i in range(40)))
CSV_B = ("ticket_id,created_at,body\n" +
         "\n".join(f"B-{i},2026-09-{1 + i % 20:02d} 11:00,Brightbank router keeps dropping #{i}" for i in range(40)))


class IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = Settings(db_path=Path(cls.tmp.name) / "main.db")
        conn = db.connect(cls.cfg.db_path)
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, cls.cfg)
        conn.close()
        cls.app = create_app(cls.cfg)
        cls.a = cls.signup("Alice", "alice@acompany.example", "A Company")
        cls.b = cls.signup("Bob", "bob@bcompany.example", "B Company")
        cls.upload(cls.a, CSV_A)
        cls.upload(cls.b, CSV_B)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def signup(cls, name, email, ws):
        c = cls.app.test_client()
        c.post("/signup", data={"name": name, "email": email, "password": "Secret123", "workspace": ws, "terms": "1"})
        c.post("/tour/done")
        return c

    @staticmethod
    def upload(c, text):
        r = c.post("/import/wizard", data={"step": "upload", "file": (io.BytesIO(text.encode()), "t.csv")},
                   content_type="multipart/form-data")
        token = re.search(rb'name="token" value="([0-9a-f]+)"', r.data).group(1).decode()
        m = {"map_ticket_id": "ticket_id", "map_created_at": "created_at", "map_body": "body"}
        if b"customer_email" in r.data:
            m["map_customer_email"] = "customer_email"
        c.post("/import/wizard", data={"step": "confirm", "token": token, "name": "t.csv", **m})

    def test_new_workspace_starts_empty(self):
        c = self.signup("Carol", "carol@ccompany.example", "C Company")
        r = c.get("/dashboard").data
        self.assertIn(b"No tickets yet", r)
        self.assertNotIn(b"Northwind", r.split(b"<main")[1].split(b"guest-bar")[0] if b"guest-bar" in r else b"")
        self.assertEqual(c.get("/api/issues").get_json(), [])

    def test_each_workspace_sees_only_its_own_tickets(self):
        a_inbox, b_inbox = self.a.get("/inbox").data, self.b.get("/inbox").data
        self.assertIn(b"A-1", a_inbox)
        self.assertNotIn(b"B-1", a_inbox)
        self.assertIn(b"B-1", b_inbox)
        self.assertNotIn(b"Zebracorn", b_inbox)
        self.assertNotIn(b"NWB-", a_inbox)                       # no demo records either

    def test_search_and_command_palette_are_scoped(self):
        self.assertIn(b"No tickets match", self.b.get("/inbox?q=Zebracorn").data)
        self.assertEqual(self.b.get("/api/search?q=Zebracorn").get_json()["tickets"], [])

    def test_guessed_issue_ids_show_only_own_data(self):
        for iid in range(1, 6):                                   # both workspaces have issue 1, 2...
            r = self.b.get(f"/issues/{iid}")
            self.assertNotIn(b"Zebracorn", r.data)
            data = self.b.get(f"/api/issues/{iid}/tickets").get_json() or []
            self.assertFalse(any("A-" in t["ticket_id"] for t in data))

    def test_labelling_another_workspaces_ticket_fails(self):
        self.assertEqual(self.b.post("/review/A-1", data={"label": "billing"}).status_code, 404)

    def test_exports_and_reports_contain_only_own_data(self):
        for c, own, other in [(self.a, "Zebracorn", "Brightbank"), (self.b, "Brightbank", "Zebracorn")]:
            c.post("/billing/trial")                              # exports are a Team-plan feature
            report = c.get("/reports").data.decode()
            self.assertNotIn(other, report)
            csv_text = c.get("/export/issues.csv").data.decode()
            self.assertNotIn(other, csv_text)

    def test_switching_to_a_workspace_you_do_not_belong_to_is_refused(self):
        for target in ["1", "2", "999", "abc"]:                   # demo, Alice's, nonexistent, junk
            r = self.b.post("/workspace/switch", data={"workspace_id": target})
            self.assertIn(r.status_code, (400, 403), target)
        self.assertNotIn(b"Zebracorn", self.b.get("/inbox").data)

    def test_tampered_session_workspace_is_corrected_server_side(self):
        with self.b.session_transaction() as s:
            s["ws"] = 2                                           # Alice's workspace id
        r = self.b.get("/inbox").data
        self.assertNotIn(b"Zebracorn", r)

    def test_sample_data_gets_a_separate_workspace(self):
        c = self.signup("Dan", "dan@dcompany.example", "D Company")
        self.upload(c, CSV_B.replace("B-", "D-").replace("Brightbank", "Dancorp"))
        c.post("/workspace/sample")
        self.assertIn(b"NWB-", c.get("/inbox").data)              # in the sample workspace now
        self.assertNotIn(b"Dancorp", c.get("/inbox").data)
        ws = [w for w in re.findall(rb'name="workspace_id" value="(\d+)"', c.get("/dashboard").data)]
        c.post("/workspace/switch", data={"workspace_id": ws[0].decode()})
        own = c.get("/inbox").data
        self.assertIn(b"Dancorp", own)
        self.assertNotIn(b"NWB-", own)                            # never mixed into the customer's data

    def test_customer_cannot_load_sample_into_own_workspace(self):
        c = self.signup("Eve", "eve@ecompany.example", "E Company")
        c.post("/import", data={"sample": "1"}, follow_redirects=True)
        ws_ids = re.findall(rb'name="workspace_id" value="(\d+)"', c.get("/dashboard").data)
        c.post("/workspace/switch", data={"workspace_id": ws_ids[0].decode()})
        self.assertIn(b"No tickets yet", c.get("/dashboard").data)

    def test_each_workspace_has_its_own_database_file(self):
        files = sorted(p.name for p in (Path(self.cfg.db_path).parent / "workspaces").glob("ws_*.db"))
        self.assertGreaterEqual(len(files), 2)

    def test_demo_users_and_guests_stay_in_the_demo_workspace(self):
        g = self.app.test_client()
        g.get("/demo")
        self.assertIn(b"NWB-", g.get("/inbox").data)
        self.assertNotIn(b"Zebracorn", g.get("/inbox?q=Zebracorn").data.split(b"<tbody>")[1])
        self.assertEqual(g.post("/workspace/switch", data={"workspace_id": "2"}).status_code, 403)

    def test_deleting_an_account_erases_its_workspace_file(self):
        c = self.signup("Finn", "finn@fcompany.example", "F Company")
        self.upload(c, CSV_B.replace("B-", "F-"))
        before = set((Path(self.cfg.db_path).parent / "workspaces").glob("ws_*.db"))
        c.post("/account/delete", data={"confirm": "DELETE"})
        after = set((Path(self.cfg.db_path).parent / "workspaces").glob("ws_*.db"))
        self.assertEqual(len(before) - len(after), 1)


if __name__ == "__main__":
    unittest.main()
