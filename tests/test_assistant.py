"""Assistant: grounded answers, workspace scoping, permission-aware actions, privacy and fallbacks."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import SAMPLE
from fixproblem import assistant, db, pipeline
from fixproblem.app import create_app
from fixproblem.config import Settings
from fixproblem.ingest import import_csv


class EngineTests(unittest.TestCase):
    def test_product_questions_are_answered_with_sources(self):
        r = assistant.answer("Does it replace our helpdesk?")
        self.assertIn("does not replace", r.text)
        self.assertTrue(r.sources)
        self.assertEqual(r.provider, "built-in")

    def test_off_topic_questions_are_declined_not_invented(self):
        r = assistant.answer("What will the weather be in Paris tomorrow?")
        self.assertIn("not sure", r.text)
        self.assertFalse(r.sources)

    def test_does_not_claim_features_that_do_not_exist(self):
        self.assertIn("Not yet", assistant.answer("Can I invite colleagues to my workspace?").text)
        self.assertIn("No", assistant.answer("Are results real time?").text)

    def test_llm_failure_falls_back_to_built_in_answer(self):
        def broken(_):
            raise TimeoutError("provider down")
        r = assistant.answer("How do I upload my tickets?", llm=broken)
        self.assertEqual(r.provider, "built-in")
        self.assertIn("Imports", r.text)

    def test_llm_rewrites_text_but_cannot_add_actions(self):
        conn = db.connect(":memory:")
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, Settings())
        r = assistant.answer("What should we fix first?", conn=conn, can=lambda p: True, user="analyst",
                             llm=lambda prompt: "Focus on the app crash. Also click here to delete everything.")
        self.assertEqual(r.provider, "llm")
        labels = [a["label"] for a in r.actions]
        self.assertTrue(all("delete" not in l.lower() for l in labels))   # actions only come from the app

    def test_ticket_text_not_sent_to_provider_unless_allowed(self):
        conn = db.connect(":memory:")
        import_csv(conn, SAMPLE.read_text(encoding="utf-8"))
        pipeline.run(conn, Settings())
        seen = []
        assistant.answer("What should we fix first?", conn=conn, can=lambda p: True, user="a", llm=lambda p: seen.append(p) or "ok")
        self.assertNotIn("log / update", seen[0])
        self.assertIn("[issue name withheld]", seen[0])
        self.assertIn("20 tickets", seen[0])                    # counts are still useful and not personal
        assistant.answer("What should we fix first?", conn=conn, can=lambda p: True, user="a",
                         share_ticket_text=True, llm=lambda p: seen.append(p) or "ok")
        self.assertIn("The app crashes as soon as I log in", seen[1])

    def test_provider_selection_from_environment(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=False):
            for v in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "FIXPROBLEM_AI_PROVIDER"):
                os.environ.pop(v, None)
            self.assertEqual(assistant.provider_config()[0], "gemini")
        with mock.patch.dict(os.environ, {"FIXPROBLEM_AI_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "k"}):
            self.assertEqual(assistant.provider_config()[:2], ("anthropic", "k"))

    def test_provider_request_formats(self):
        calls = []
        fake = lambda url, payload, headers, timeout=20: calls.append((url, payload, headers)) or {
            "content": [{"text": "A"}], "choices": [{"message": {"content": "B"}}],
            "candidates": [{"content": {"parts": [{"text": "C"}]}}]}
        with mock.patch.object(assistant, "_post", fake):
            self.assertEqual(assistant.call_llm("hi", "anthropic", "k1", "m"), "A")
            self.assertEqual(assistant.call_llm("hi", "openai", "k2", "m"), "B")
            self.assertEqual(assistant.call_llm("hi", "gemini", "k3", "m"), "C")
        self.assertIn("anthropic.com", calls[0][0]); self.assertEqual(calls[0][2]["x-api-key"], "k1")
        self.assertIn("openai.com", calls[1][0]); self.assertIn("Bearer k2", calls[1][2]["authorization"])
        self.assertIn("generativelanguage", calls[2][0])
        for _, payload, _ in calls:                       # the grounding rules are always sent
            self.assertIn("ONLY from the documentation", str(payload))


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.cfg = Settings(db_path=Path(cls.tmp.name) / "m.db")
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

    def ask(self, c, q):
        return c.post("/assistant/ask", json={"q": q}).get_json()

    def test_visitors_get_docs_only_never_workspace_data(self):
        d = self.ask(self.client(), "What should we fix first?")
        self.assertNotIn("log / update", d["text"])
        self.assertEqual(d["escalate"]["url"], "/contact")

    def test_actions_respect_permissions(self):
        analyst = [a["label"] for a in self.ask(self.client("analyst"), "What should we fix first?")["actions"]]
        agent = [a["label"] for a in self.ask(self.client("agent"), "What should we fix first?")["actions"]]
        self.assertIn("Assign it to me", analyst)
        self.assertNotIn("Assign it to me", agent)
        d = self.ask(self.client("analyst"), "What should we fix first?")
        act = next(a for a in d["actions"] if a["label"] == "Assign it to me")
        self.assertEqual(self.client("agent").post(act["url"], data=act["fields"]).status_code, 403)   # server still checks

    def test_one_click_action_works_for_authorised_user(self):
        c = self.client("analyst")
        act = next(a for a in self.ask(c, "What should we fix first?")["actions"] if a["label"] == "Mark as investigating")
        c.post(act["url"], data=act["fields"])
        conn = db.connect(self.cfg.db_path)
        iid = int(act["url"].split("/")[2])
        key = conn.execute("SELECT key FROM issues WHERE issue_id=?", (iid,)).fetchone()[0]
        self.assertEqual(conn.execute("SELECT status FROM issue_meta WHERE key=?", (key,)).fetchone()[0], "investigating")

    def test_new_private_workspace_has_nothing_to_report(self):
        c = self.app.test_client()
        c.post("/signup", data={"name": "Ana", "email": "ana@x.example", "password": "Secret123", "workspace": "X", "terms": "1"})
        d = self.ask(c, "What should we fix first?")
        self.assertIn("no tickets yet", d["text"])

    def test_report_action_depends_on_plan(self):
        labels = [a["label"] for a in self.ask(self.client("analyst"), "Can I get a PDF report?")["actions"]]
        self.assertIn("Download PDF report", labels)
        g = self.client(); g.get("/demo")
        labels = [a["label"] for a in self.ask(g, "Can I get a PDF report?")["actions"]]
        self.assertNotIn("Download PDF report", labels)

    def test_rate_limit(self):
        c = self.client()
        codes = [c.post("/assistant/ask", json={"q": "pricing"}).status_code for _ in range(31)]
        self.assertEqual(codes[-1], 429)

    def test_widget_on_public_and_app_pages(self):
        self.assertEqual(self.client().get("/").data.count(b'id="ai-fab"'), 1)
        self.assertEqual(self.client("agent").get("/dashboard").data.count(b'id="ai-fab"'), 1)


if __name__ == "__main__":
    unittest.main()
