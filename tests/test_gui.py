"""
GUI tests. The number-parsing and formatting helpers are tested everywhere;
the widget tests only run where tkinter and a display are both available, so
this suite stays green on a headless box.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import tkinter  # noqa: F401
    HAS_TK = True
except ImportError:
    HAS_TK = False

HAS_DISPLAY = bool(os.environ.get("DISPLAY")) or sys.platform in ("win32", "darwin")


@unittest.skipUnless(HAS_TK, "tkinter not available in this Python build")
class TestNumberParsing(unittest.TestCase):
    def setUp(self):
        from gui import parse_number
        self.parse = parse_number

    def test_plain_and_decorated_numbers(self):
        self.assertEqual(self.parse("285000"), 285000.0)
        self.assertEqual(self.parse("$285,000"), 285000.0)
        self.assertEqual(self.parse(" 7.0% "), 7.0)
        self.assertEqual(self.parse("1,392"), 1392.0)

    def test_blank_and_garbage_fall_back(self):
        self.assertIsNone(self.parse(""))
        self.assertIsNone(self.parse("   "))
        self.assertEqual(self.parse("not a number", 42.0), 42.0)
        self.assertEqual(self.parse(None, 7.0), 7.0)


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestGUIPipeline(unittest.TestCase):
    """Drives the real widgets: load a listing, underwrite, compare, export."""

    def setUp(self):
        import tkinter as tk
        from gui import RentalAnalyzerGUI
        self.root = tk.Tk()
        self.app = RentalAnalyzerGUI(self.root)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_sample_loads_and_underwrites(self):
        self.app.load_sample()
        self.root.update()
        self.assertEqual(self.app.f_price.get(), 239900.0)
        self.assertEqual(self.app.f_rent.get(), 2399.0)      # 1% rule auto-fill
        self.assertIsNotNone(self.app.result)
        self.assertIn("PASS ON IT", self.app.verdict_label.cget("text"))

    def test_typed_tax_flips_source_to_verified(self):
        self.app.load_sample()
        self.assertFalse(self.app._tax_is_provided())
        self.app.f_tax.var.set("6800")
        self.app.underwrite()
        self.assertTrue(self.app._tax_is_provided())
        self.assertEqual(self.app.result.inputs.expenses.property_tax_annual, 6800.0)
        self.assertIn("VERIFIED", self.app.tax_source_label.cget("text"))

    def test_edited_field_drives_the_model(self):
        self.app.load_sample()
        self.app.f_price.var.set("180000")
        self.app.f_rate.var.set("6.0")
        self.app.underwrite()
        self.assertEqual(self.app.result.inputs.purchase_price, 180000.0)
        self.assertAlmostEqual(self.app.result.inputs.interest_rate, 0.06)

    def test_hand_typed_address_not_duplicated(self):
        self.app.load_sample()
        self.app.f_address.var.set("12 Elm St")
        self.app.underwrite()
        self.assertEqual(self.app.listing.full_address, "12 Elm St")

    def test_comparison_accumulates(self):
        self.app.load_sample()
        self.app.add_to_comparison()
        self.app.f_price.var.set("165000")
        self.app.underwrite()
        self.app.add_to_comparison()
        self.assertEqual(len(self.app.comparison), 2)
        self.assertEqual(len(self.app.tree.get_children()), 2)
        self.app.clear_comparison()
        self.assertEqual(len(self.app.comparison), 0)

    def test_underwrite_without_price_is_not_fatal(self):
        self.app.reset_fields()
        self.app.underwrite()
        self.assertIsNone(self.app.result)
        self.assertIn("purchase price", self.app.status.get().lower())


if __name__ == "__main__":
    unittest.main()
