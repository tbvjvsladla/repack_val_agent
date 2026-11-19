import json
import os
import tempfile
import unittest

from app import ledger


class LedgerTest(unittest.TestCase):
    def test_missing_file_has_no_entries(self):
        self.assertEqual(ledger.load_entries("/nonexistent/entries.json"), [])

    def test_loads_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "e.json")
            with open(path, "w") as handle:
                json.dump([{"cents": 5}], handle)
            self.assertEqual(ledger.load_entries(path), [{"cents": 5}])

    def test_total_and_describe(self):
        entries = [{"cents": 150}, {"cents": 25}]
        self.assertEqual(ledger.total(entries), 175)
        self.assertEqual(ledger.describe(entries), "total 1.75")

    def test_parse_and_ratio(self):
        self.assertEqual(ledger.parse_cents("42"), 42)
        self.assertEqual(ledger.checked_ratio(6, 3), 2)
        with self.assertRaises(ValueError):
            ledger.checked_ratio(1, 0)

    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x")
            self.assertFalse(ledger.wait_for_export(path))
            ledger.remove_stale(path)
            open(path, "w").close()
            self.assertTrue(ledger.export_done(path))
            ledger.remove_stale(path)
            self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()
