"""
GUI tests. The number-parsing and formatting helpers are tested everywhere;
the widget tests only run where tkinter and a display are both available, so
this suite stays green on a headless box.

The load-bearing tests here are the refusals: the GUI must not underwrite
without a rent or a tax figure, and it must not let one listing's rent or tax
follow you onto the next one.
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


class GUITestCase(unittest.TestCase):
    """Shared fixture: a real widget tree with the sample listing available."""

    def setUp(self):
        import tkinter as tk
        from gui import RentalAnalyzerGUI
        self.root = tk.Tk()
        self.app = RentalAnalyzerGUI(self.root)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def load_and_fill(self, rent="2400", tax="5245"):
        """Load the sample, then supply the two required figures by hand."""
        self.app.load_sample()
        self.app.f_rent.var.set(rent)
        self.app.f_tax.var.set(tax)
        self.app.underwrite()
        self.root.update()


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestGUIPipeline(GUITestCase):
    """Drives the real widgets: load a listing, underwrite, compare, export."""

    def test_sample_loads_but_does_not_underwrite_on_its_own(self):
        self.app.load_sample()
        self.root.update()
        self.assertEqual(self.app.f_price.get(), 239900.0)
        # Nothing auto-fills rent or tax, so nothing is underwritten yet.
        self.assertEqual(self.app.f_rent.get_text(), "")
        self.assertEqual(self.app.f_tax.get_text(), "")
        self.assertIsNone(self.app.result)

    def test_underwrites_once_rent_and_tax_are_supplied(self):
        self.load_and_fill()
        self.assertIsNotNone(self.app.result)
        self.assertEqual(self.app.result.inputs.monthly_rent, 2400.0)
        self.assertEqual(self.app.result.inputs.expenses.property_tax_annual, 5245.0)

    def test_edited_field_drives_the_model(self):
        self.load_and_fill()
        self.app.f_price.var.set("180000")
        self.app.f_rate.var.set("6.0")
        self.app.underwrite()
        self.assertEqual(self.app.result.inputs.purchase_price, 180000.0)
        self.assertAlmostEqual(self.app.result.inputs.interest_rate, 0.06)

    def test_editing_price_does_not_move_rent_or_tax(self):
        """The whole point of requiring them: they are not price derivatives."""
        self.load_and_fill()
        self.app.f_price.var.set("400000")
        self.app.underwrite()
        self.assertEqual(self.app.f_rent.get(), 2400.0)
        self.assertEqual(self.app.f_tax.get(), 5245.0)
        self.assertEqual(self.app.result.inputs.monthly_rent, 2400.0)

    def test_hand_typed_address_not_duplicated(self):
        self.load_and_fill()
        self.app.f_address.var.set("12 Elm St")
        self.app.underwrite()
        self.assertEqual(self.app.listing.full_address, "12 Elm St")

    def test_comparison_accumulates(self):
        self.load_and_fill()
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


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestRequiredInputs(GUITestCase):
    """The GUI must refuse, visibly, rather than substitute a figure."""

    def test_refuses_to_underwrite_with_an_empty_rent_box(self):
        self.app.load_sample()
        self.app.f_tax.var.set("5245")
        self.app.f_rent.var.set("")
        self.app.underwrite()
        self.assertIsNone(self.app.result)
        self.assertIn("rent", self.app.status.get().lower())

    def test_refuses_a_zero_or_negative_rent(self):
        self.app.load_sample()
        self.app.f_tax.var.set("5245")
        for bad in ("0", "-100"):
            self.app.f_rent.var.set(bad)
            self.app.underwrite()
            self.assertIsNone(self.app.result, bad)
            self.assertIn("rent", self.app.status.get().lower())

    def test_refuses_to_underwrite_with_an_empty_tax_box(self):
        self.app.load_sample()
        self.app.f_rent.var.set("2400")
        self.app.f_tax.var.set("")
        self.app.underwrite()
        self.assertIsNone(self.app.result)
        self.assertIn("tax", self.app.status.get().lower())

    def test_refuses_a_zero_or_negative_tax(self):
        self.app.load_sample()
        self.app.f_rent.var.set("2400")
        for bad in ("0", "-1"):
            self.app.f_tax.var.set(bad)
            self.app.underwrite()
            self.assertIsNone(self.app.result, bad)
            self.assertIn("tax", self.app.status.get().lower())

    def test_loading_a_second_listing_clears_rent_and_tax(self):
        """
        A stale rent or tax carried over from the last property underwrites a
        deal that does not exist, and looks exactly like one that does.
        """
        self.load_and_fill(rent="2400", tax="5245")
        self.assertIsNotNone(self.app.result)

        self.app.load_sample()          # "next listing"
        self.root.update()
        self.assertEqual(self.app.f_rent.get_text(), "")
        self.assertEqual(self.app.f_tax.get_text(), "")

        # And it will not underwrite again until both are refilled.
        self.app.result = None
        self.app.underwrite()
        self.assertIsNone(self.app.result)

    def test_the_one_percent_button_and_estimator_button_are_gone(self):
        for attribute in ("apply_one_percent", "reestimate_tax",
                          "_tax_is_provided", "_refresh_tax_source_label"):
            self.assertFalse(hasattr(self.app, attribute), attribute)
        for field in ("f_seller_tax", "f_taxable", "f_sev", "f_homestead"):
            self.assertFalse(hasattr(self.app, field), field)

    def test_insurance_still_auto_fills_on_load(self):
        """It used to live inside reestimate_tax(); it must have moved, not died."""
        self.app.load_sample()
        self.root.update()
        self.assertGreater(self.app.f_insurance.get(0.0), 0.0)


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestPessimismDefaults(GUITestCase):
    def test_make_ready_defaults_to_the_higher_rule_on_load(self):
        from financial_engine import default_make_ready
        self.app.load_sample()
        self.root.update()
        self.assertEqual(self.app.f_capex0.get(), default_make_ready(239900))

    def test_lease_up_defaults_to_one_month(self):
        self.load_and_fill()
        self.assertEqual(self.app.result.inputs.lease_up_months, 1.0)

    def test_lease_up_box_drives_the_model(self):
        self.load_and_fill()
        base = self.app.result.year1_cash_flow
        self.app.f_lease_up.var.set("3")
        self.app.underwrite()
        self.assertEqual(self.app.result.inputs.lease_up_months, 3.0)
        self.assertLess(self.app.result.year1_cash_flow, base)

    def test_banner_labels_year_one_and_shows_stabilized(self):
        self.load_and_fill()
        text = self.app.metrics_label.cget("text")
        self.assertIn("Year 1 (incl. lease-up)", text)
        self.assertIn("Stabilized (year 2)", text)

    def test_exit_cap_box_drives_the_model(self):
        self.load_and_fill()
        default = self.app.result.exit_cap_rate_used
        self.app.f_exit_cap.var.set("8.0")
        self.app.underwrite()
        self.assertAlmostEqual(self.app.result.exit_cap_rate_used, 0.08)
        self.assertNotAlmostEqual(default, 0.08)


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestAfterTaxTab(GUITestCase):
    def test_after_tax_layer_is_computed_and_rendered(self):
        self.load_and_fill()
        self.assertIsNotNone(self.app.after_tax)
        text = self.app.txt_after_tax.get("1.0", "end")
        self.assertIn("AFTER TAX", text)
        self.assertIn("AFTER-TAX CASH FLOW", text)

    def test_tax_boxes_drive_the_layer(self):
        self.load_and_fill()
        self.assertAlmostEqual(self.app.after_tax.assumptions.ordinary_rate, 0.2625)
        self.app.f_city_rate.var.set("1.5")
        self.app.underwrite()
        self.assertAlmostEqual(self.app.after_tax.assumptions.ordinary_rate, 0.2775)

    def test_after_tax_is_not_screened_unless_the_box_is_ticked(self):
        self.load_and_fill()
        # The number is there on a fresh form; the tick is what turns it on.
        self.assertIsNone(self.app.thresholds_from_fields().min_after_tax_irr)
        self.app.f_min_at_irr.set_on(True)
        self.app.f_min_at_irr.var.set("6")
        self.assertAlmostEqual(self.app.thresholds_from_fields().min_after_tax_irr, 0.06)

    def test_passive_loss_toggle_changes_the_layer(self):
        self.load_and_fill()
        usable = self.app.after_tax.years[0].tax
        self.app.v_passive_usable.set(False)
        self.app.underwrite()
        self.assertEqual(self.app.after_tax.years[0].tax, 0.0)
        self.assertLess(usable, 0.0)


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestThresholdsAreAllExposed(GUITestCase):
    """Every threshold screen() enforces must have a box driving it."""

    def test_every_threshold_field_is_driven_by_the_form(self):
        """A threshold with no box behind it would be enforced invisibly."""
        import dataclasses
        from report import Thresholds
        defaults = Thresholds()
        from_form = self.app.thresholds_from_fields()
        for field in dataclasses.fields(Thresholds):
            self.assertEqual(getattr(from_form, field.name), getattr(defaults, field.name),
                             f"{field.name} is not wired to the form")

    def test_only_the_two_cash_tests_are_ticked_on_a_fresh_form(self):
        for widget in (self.app.f_min_coc, self.app.f_min_cf):
            self.assertTrue(widget.is_on())
        for widget in (self.app.f_min_dscr, self.app.f_min_cap, self.app.f_min_irr,
                       self.app.f_min_at_irr, self.app.f_max_be_occ):
            self.assertFalse(widget.is_on())

    def test_ticking_a_box_adds_that_check(self):
        from report import screen
        self.load_and_fill()
        self.assertNotIn("DSCR", screen(self.app.result, self.app.thresholds_from_fields()))
        self.app.f_min_dscr.set_on(True)
        self.assertIn("DSCR", screen(self.app.result, self.app.thresholds_from_fields()))

    def test_unticking_a_box_drops_that_check(self):
        from report import screen
        self.load_and_fill()
        self.app.f_min_cf.set_on(False)
        checks = screen(self.app.result, self.app.thresholds_from_fields())
        self.assertNotIn("Monthly Cash Flow", checks)
        # The number itself survives being switched off, so it comes back
        # unchanged when you tick the box again.
        self.assertEqual(self.app.thresholds_from_fields().min_monthly_cash_flow, 0.0)

    def test_the_entry_greys_out_while_its_box_is_unticked(self):
        self.assertEqual(str(self.app.f_min_dscr.entry.cget("state")), "disabled")
        self.app.f_min_dscr.set_on(True)
        self.assertEqual(str(self.app.f_min_dscr.entry.cget("state")), "normal")

    def test_breakeven_occupancy_screens_once_ticked(self):
        from report import screen
        self.load_and_fill()
        self.app.f_max_be_occ.set_on(True)
        self.app.f_max_be_occ.var.set("85")
        checks = screen(self.app.result, self.app.thresholds_from_fields())
        self.assertEqual(checks["Breakeven Occupancy"], "FAIL")

    def test_a_year_one_only_miss_reaches_the_banner_and_the_report(self):
        from report import PASS_LATER, screen
        self.load_and_fill()
        checks = screen(self.app.result, self.app.thresholds_from_fields())
        self.assertEqual(checks["Monthly Cash Flow"], PASS_LATER)
        self.assertIn("clearing from year 2", self.app.verdict_label.cget("text"))
        self.assertIn("YEAR 1 ONLY", self.app.txt_report.get("1.0", "end"))

    def test_cash_flow_box_changes_the_screen(self):
        from report import screen
        self.load_and_fill()
        # A bar no year of the hold clears is a plain miss, asterisk or not.
        self.app.f_min_cf.var.set("900")
        checks = screen(self.app.result, self.app.thresholds_from_fields())
        self.assertEqual(checks["Monthly Cash Flow"], "FAIL")
        # A landlord willing to feed the deal $300/mo through year 1.
        self.app.f_min_cf.var.set("-300")
        checks = screen(self.app.result, self.app.thresholds_from_fields())
        self.assertEqual(checks["Monthly Cash Flow"], "PASS")

    def test_breakeven_occupancy_is_reported_not_screened(self):
        from report import screen
        self.load_and_fill()
        self.assertNotIn("Breakeven Occupancy", screen(self.app.result,
                                                       self.app.thresholds_from_fields()))
        self.assertIn("Breakeven occ", self.app.metrics_label.cget("text"))


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestInputPanelScrolling(GUITestCase):
    """The left panel must scroll on the wheel, not only on the scrollbar."""

    def _canvas(self):
        import tkinter as tk
        found = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, tk.Canvas):
                    found.append(child)
                walk(child)

        walk(self.root)
        self.assertTrue(found, "input panel canvas not found")
        return found[0]

    def test_wheel_over_a_child_entry_scrolls_the_panel(self):
        """The pointer sits over an entry, never the canvas -- bind accordingly."""
        self.root.geometry("1200x400")
        self.root.update()
        canvas = self._canvas()
        start = canvas.yview()
        for _ in range(3):                      # X11 wheel-down
            self.app.f_rent.entry.event_generate("<Button-5>")
        self.root.update()
        self.assertGreater(canvas.yview()[0], start[0])
        for _ in range(3):                      # and back up
            self.app.f_rent.entry.event_generate("<Button-4>")
        self.root.update()
        self.assertAlmostEqual(canvas.yview()[0], start[0], places=6)

    def test_wheel_over_the_report_pane_leaves_the_panel_alone(self):
        self.root.geometry("1200x400")
        self.root.update()
        canvas = self._canvas()
        before = canvas.yview()
        self.app.txt_report.event_generate("<Button-5>")
        self.root.update()
        self.assertEqual(canvas.yview(), before)


@unittest.skipUnless(HAS_TK and HAS_DISPLAY, "needs tkinter and a display")
class TestRepairBumpIsAnOperatingExpense(GUITestCase):
    """It is an opex bump, not a second make-ready, and it sits with the opex."""

    def test_repair_bump_lives_in_the_operating_expenses_section(self):
        section = self.app.f_repair_bump.entry.master
        self.assertEqual(section.cget("text"), "Operating expenses")

    def test_make_ready_and_repair_bump_move_different_numbers(self):
        """Make-ready is cash in; the bump is year-1 NOI. Neither is the other."""
        self.load_and_fill()
        base = self.app.result

        self.app.f_capex0.var.set(str(base.inputs.initial_capex + 10000))
        self.app.underwrite()
        capex = self.app.result
        self.assertGreater(capex.total_cash_invested, base.total_cash_invested)
        self.assertEqual(round(capex.year1_cash_flow, 6),
                         round(base.year1_cash_flow, 6))

        self.app.f_capex0.var.set(str(base.inputs.initial_capex))
        self.app.f_repair_bump.var.set("5")
        self.app.underwrite()
        bump = self.app.result
        self.assertEqual(round(bump.total_cash_invested, 6),
                         round(base.total_cash_invested, 6))
        self.assertLess(bump.year1_cash_flow, base.year1_cash_flow)
        self.assertEqual(round(bump.years[1].noi, 6), round(base.years[1].noi, 6))


if __name__ == "__main__":
    unittest.main()
