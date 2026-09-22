"""
After-tax layer tests: depreciation, the passive-loss carryforward and its
release at sale, and the recapture / capital-gains split on a hand-computed
example.

The governing rule this suite enforces: the after-tax layer READS the
projection and never changes a pre-tax number.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from financial_engine import OperatingExpenses, PropertyInputs, underwrite  # noqa: E402
from report import Thresholds, format_report, screen  # noqa: E402
from tax_engine import (  # noqa: E402
    PHASEOUT_END,
    PHASEOUT_START,
    TaxAssumptions,
    after_tax,
    annual_depreciation,
    depreciable_basis,
    total_basis,
)


def make_result(**overrides):
    base = dict(
        purchase_price=200000,
        monthly_rent=2000,
        initial_capex=0,
        expenses=OperatingExpenses(property_tax_annual=3000, insurance_annual=1200),
    )
    base.update(overrides)
    return underwrite(PropertyInputs(**base))


class TestDepreciation(unittest.TestCase):
    def setUp(self):
        self.result = make_result()
        self.assumptions = TaxAssumptions()

    def test_basis_is_price_plus_closing_plus_make_ready_times_building_share(self):
        result = make_result(initial_capex=5000)
        # 200,000 price + 6,000 closing + 5,000 make-ready = 211,000
        self.assertAlmostEqual(total_basis(result), 211000.0, places=6)
        self.assertAlmostEqual(depreciable_basis(result, TaxAssumptions()),
                               211000.0 * 0.80, places=6)

    def test_land_share_is_excluded(self):
        half = depreciable_basis(self.result, TaxAssumptions(building_share=0.50))
        full = depreciable_basis(self.result, TaxAssumptions(building_share=1.00))
        self.assertAlmostEqual(half * 2, full, places=6)

    def test_straight_line_over_twenty_seven_and_a_half_years(self):
        basis = depreciable_basis(self.result, self.assumptions)
        expected = basis / 27.5
        for year in (1, 2, 15, 27):
            self.assertAlmostEqual(annual_depreciation(basis, year, self.assumptions),
                                   expected, places=6)

    def test_depreciation_stops_at_full_recovery(self):
        basis = depreciable_basis(self.result, self.assumptions)
        # Year 28 takes the half-year stub, year 29 onward takes nothing.
        self.assertAlmostEqual(annual_depreciation(basis, 28, self.assumptions),
                               basis / 27.5 * 0.5, places=4)
        self.assertEqual(annual_depreciation(basis, 29, self.assumptions), 0.0)
        self.assertEqual(annual_depreciation(basis, 40, self.assumptions), 0.0)

    def test_total_depreciation_never_exceeds_basis(self):
        layer = after_tax(make_result(hold_years=30))
        self.assertAlmostEqual(layer.accumulated_depreciation,
                               layer.depreciable_basis, places=4)

    def test_mid_month_convention(self):
        basis = 275000.0
        july = TaxAssumptions(purchase_month=7)
        full = basis / 27.5
        # Mid-July: 12.5 - 7 = 5.5 months of the first year.
        self.assertAlmostEqual(annual_depreciation(basis, 1, july),
                               full * 5.5 / 12, places=6)
        self.assertAlmostEqual(annual_depreciation(basis, 2, july), full, places=6)

    def test_taxable_income_does_not_subtract_principal(self):
        layer = after_tax(self.result)
        row = layer.years[0]
        y1 = self.result.years[0]
        self.assertAlmostEqual(row.taxable_income,
                               y1.noi - y1.interest_paid - row.depreciation, places=6)
        # Principal is real money out, but it is not a deduction.
        self.assertGreater(y1.principal_paid, 0)
        self.assertNotAlmostEqual(row.taxable_income,
                                  y1.noi - y1.debt_service - row.depreciation, places=2)


class TestSuspendedLosses(unittest.TestCase):
    def test_usable_losses_are_a_benefit_in_the_year_they_arise(self):
        layer = after_tax(make_result(), TaxAssumptions(passive_losses_usable=True))
        row = layer.years[0]
        self.assertLess(row.taxable_income, 0)          # depreciation makes a paper loss
        self.assertLess(row.tax, 0)                     # so year 1 is a tax benefit
        self.assertEqual(row.suspended_balance, 0.0)    # nothing to carry forward
        self.assertGreater(row.after_tax_cash_flow, row.pre_tax_cash_flow)

    def test_unusable_losses_suspend_instead(self):
        layer = after_tax(make_result(), TaxAssumptions(passive_losses_usable=False))
        row = layer.years[0]
        # The loss is real but none of it is deductible this year, so the
        # taxable income that reaches the return is zero and the whole loss
        # goes to the carryforward.
        self.assertLess(row.noi - row.interest - row.depreciation, 0)
        self.assertAlmostEqual(row.taxable_income, 0.0, places=9)
        self.assertAlmostEqual(row.suspended_loss_added,
                               -(row.noi - row.interest - row.depreciation), places=6)
        self.assertEqual(row.tax, 0.0)                  # no benefit taken now
        self.assertAlmostEqual(row.suspended_balance, row.suspended_loss_added, places=6)
        self.assertGreater(row.suspended_balance, 0)
        self.assertEqual(row.after_tax_cash_flow, row.pre_tax_cash_flow)

    def test_carryforward_accumulates_then_offsets_future_income(self):
        layer = after_tax(make_result(hold_years=30),
                          TaxAssumptions(passive_losses_usable=False))
        balances = [row.suspended_balance for row in layer.years]
        peak = max(balances)
        self.assertGreater(peak, 0)
        # Once the deal turns taxable, the carryforward is spent down first
        # and no tax is paid until it is gone.
        spent = [row for row in layer.years if row.suspended_loss_used > 0]
        self.assertTrue(spent, "carryforward should eventually be used")
        for row in spent:
            if row.suspended_balance > 0:
                self.assertAlmostEqual(row.tax, 0.0, places=6)
        self.assertLess(balances[-1], peak)

    def test_suspended_balance_releases_at_sale(self):
        assumptions = TaxAssumptions(passive_losses_usable=False)
        layer = after_tax(make_result(hold_years=5), assumptions)
        suspended = layer.suspended_balance_at_sale
        self.assertGreater(suspended, 0)
        sale = layer.sale_appreciation
        self.assertAlmostEqual(sale.suspended_loss_released, suspended, places=6)
        self.assertAlmostEqual(sale.suspended_loss_benefit,
                               suspended * assumptions.ordinary_rate, places=6)
        # The release is a deduction, so it lowers the tax bill at exit.
        gross = (sale.recapture_tax + sale.capital_gains_tax
                 + sale.state_tax + sale.niit_tax)
        self.assertAlmostEqual(sale.total_tax, gross - sale.suspended_loss_benefit, places=6)
        self.assertLess(sale.total_tax, gross)

    def test_allowance_phases_out_between_100k_and_150k(self):
        self.assertEqual(TaxAssumptions(magi=90000).usable_allowance(), 25000.0)
        self.assertEqual(TaxAssumptions(magi=PHASEOUT_START).usable_allowance(), 25000.0)
        self.assertEqual(TaxAssumptions(magi=125000).usable_allowance(), 12500.0)
        self.assertEqual(TaxAssumptions(magi=PHASEOUT_END).usable_allowance(), 0.0)
        self.assertEqual(TaxAssumptions(magi=200000).usable_allowance(), 0.0)
        self.assertEqual(TaxAssumptions(magi=None).usable_allowance(), 25000.0)

    def test_phased_out_filer_suspends_like_a_passive_one(self):
        phased = after_tax(make_result(), TaxAssumptions(magi=200000))
        blocked = after_tax(make_result(), TaxAssumptions(passive_losses_usable=False))
        self.assertAlmostEqual(phased.years[0].suspended_balance,
                               blocked.years[0].suspended_balance, places=6)

    def test_loss_larger_than_the_allowance_splits(self):
        """A $30k loss against a $25k allowance: $25k now, $5k carried."""
        assumptions = TaxAssumptions(allowance=1000.0)
        layer = after_tax(make_result(), assumptions)
        row = layer.years[0]
        loss = -(row.noi - row.interest - row.depreciation)
        self.assertGreater(loss, 1000.0)
        self.assertAlmostEqual(row.taxable_income, -1000.0, places=6)
        self.assertAlmostEqual(row.suspended_loss_added, loss - 1000.0, places=6)


class TestSale(unittest.TestCase):
    def test_recapture_and_capital_gain_split_by_hand(self):
        """
        Hand-computed. Price 200,000 + 6,000 closing = 206,000 basis.
        Building share 80% -> 164,800 depreciable; 10 years at 164,800/27.5
        = 5,992.727/yr = 59,927.27 accumulated. Adjusted basis 146,072.73.
        """
        result = make_result(hold_years=10)
        assumptions = TaxAssumptions(
            building_share=0.80, capital_gains_rate=0.15,
            depreciation_recapture_rate=0.25, state_gain_rate=0.0425, niit=False,
            passive_losses_usable=False,
        )
        layer = after_tax(result, assumptions)

        self.assertAlmostEqual(layer.depreciable_basis, 164800.0, places=4)
        self.assertAlmostEqual(layer.accumulated_depreciation, 59927.2727, places=3)

        sale = layer.sale_appreciation
        self.assertAlmostEqual(sale.original_basis, 206000.0, places=4)
        self.assertAlmostEqual(sale.adjusted_basis, 206000.0 - 59927.2727, places=3)

        expected_net_sale = result.years[-1].property_value * 0.94
        self.assertAlmostEqual(sale.net_sale_price, expected_net_sale, places=4)
        self.assertAlmostEqual(sale.total_gain, expected_net_sale - sale.adjusted_basis,
                               places=4)

        # Everything up to accumulated depreciation is recaptured at 25%.
        self.assertAlmostEqual(sale.recapture_gain, 59927.2727, places=3)
        self.assertAlmostEqual(sale.recapture_tax, 59927.2727 * 0.25, places=3)
        # The remainder is long-term capital gain at 15%.
        self.assertAlmostEqual(sale.capital_gain, sale.total_gain - 59927.2727, places=3)
        self.assertAlmostEqual(sale.capital_gains_tax,
                               (sale.total_gain - 59927.2727) * 0.15, places=3)
        self.assertAlmostEqual(sale.state_tax, sale.total_gain * 0.0425, places=3)
        self.assertEqual(sale.niit_tax, 0.0)
        # Gain splits exactly: no dollar taxed twice, none missed.
        self.assertAlmostEqual(sale.recapture_gain + sale.capital_gain,
                               sale.total_gain, places=6)

    def test_niit_toggle_adds_three_point_eight_percent(self):
        result = make_result(hold_years=10)
        off = after_tax(result, TaxAssumptions(niit=False)).sale_appreciation
        on = after_tax(result, TaxAssumptions(niit=True)).sale_appreciation
        self.assertEqual(off.niit_tax, 0.0)
        self.assertAlmostEqual(on.niit_tax, on.total_gain * 0.038, places=4)
        self.assertAlmostEqual(on.total_tax - off.total_tax, on.niit_tax, places=4)

    def test_recapture_capped_by_the_gain_when_the_sale_is_weak(self):
        """Sell into a flat market: the gain can be smaller than the depreciation."""
        result = make_result(hold_years=3, appreciation=0.0)
        layer = after_tax(result, TaxAssumptions())
        sale = layer.sale_appreciation
        self.assertLess(sale.total_gain, layer.accumulated_depreciation)
        self.assertAlmostEqual(sale.recapture_gain, max(sale.total_gain, 0.0), places=6)
        self.assertLessEqual(sale.capital_gain, 0.0 + 1e-9)

    def test_both_exits_are_taxed(self):
        layer = after_tax(make_result(hold_years=10))
        self.assertEqual(layer.sale_appreciation.method, "Appreciation exit")
        self.assertEqual(layer.sale_cap_rate.method, "Cap-rate exit")
        self.assertAlmostEqual(layer.sale_appreciation.accumulated_depreciation,
                               layer.sale_cap_rate.accumulated_depreciation, places=6)

    def test_after_tax_proceeds_are_net_of_payoff_and_tax(self):
        layer = after_tax(make_result(hold_years=10))
        sale = layer.sale_appreciation
        self.assertAlmostEqual(sale.net_proceeds,
                               sale.net_sale_price - sale.loan_balance, places=6)
        self.assertAlmostEqual(sale.after_tax_proceeds,
                               sale.net_proceeds - sale.total_tax, places=6)


class TestOutputs(unittest.TestCase):
    def setUp(self):
        self.result = make_result()
        self.layer = after_tax(self.result)

    def test_pre_tax_result_is_untouched(self):
        before = (self.result.cap_rate, self.result.dscr, self.result.irr_screened,
                  self.result.year1_cash_flow, self.result.total_cash_invested)
        after_tax(self.result, TaxAssumptions(federal_ordinary_rate=0.37))
        after = (self.result.cap_rate, self.result.dscr, self.result.irr_screened,
                 self.result.year1_cash_flow, self.result.total_cash_invested)
        self.assertEqual(before, after)

    def test_year_one_after_tax_cash_flow(self):
        row = self.layer.years[0]
        self.assertAlmostEqual(self.layer.year1_after_tax_cash_flow,
                               row.pre_tax_cash_flow - row.tax, places=6)

    def test_after_tax_irr_for_both_exits(self):
        self.assertIsNotNone(self.layer.irr_appreciation_exit)
        self.assertIsNotNone(self.layer.irr_cap_rate_exit)
        self.assertEqual(self.layer.irr_screened,
                         min(self.layer.irr_appreciation_exit,
                             self.layer.irr_cap_rate_exit))

    def test_after_tax_irr_is_below_pre_tax_irr(self):
        self.assertLess(self.layer.irr_screened, self.result.irr_screened)

    def test_ordinary_rate_combines_federal_state_and_city(self):
        a = TaxAssumptions(federal_ordinary_rate=0.22, state_ordinary_rate=0.0425,
                           city_ordinary_rate=0.01)
        self.assertAlmostEqual(a.ordinary_rate, 0.2725, places=6)

    def test_step_up_at_death_is_noted(self):
        self.assertTrue(any("step-up at death" in n for n in self.layer.notes))
        self.assertTrue(any("TAXABLE SALE" in n for n in self.layer.notes))

    def test_capex_reserve_simplification_is_noted(self):
        self.assertTrue(any("capitalized" in n.lower() for n in self.layer.notes))

    def test_loan_cost_simplification_is_noted(self):
        self.assertTrue(any("amortize over the" in n for n in self.layer.notes))

    def test_after_tax_is_not_screened_by_default(self):
        self.assertIsNone(Thresholds().min_after_tax_irr)
        self.assertNotIn("After-tax IRR", screen(self.result, Thresholds(), self.layer))

    def test_after_tax_threshold_screens_when_set(self):
        low = Thresholds(min_after_tax_irr=self.layer.irr_screened - 0.01)
        high = Thresholds(min_after_tax_irr=self.layer.irr_screened + 0.01)
        self.assertEqual(screen(self.result, low, self.layer)["After-tax IRR"], "PASS")
        self.assertEqual(screen(self.result, high, self.layer)["After-tax IRR"], "FAIL")

    def test_report_renders_the_after_tax_section(self):
        text = format_report(self.result, None, Thresholds(), after_tax=self.layer)
        self.assertIn("AFTER TAX (reported, not screened by default)", text)
        self.assertIn("AFTER-TAX CASH FLOW", text)
        self.assertIn("Depreciation recapture", text)
        self.assertIn("step-up at death", text)

    def test_report_omits_the_section_when_no_layer_is_supplied(self):
        self.assertNotIn("AFTER TAX", format_report(self.result, None, Thresholds()))


class TestValidation(unittest.TestCase):
    def test_building_share_bounds(self):
        for bad in (0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                TaxAssumptions(building_share=bad)

    def test_purchase_month_bounds(self):
        for bad in (0, 13, -1):
            with self.assertRaises(ValueError):
                TaxAssumptions(purchase_month=bad)
        TaxAssumptions(purchase_month=1)
        TaxAssumptions(purchase_month=12)


if __name__ == "__main__":
    unittest.main()
