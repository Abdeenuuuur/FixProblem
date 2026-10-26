import unittest

from tests.helpers import SAMPLE
from fixproblem import db, pipeline
from fixproblem.classify import TicketClassifier
from fixproblem.config import Settings
from fixproblem.ingest import import_csv
from fixproblem.summarise import extractive_summary, summarise


class ClassifierTests(unittest.TestCase):
    def test_low_confidence_goes_to_review(self):
        clf = TicketClassifier(review_threshold=0.99).fit(
            ["bill too high", "charged twice", "internet down", "no broadband"] * 3,
            ["billing", "billing", "connectivity", "connectivity"] * 3)
        pred = clf.predict(["something unrelated entirely"])[0]
        self.assertTrue(pred.needs_review)

    def test_untrained_model_raises(self):
        with self.assertRaises(RuntimeError):
            TicketClassifier().predict(["x"])

    def test_single_class_training_rejected(self):
        with self.assertRaises(ValueError):
            TicketClassifier().fit(["a", "b"], ["billing", "billing"])


class SummaryTests(unittest.TestCase):
    def test_llm_failure_falls_back(self):
        def broken(_):
            raise TimeoutError("network down")
        text = summarise("app / crash", ["a", "b"], "app crashed", llm=broken)
        self.assertEqual(text, extractive_summary("app / crash", 2, "app crashed"))

    def test_llm_used_when_available(self):
        self.assertEqual(summarise("x", ["a"], "a", llm=lambda ex: "LLM summary"), "LLM summary")


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = db.connect(":memory:")
        import_csv(cls.conn, SAMPLE.read_text(encoding="utf-8"))
        cls.summary = pipeline.run(cls.conn, Settings(), llm=None)

    def test_every_ticket_gets_category_and_issue(self):
        missing = self.conn.execute("SELECT COUNT(*) FROM tickets WHERE pred_category IS NULL OR issue_id IS NULL")
        self.assertEqual(missing.fetchone()[0], 0)

    def test_injected_app_crash_is_alerted(self):
        alerts = self.conn.execute("SELECT * FROM alerts").fetchall()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["category"], "technical_app")

    def test_alert_has_ticket_references(self):
        alert = self.conn.execute("SELECT issue_ids, tickets FROM alerts").fetchone()
        ids = alert["issue_ids"].split(",")
        n = self.conn.execute(f"SELECT COUNT(*) FROM tickets WHERE issue_id IN ({','.join('?'*len(ids))})", ids)
        self.assertEqual(n.fetchone()[0], alert["tickets"])

    def test_empty_database(self):
        self.assertEqual(pipeline.run(db.connect(":memory:"), Settings())["tickets"], 0)


if __name__ == "__main__":
    unittest.main()
