"""The saved-comparison file: round trip, missing file, corrupt file."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import comparison_store  # noqa: E402


class TestComparisonStore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "sub", comparison_store.FILENAME)

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_is_an_empty_table(self):
        self.assertEqual(comparison_store.load(self.path), [])

    def test_round_trip(self):
        deals = [{"label": "12 Elm St", "row": ["12 Elm St", "PASS"],
                  "form": {"fields": {"f_price": "200,000"}, "switches": {},
                           "niit": False, "passive_usable": True, "listing": {}}}]
        comparison_store.save(deals, self.path)
        self.assertEqual(comparison_store.load(self.path), deals)
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_corrupt_file_is_moved_aside_not_lost(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("{not json")
        with self.assertRaises(ValueError):
            comparison_store.load(self.path)
        self.assertFalse(os.path.exists(self.path))
        with open(self.path + ".corrupt") as fh:
            self.assertEqual(fh.read(), "{not json")

    def test_env_override_sets_location(self):
        old = os.environ.get("RENTAL_ANALYZER_DATA_DIR")
        os.environ["RENTAL_ANALYZER_DATA_DIR"] = self.dir.name
        try:
            self.assertEqual(comparison_store.store_path(),
                             os.path.join(self.dir.name, comparison_store.FILENAME))
        finally:
            if old is None:
                del os.environ["RENTAL_ANALYZER_DATA_DIR"]
            else:
                os.environ["RENTAL_ANALYZER_DATA_DIR"] = old

    def test_default_location_is_git_ignored(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, ".gitignore")) as fh:
            self.assertIn(comparison_store.FILENAME, fh.read().split())

    def test_file_is_plain_json(self):
        comparison_store.save([{"row": ["x"]}], self.path)
        with open(self.path) as fh:
            self.assertEqual(json.load(fh)["deals"], [{"row": ["x"]}])


if __name__ == "__main__":
    unittest.main()
