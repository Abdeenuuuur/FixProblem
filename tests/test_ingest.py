import unittest

from tests.helpers import SAMPLE  # noqa: F401
from fixproblem import db
from fixproblem.ingest import ImportFormatError, import_csv, parse_timestamp

HEADER = "ticket_id,created_at,body,customer_tier\n"


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")

    def test_valid_rows_are_imported(self):
        res = import_csv(self.conn, HEADER + "T1,2026-01-05 09:00,router down,premium\nT2,2026-01-05 10:00,bill wrong,\n")
        self.assertEqual(res.accepted, 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 2)

    def test_reimport_skips_duplicates(self):
        data = HEADER + "T1,2026-01-05 09:00,router down,\n"
        import_csv(self.conn, data)
        res = import_csv(self.conn, data)
        self.assertEqual((res.accepted, res.duplicates), (0, 1))

    def test_duplicate_ids_inside_one_file(self):
        res = import_csv(self.conn, HEADER + "T1,2026-01-05 09:00,a,\nT1,2026-01-05 09:05,b,\n")
        self.assertEqual((res.accepted, res.duplicates), (1, 1))

    def test_bad_rows_are_rejected_with_reason_not_dropped(self):
        res = import_csv(self.conn, HEADER + "T1,not-a-date,hello,\nT2,2026-01-05 09:00,,\nT3,2026-01-05 09:00,ok,\n")
        self.assertEqual(res.accepted, 1)
        self.assertEqual(res.rejected_count, 2)
        reasons = dict(res.rejected)
        self.assertIn("timestamp", reasons[2])
        self.assertIn("missing body", reasons[3])

    def test_missing_required_column_fails_whole_file(self):
        with self.assertRaises(ImportFormatError):
            import_csv(self.conn, "ticket_id,body\nT1,hello\n")

    def test_empty_and_binary_files_fail_cleanly(self):
        with self.assertRaises(ImportFormatError):
            import_csv(self.conn, "")
        with self.assertRaises(ImportFormatError):
            import_csv(self.conn, b"\xff\xfe\x00\x81binary")

    def test_unknown_tier_defaults_to_standard(self):
        import_csv(self.conn, HEADER + "T1,2026-01-05 09:00,x,platinum\n")
        self.assertEqual(self.conn.execute("SELECT customer_tier FROM tickets").fetchone()[0], "standard")

    def test_timestamp_formats(self):
        self.assertEqual(parse_timestamp("05/01/2026 09:30"), "2026-01-05 09:30")
        self.assertEqual(parse_timestamp("2026-01-05T09:30:00"), "2026-01-05 09:30")

    def test_import_is_logged(self):
        import_csv(self.conn, HEADER + "T1,bad,x,\n", "export.csv")
        row = self.conn.execute("SELECT filename, accepted, rejected FROM import_log").fetchone()
        self.assertEqual(tuple(row), ("export.csv", 0, 1))


if __name__ == "__main__":
    unittest.main()
