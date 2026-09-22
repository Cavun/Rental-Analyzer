"""
Self-contained regression tests: python3 -m unittest discover tests

Covers the parts where a silent error would be expensive: loan math, the IRR
solver, the post-transfer tax correction, and the guarantee that sensitivity
runs never mutate the base inputs.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enrichment import EnrichmentAssumptions, enrich, to_property_inputs  # noqa: E402
from extraction import ListingData, parse_listing  # noqa: E402
from financial_engine import (  # noqa: E402
    OperatingExpenses,
    PropertyInputs,
    amortize_year,
    irr,
    monthly_payment,
    npv,
    underwrite,
)
from report import Thresholds, screen, render_table  # noqa: E402
from sample_listing import SAMPLE_LISTING_HTML  # noqa: E402
from sensitivity import interest_rate_grid, rent_growth_vs_vacancy, rent_level_grid  # noqa: E402


class TestLoanMath(unittest.TestCase):
    def test_known_payment(self):
        # $228,000 at 7% over 30 years = $1,516.89/mo
        self.assertAlmostEqual(monthly_payment(228000, 0.07, 30), 1516.89, places=2)

    def test_zero_rate(self):
        self.assertAlmostEqual(monthly_payment(120000, 0.0, 10), 1000.0, places=6)

    def test_no_loan(self):
        self.assertEqual(monthly_payment(0, 0.07, 30), 0.0)

    def test_amortization_reduces_balance(self):
        payment = monthly_payment(200000, 0.06, 30)
        interest, principal, balance = amortize_year(200000, 0.06, payment)
        self.assertAlmostEqual(interest + principal, payment * 12, places=4)
        self.assertLess(balance, 200000)
        self.assertGreater(interest, principal)  # year 1 is interest-heavy

    def test_loan_fully_amortizes(self):
        inputs = PropertyInputs(purchase_price=200000, loan_term_years=30, hold_years=30)
        result = underwrite(inputs)
        self.assertLess(result.years[-1].loan_balance, 1.0)


class TestIRR(unittest.TestCase):
    def test_simple_doubling(self):
        self.assertAlmostEqual(irr([-100, 0, 121]), 0.10, places=6)

    def test_matches_npv_zero(self):
        flows = [-55000, -2000, -1000, 500, 2000, 90000]
        rate = irr(flows)
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(npv(rate, flows), 0.0, places=4)

    def test_no_sign_change_returns_none(self):
        self.assertIsNone(irr([-100, -50, -25]))
        self.assertIsNone(irr([100, 50, 25]))

    def test_negative_irr_is_found(self):
        """A deal that loses money still has a real (negative) IRR."""
        flows = [-100000] + [5000] * 9 + [20000]
        rate = irr(flows)
        self.assertIsNotNone(rate)
        self.assertLess(rate, 0)
        self.assertAlmostEqual(npv(rate, flows), 0.0, places=4)


class TestExtraction(unittest.TestCase):
    def setUp(self):
        self.listing = parse_listing(SAMPLE_LISTING_HTML)

    def test_core_fields_from_json_blob(self):
        self.assertEqual(self.listing.price, 239900.0)
        self.assertEqual(self.listing.beds, 3.0)
        self.assertEqual(self.listing.mls_id, "26051188")
        self.assertEqual(self.listing.city, "Grand Rapids")
        self.assertEqual(self.listing.state, "MI")

    def test_detail_fields(self):
        self.assertEqual(self.listing.year_built, 1948)
        self.assertEqual(self.listing.county, "Kent")
        self.assertEqual(self.listing.lot_acres, 0.14)
        self.assertEqual(self.listing.sev, 94700.0)
        self.assertEqual(self.listing.taxable_value, 58420.0)

    def test_sellers_actual_tax_not_taxable_value(self):
        """The classic mistake: reading Taxable Value as the tax bill."""
        self.assertEqual(self.listing.property_tax_annual, 2184.0)
        self.assertNotEqual(self.listing.property_tax_annual, self.listing.taxable_value)

    def test_no_missing_critical_fields(self):
        self.assertEqual(self.listing.missing_fields, [])

    def test_plain_text_fallback(self):
        text = "123 Elm Street\n$185,000\n3 bd 2 ba 1,250 sqft\nBuilt 1975\nTaxes $2,400/yr\n45 days on market"
        parsed = parse_listing(text)
        self.assertEqual(parsed.price, 185000.0)
        self.assertEqual(parsed.beds, 3.0)
        self.assertEqual(parsed.year_built, 1975)
        self.assertEqual(parsed.property_tax_annual, 2400.0)


class TestEnrichment(unittest.TestCase):
    def test_rent_is_one_percent(self):
        enriched = enrich(parse_listing(SAMPLE_LISTING_HTML))
        self.assertAlmostEqual(enriched.monthly_rent, 2399.0, places=2)

    def test_tax_uncaps_to_sev_and_loses_homestead(self):
        listing = ListingData(price=285000, property_tax_annual=1925,
                              taxable_value=65737, sev=103300, homestead_pct=100)
        enriched = enrich(listing)
        # 1925 / 65.737 = 29.28 mills; +18 non-homestead; applied to SEV 103.3
        self.assertAlmostEqual(enriched.property_tax_annual, 4884, delta=5)
        self.assertGreater(enriched.property_tax_annual, listing.property_tax_annual * 2)
        self.assertTrue(any("2.5x" in w or "expect about" in w for w in enriched.warnings))

    def test_no_homestead_means_no_adder(self):
        listing = ListingData(price=285000, property_tax_annual=3000,
                              taxable_value=100000, sev=100000, homestead_pct=0)
        self.assertAlmostEqual(enrich(listing).property_tax_annual, 3000, delta=1)

    def test_provided_tax_is_used_verbatim(self):
        """A verified figure (e.g. from the MI estimator) overrides every estimate."""
        listing = ListingData(price=285000, property_tax_annual=1925,
                              taxable_value=65737, sev=103300, homestead_pct=100)
        enriched = enrich(listing, property_tax_annual=4312.55)
        self.assertEqual(enriched.property_tax_annual, 4312.55)
        self.assertEqual(enriched.property_tax_source, "provided")
        self.assertTrue(any("PROVIDED" in n for n in enriched.notes))

    def test_provided_tax_works_without_any_listing_tax_data(self):
        enriched = enrich(ListingData(price=200000), property_tax_annual=3800)
        self.assertEqual(enriched.property_tax_annual, 3800.0)
        self.assertEqual(enriched.property_tax_source, "provided")

    def test_provided_tax_far_from_estimate_warns(self):
        listing = ListingData(price=285000, property_tax_annual=1925,
                              taxable_value=65737, sev=103300, homestead_pct=100)
        enriched = enrich(listing, property_tax_annual=9000)
        self.assertTrue(any("differs sharply" in w for w in enriched.warnings))

    def test_provided_tax_close_to_estimate_does_not_warn(self):
        listing = ListingData(price=285000, property_tax_annual=1925,
                              taxable_value=65737, sev=103300, homestead_pct=100)
        enriched = enrich(listing, property_tax_annual=4750)
        self.assertFalse(any("differs sharply" in w for w in enriched.warnings))

    def test_negative_provided_tax_rejected(self):
        with self.assertRaises(ValueError):
            enrich(ListingData(price=200000), property_tax_annual=-1)

    def test_omitted_tax_still_estimates_as_before(self):
        listing = ListingData(price=285000, property_tax_annual=1925,
                              taxable_value=65737, sev=103300, homestead_pct=100)
        enriched = enrich(listing)
        self.assertEqual(enriched.property_tax_source, "estimated")
        self.assertAlmostEqual(enriched.property_tax_annual, 4884, delta=5)

    def test_missing_tax_data_falls_back_to_price(self):
        enriched = enrich(ListingData(price=200000))
        self.assertAlmostEqual(enriched.property_tax_annual, 3000, delta=1)
        self.assertTrue(enriched.warnings)

    def test_price_required(self):
        with self.assertRaises(ValueError):
            enrich(ListingData(price=0))


class TestUnderwriting(unittest.TestCase):
    def setUp(self):
        self.inputs = PropertyInputs(
            purchase_price=200000,
            expenses=OperatingExpenses(property_tax_annual=3000, insurance_annual=1200),
        )
        self.result = underwrite(self.inputs)

    def test_defaults_applied(self):
        self.assertEqual(self.inputs.monthly_rent, 2000.0)           # 1% rule
        self.assertEqual(self.inputs.down_payment, 40000.0)          # 20% down
        self.assertEqual(self.inputs.closing_costs, 6000.0)          # 3% closing
        self.assertEqual(self.inputs.total_cash_invested, 46000.0)
        self.assertAlmostEqual(self.inputs.vacancy_rate, 1 / 12)

    def test_metric_identities(self):
        y1 = self.result.years[0]
        self.assertAlmostEqual(self.result.cap_rate, y1.noi / 200000, places=9)
        self.assertAlmostEqual(self.result.cash_on_cash, y1.cash_flow / 46000, places=9)
        self.assertAlmostEqual(self.result.dscr, y1.noi / self.result.annual_debt_service, places=9)
        self.assertAlmostEqual(y1.cash_flow, y1.noi - y1.debt_service, places=6)
        self.assertAlmostEqual(y1.effective_gross_income, y1.gross_rent * (1 - 1 / 12), places=6)

    def test_breakeven_occupancy_consistent(self):
        """At breakeven occupancy, cash flow should be ~zero."""
        stressed = self.inputs.copy_with(vacancy_rate=1 - self.result.breakeven_occupancy)
        self.assertAlmostEqual(underwrite(stressed).year1_cash_flow, 0.0, delta=1.0)

    def test_horizon_and_growth(self):
        self.assertEqual(len(self.result.years), 30)
        self.assertGreater(self.result.years[1].gross_rent, self.result.years[0].gross_rent)

    def test_copy_with_is_independent(self):
        clone = self.inputs.copy_with(interest_rate=0.09)
        clone.expenses.property_tax_annual = 99999
        self.assertEqual(self.inputs.interest_rate, 0.07)
        self.assertEqual(self.inputs.expenses.property_tax_annual, 3000)


class TestSensitivity(unittest.TestCase):
    def setUp(self):
        self.inputs = to_property_inputs(enrich(parse_listing(SAMPLE_LISTING_HTML)))

    def test_base_inputs_never_mutated(self):
        snapshot = (self.inputs.interest_rate, self.inputs.vacancy_rate,
                    self.inputs.rent_growth, self.inputs.monthly_rent,
                    self.inputs.expenses.property_tax_annual)
        rent_growth_vs_vacancy(self.inputs)
        interest_rate_grid(self.inputs)
        rent_level_grid(self.inputs)
        self.assertEqual(
            snapshot,
            (self.inputs.interest_rate, self.inputs.vacancy_rate,
             self.inputs.rent_growth, self.inputs.monthly_rent,
             self.inputs.expenses.property_tax_annual),
        )

    def test_grid_shapes(self):
        grid = rent_growth_vs_vacancy(self.inputs)
        self.assertEqual(len(grid.cells), len(grid.row_values))
        self.assertEqual(len(grid.cells[0]), len(grid.col_values))

    def test_higher_rate_lowers_cash_flow(self):
        grid = interest_rate_grid(self.inputs)
        monthly_cf = [row[-1] for row in grid.cells]
        self.assertEqual(monthly_cf, sorted(monthly_cf, reverse=True))


class TestReport(unittest.TestCase):
    def test_screen_flags_bad_deal(self):
        result = underwrite(PropertyInputs(
            purchase_price=400000, monthly_rent=1500,
            expenses=OperatingExpenses(property_tax_annual=8000, insurance_annual=2000),
        ))
        checks = screen(result, Thresholds())
        self.assertEqual(checks["DSCR"], "FLAG")
        self.assertEqual(checks["Cash-on-Cash"], "FLAG")

    def test_screen_passes_strong_deal(self):
        result = underwrite(PropertyInputs(
            purchase_price=120000, monthly_rent=1800,
            expenses=OperatingExpenses(property_tax_annual=1500, insurance_annual=900),
        ))
        checks = screen(result, Thresholds())
        self.assertEqual(checks["DSCR"], "PASS")
        self.assertEqual(checks["Cash-on-Cash"], "PASS")

    def test_table_renders_aligned(self):
        table = render_table(["A", "B"], [["x", "1"], ["longer", "22"]])
        widths = {len(line) for line in table.splitlines()}
        self.assertEqual(len(widths), 1)


if __name__ == "__main__":
    unittest.main()


class TestPackagingFallbacks(unittest.TestCase):
    """
    A packaged app built by a Python without beautifulsoup4 used to die at
    import with a raw traceback. It must now degrade to a clear message and
    keep the text path working.
    """

    def test_text_parsing_works_without_bs4(self):
        import extraction
        original = extraction.BS4_AVAILABLE
        extraction.BS4_AVAILABLE = False
        try:
            parsed = extraction.parse_listing("42 Oak Ave\n$150,000\n3 bd 1 ba\nTaxes $1,800/yr")
            self.assertEqual(parsed.price, 150000.0)
        finally:
            extraction.BS4_AVAILABLE = original

    def test_html_parsing_raises_actionable_error_without_bs4(self):
        import extraction
        original = extraction.BS4_AVAILABLE
        extraction.BS4_AVAILABLE = False
        try:
            with self.assertRaises(extraction.ParserUnavailableError) as ctx:
                extraction.parse_listing(SAMPLE_LISTING_HTML)
            message = str(ctx.exception)
            self.assertIn("beautifulsoup4", message)
            self.assertIn("pip install", message)
        finally:
            extraction.BS4_AVAILABLE = original

    def test_spec_preflights_the_parser(self):
        """The spec must refuse to build without bs4 rather than ship a broken app."""
        spec = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "rental_analyzer.spec")
        with open(spec, encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("BUILD STOPPED", content)
        self.assertIn("collect_all", content)
