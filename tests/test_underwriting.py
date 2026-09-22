"""
Self-contained regression tests: python3 -m unittest discover tests

Covers the parts where a silent error would be expensive: loan math, the IRR
solver, the refusal to invent a rent or a tax figure, the year-1 pessimism
levers, end-of-year appreciation, the two exit methods, and the guarantee
that sensitivity runs never mutate the base inputs.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enrichment import (  # noqa: E402
    EnrichmentAssumptions,
    enrich,
    listing_warnings,
    tax_sanity_warning,
    to_property_inputs,
)
from extraction import CRITICAL_FIELDS, ListingData, parse_listing  # noqa: E402
from financial_engine import (  # noqa: E402
    OperatingExpenses,
    PropertyInputs,
    amortize_year,
    count_sign_changes,
    default_make_ready,
    irr,
    monthly_payment,
    npv,
    project,
    underwrite,
)
from report import (OFF, PASS_LATER, Thresholds,  # noqa: E402
                    cash_flow_asterisk_note, cash_flow_clears_later, format_report,
                    one_line_summary, render_table, screen, verdict)
from sample_listing import SAMPLE_LISTING_HTML  # noqa: E402
from sensitivity import (  # noqa: E402
    RENT_LEVEL_DELTAS,
    breakeven_rent,
    interest_rate_grid,
    rent_growth_vs_vacancy,
    rent_level_grid,
)


def make_inputs(**overrides) -> PropertyInputs:
    """A plain, valid deal. Every knob explicit so a default change is visible."""
    base = dict(
        purchase_price=200000,
        monthly_rent=2000,
        expenses=OperatingExpenses(property_tax_annual=3000, insurance_annual=1200),
    )
    base.update(overrides)
    return PropertyInputs(**base)


def sample_enriched(**overrides):
    kwargs = dict(property_tax_annual=5245, monthly_rent=2400)
    kwargs.update(overrides)
    return enrich(parse_listing(SAMPLE_LISTING_HTML), **kwargs)


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
        result = underwrite(make_inputs(loan_term_years=30, hold_years=30))
        self.assertLess(result.years[-1].loan_balance, 1.0)


class TestIRR(unittest.TestCase):
    def test_simple_doubling(self):
        self.assertAlmostEqual(irr([-100, 0, 121]), 0.10, places=6)

    def test_textbook_series(self):
        """$1,000 out, $500 back three times: the IRR is 23.375%."""
        rate = irr([-1000, 500, 500, 500])
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(rate, 0.23375, places=4)
        self.assertAlmostEqual(npv(rate, [-1000, 500, 500, 500]), 0.0, places=6)

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

    def test_newton_exhaustion_falls_through_to_bisection(self):
        """
        max_iter=1 guarantees Newton cannot converge. The old solver returned
        None here; bisection must still find the root.
        """
        flows = [-1000, 500, 500, 500]
        rate = irr(flows, max_iter=1)
        self.assertIsNotNone(rate, "Newton exhaustion must fall through to bisection")
        self.assertAlmostEqual(npv(rate, flows), 0.0, places=4)
        self.assertAlmostEqual(rate, irr(flows), places=6)

    def test_root_outside_the_naive_bracket_endpoints(self):
        """
        The endpoints of [-0.99, 10] do not bracket this root by sign; only a
        scan finds it. The old fallback returned None.
        """
        flows = [-100, 400, -400]   # NPV <= 0 at both ends, touches zero between
        self.assertEqual(count_sign_changes(flows), 2)
        rate = irr(flows)
        self.assertIsNotNone(rate)

    def test_two_sign_changes_are_counted(self):
        self.assertEqual(count_sign_changes([-1000, 6000, -6000]), 2)
        self.assertEqual(count_sign_changes([-1000, 500, 500, 500]), 1)
        self.assertEqual(count_sign_changes([-1000, 0, 0, 1200]), 1)

    def test_multiple_irr_series_returns_root_nearest_guess(self):
        # Two real roots: 26.8% and 373.2%. Neither is "the" IRR.
        flows = [-1000, 6000, -6000]
        self.assertEqual(count_sign_changes(flows), 2)
        rate = irr(flows, guess=0.10)
        self.assertIsNotNone(rate)
        self.assertAlmostEqual(npv(rate, flows), 0.0, places=4)
        self.assertAlmostEqual(rate, 0.2676, places=3)     # the near root, not 3.73
        # And the far root when the guess sits nearer to it.
        self.assertAlmostEqual(irr(flows, guess=4.0), 3.7324, places=3)
        # Deterministic: same guess, same answer, every time.
        self.assertEqual(rate, irr(flows, guess=0.10))

    def test_multiple_irr_flag_is_reported(self):
        self.assertFalse(underwrite(make_inputs()).multiple_irr_possible)

    def test_multiple_irr_flag_trips_on_a_sign_flipping_deal(self):
        """
        Expenses outrunning flat rent send cash flow positive, then negative,
        then positive again at the sale: three sign changes, several possible
        IRRs, and the report must say so rather than print one and move on.
        """
        result = underwrite(make_inputs(hold_years=20, rent_growth=0.0,
                                        expense_growth=0.15))
        flows = ([-result.total_cash_invested] + [y.cash_flow for y in result.years])
        flows[-1] += result.net_sale_appreciation
        self.assertGreater(count_sign_changes(flows), 1)
        self.assertTrue(result.multiple_irr_possible)
        text = format_report(result, None, Thresholds())
        self.assertIn("changes sign more than once", text)


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

    def test_sellers_actual_tax_still_parsed_for_reference(self):
        """Parsed, shown, never used: the classic mistake is computing with it."""
        self.assertEqual(self.listing.property_tax_annual, 2184.0)
        self.assertNotEqual(self.listing.property_tax_annual, self.listing.taxable_value)

    def test_tax_is_not_a_critical_field(self):
        """
        The seller's bill is reference data. Warning that a listing omitted it
        would be nagging about an input we refuse to use.
        """
        self.assertNotIn("property_tax_annual", CRITICAL_FIELDS)
        parsed = parse_listing("9 Oak Ave\n$150,000\n3 bd 2 ba 1,000 sqft\nBuilt 1975")
        self.assertNotIn("property_tax_annual", parsed.missing_fields)

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
    def test_rent_and_tax_are_used_verbatim(self):
        enriched = sample_enriched()
        self.assertEqual(enriched.monthly_rent, 2400.0)
        self.assertEqual(enriched.property_tax_annual, 5245.0)
        self.assertTrue(any("your figure" in n for n in enriched.notes))

    def test_enrich_raises_without_a_tax_figure(self):
        listing = ListingData(price=285000, property_tax_annual=1925,
                              taxable_value=65737, sev=103300, homestead_pct=100)
        with self.assertRaises(TypeError):
            enrich(listing, monthly_rent=2000)          # omitted entirely
        for bad in (None, 0, -1):
            with self.assertRaises(ValueError):
                enrich(listing, property_tax_annual=bad, monthly_rent=2000)

    def test_enrich_raises_without_a_rent(self):
        listing = ListingData(price=285000)
        with self.assertRaises(TypeError):
            enrich(listing, property_tax_annual=4000)   # omitted entirely
        for bad in (None, 0, -1):
            with self.assertRaises(ValueError):
                enrich(listing, property_tax_annual=4000, monthly_rent=bad)

    def test_sellers_tax_never_drives_the_number(self):
        """A wildly different seller bill must not move the underwritten tax."""
        cheap = ListingData(price=285000, property_tax_annual=500)
        dear = ListingData(price=285000, property_tax_annual=9000)
        self.assertEqual(enrich(cheap, property_tax_annual=4312.55, monthly_rent=2200)
                         .property_tax_annual,
                         enrich(dear, property_tax_annual=4312.55, monthly_rent=2200)
                         .property_tax_annual)

    def test_no_estimate_language_anywhere_in_the_tax_notes(self):
        enriched = sample_enriched()
        tax_notes = [n for n in enriched.notes if "tax" in n.lower()]
        self.assertTrue(tax_notes)
        for note in tax_notes:
            self.assertNotIn("estimate", note.lower())

    def test_tax_sanity_warning_catches_a_monthly_figure(self):
        # $437/mo typed where $5,245/yr belongs: 0.18% of price.
        self.assertIsNotNone(tax_sanity_warning(437, 239900))
        self.assertIn("low", tax_sanity_warning(437, 239900))

    def test_tax_sanity_warning_catches_an_extra_zero(self):
        self.assertIsNotNone(tax_sanity_warning(52450, 239900))
        self.assertIn("high", tax_sanity_warning(52450, 239900))

    def test_tax_sanity_warning_silent_in_the_normal_band(self):
        for tax in (1500, 3000, 5245, 8000):
            self.assertIsNone(tax_sanity_warning(tax, 239900))

    def test_sanity_warning_is_not_blocking(self):
        enriched = enrich(ListingData(price=200000), property_tax_annual=50,
                          monthly_rent=1800)
        self.assertEqual(enriched.property_tax_annual, 50.0)   # used anyway
        self.assertTrue(any("unusually low" in w for w in enriched.warnings))

    def test_insurance_estimated_unless_supplied(self):
        listing = parse_listing(SAMPLE_LISTING_HTML)
        estimated = enrich(listing, property_tax_annual=5245, monthly_rent=2400)
        self.assertGreater(estimated.insurance_annual, 0)
        supplied = enrich(listing, property_tax_annual=5245, monthly_rent=2400,
                          insurance_annual=900)
        self.assertEqual(supplied.insurance_annual, 900.0)
        self.assertTrue(any("Insurance (your figure)" in n for n in supplied.notes))

    def test_listing_warnings_computes_nothing(self):
        """The GUI uses this on its own; it must need no assumptions at all."""
        warnings = listing_warnings(ListingData(price=200000, year_built=1948,
                                                zoning="LDR", mls_status="Pending"))
        self.assertTrue(any("1948" in w for w in warnings))
        self.assertTrue(any("LDR" in w for w in warnings))
        self.assertTrue(any("Pending" in w for w in warnings))

    def test_price_required(self):
        with self.assertRaises(ValueError):
            enrich(ListingData(price=0), property_tax_annual=3000, monthly_rent=1500)


class TestRentIsRequired(unittest.TestCase):
    def test_property_inputs_raises_without_rent(self):
        with self.assertRaises(TypeError):
            PropertyInputs(purchase_price=200000)          # omitted entirely
        for bad in (None, 0, -50):
            with self.assertRaises(ValueError) as ctx:
                PropertyInputs(purchase_price=200000, monthly_rent=bad)
            self.assertIn("monthly_rent is required", str(ctx.exception))

    def test_no_one_percent_default_survives_anywhere(self):
        """Rent must never be a function of price again."""
        import financial_engine
        import enrichment
        self.assertFalse(hasattr(financial_engine, "RENT_PCT_OF_PRICE"))
        self.assertFalse(hasattr(enrichment, "estimate_rent"))
        self.assertFalse(hasattr(enrichment.EnrichmentAssumptions(), "rent_pct_of_price"))

    def test_one_percent_rule_survives_as_a_reference_ratio(self):
        result = underwrite(make_inputs(purchase_price=200000, monthly_rent=2360))
        self.assertAlmostEqual(result.one_percent_rule, 0.0118, places=6)


class TestTaxEstimatorIsGone(unittest.TestCase):
    def test_estimator_and_its_knobs_are_deleted(self):
        import enrichment
        self.assertFalse(hasattr(enrichment, "estimate_post_transfer_tax"))
        assumptions = enrichment.EnrichmentAssumptions()
        for knob in ("uncap_taxable_to_sev", "non_homestead_mills_adder",
                     "fallback_tax_rate_pct_of_price", "tax_growth_note_mills_cap"):
            self.assertFalse(hasattr(assumptions, knob), knob)
        # The link to the real estimator stays.
        self.assertIn("ptestimator", assumptions.tax_estimator_url)

    def test_enriched_listing_has_no_tax_source(self):
        self.assertFalse(hasattr(sample_enriched(), "property_tax_source"))

    def test_report_says_your_figure_not_estimated(self):
        enriched = sample_enriched()
        inputs = to_property_inputs(enriched)
        text = format_report(underwrite(inputs), enriched, Thresholds())
        self.assertIn("your figure", text)
        self.assertNotIn("post-transfer estimate", text)
        self.assertNotIn("VERIFIED (provided)", text)


class TestYearOnePessimism(unittest.TestCase):
    def test_year1_vacancy_is_base_plus_lease_up(self):
        inputs = make_inputs(vacancy_rate=1 / 12, lease_up_months=1)
        years = project(inputs)
        expected = inputs.monthly_rent * 12 * (1 / 12) + inputs.monthly_rent * 1
        self.assertAlmostEqual(years[0].vacancy_loss, expected, places=6)

    def test_year2_vacancy_is_base_only(self):
        inputs = make_inputs(vacancy_rate=1 / 12, lease_up_months=1)
        years = project(inputs)
        self.assertAlmostEqual(years[1].vacancy_loss,
                               years[1].gross_rent * (1 / 12), places=6)

    def test_lease_up_only_bites_year_one(self):
        with_lease_up = underwrite(make_inputs(lease_up_months=2))
        without = underwrite(make_inputs(lease_up_months=0))
        self.assertLess(with_lease_up.year1_cash_flow, without.year1_cash_flow)
        self.assertAlmostEqual(with_lease_up.years[1].cash_flow,
                               without.years[1].cash_flow, places=6)

    def test_lease_up_bounds_are_validated(self):
        for bad in (-1, 12, 13):
            with self.assertRaises(ValueError):
                make_inputs(lease_up_months=bad)

    def test_total_year1_vacancy_must_stay_under_twelve_months(self):
        # 6 months of steady-state vacancy + 6 months of lease-up == the year.
        with self.assertRaises(ValueError) as ctx:
            make_inputs(vacancy_rate=0.5, lease_up_months=6)
        self.assertIn("under 12 months", str(ctx.exception))
        make_inputs(vacancy_rate=0.5, lease_up_months=5)   # fine

    def test_make_ready_default_is_the_higher_of_the_two_rules(self):
        self.assertEqual(default_make_ready(150000), 2500.0)    # 1% = 1,500 -> floor
        self.assertEqual(default_make_ready(400000), 4000.0)    # 1% = 4,000 -> pct
        self.assertEqual(default_make_ready(250000), 2500.0)    # exactly equal

    def test_make_ready_lands_in_total_cash_invested(self):
        inputs = make_inputs(initial_capex=2500)
        self.assertEqual(inputs.total_cash_invested,
                         inputs.down_payment + inputs.closing_costs + 2500)
        self.assertEqual(underwrite(inputs).total_cash_invested, 48500.0)

    def test_make_ready_is_in_the_cap_rate_basis(self):
        without = underwrite(make_inputs(initial_capex=0))
        with_capex = underwrite(make_inputs(initial_capex=20000))
        self.assertAlmostEqual(with_capex.cap_rate,
                               with_capex.year1_noi / 220000, places=9)
        self.assertLess(with_capex.cap_rate, without.cap_rate)

    def test_year1_repair_bump_is_year_one_only(self):
        bumped = underwrite(make_inputs(year1_repair_bump_pct=0.04))
        plain = underwrite(make_inputs(year1_repair_bump_pct=0.0))
        self.assertLess(bumped.year1_noi, plain.year1_noi)
        self.assertAlmostEqual(bumped.years[1].noi, plain.years[1].noi, places=6)
        self.assertAlmostEqual(
            plain.year1_noi - bumped.year1_noi,
            bumped.years[0].effective_gross_income * 0.04, places=6)

    def test_all_pessimism_knobs_at_zero_reproduces_the_old_numbers(self):
        """
        The classic model: no lease-up, no make-ready, no repair bump. These
        are the figures the engine produced before item 5, so a stray change
        to the default path shows up here.
        """
        result = underwrite(make_inputs(lease_up_months=0, initial_capex=0,
                                        year1_repair_bump_pct=0.0))
        y1 = result.years[0]
        self.assertAlmostEqual(y1.gross_rent, 24000.0, places=6)
        self.assertAlmostEqual(y1.vacancy_loss, 2000.0, places=6)
        self.assertAlmostEqual(y1.effective_gross_income, 22000.0, places=6)
        self.assertAlmostEqual(result.total_cash_invested, 46000.0, places=6)
        self.assertAlmostEqual(result.cap_rate, y1.noi / 200000, places=9)

    def test_stabilized_reference_is_year_two(self):
        result = underwrite(make_inputs(lease_up_months=1))
        y2 = result.years[1]
        self.assertAlmostEqual(result.stabilized_noi, y2.noi, places=9)
        self.assertAlmostEqual(result.stabilized_cash_flow, y2.cash_flow, places=9)
        self.assertAlmostEqual(result.stabilized_dscr,
                               y2.noi / result.annual_debt_service, places=9)
        self.assertAlmostEqual(result.stabilized_cash_on_cash,
                               y2.cash_flow / result.total_cash_invested, places=9)
        # Lease-up is what makes year 1 the worse of the two.
        self.assertGreater(result.stabilized_dscr, result.dscr)

    def test_verdict_screens_on_year_one(self):
        """A deal that clears stabilized but fails year 1 must still flag."""
        result = underwrite(make_inputs(purchase_price=200000, monthly_rent=1700,
                                        lease_up_months=3))
        self.assertLess(result.dscr, result.stabilized_dscr)
        checks = screen(result, Thresholds(min_dscr=result.stabilized_dscr - 0.01,
                                           screen_dscr=True))
        self.assertEqual(checks["DSCR"], "FAIL")

    def test_report_labels_year_one_and_shows_stabilized(self):
        text = format_report(underwrite(make_inputs()), None, Thresholds())
        self.assertIn("YEAR 1 (incl. lease-up)", text)
        self.assertIn("Stabilized (year 2", text)

    def test_breakeven_text_accounts_for_lease_up(self):
        text = format_report(underwrite(make_inputs(lease_up_months=1)), None, Thresholds())
        self.assertIn("lease-up", text)
        # The sentence wraps, so match the tail that always lands on one line.
        self.assertIn("month(s) of the 12", text)
        self.assertIn("beyond the lease-up", text)


class TestAppreciation(unittest.TestCase):
    def test_year_one_value_includes_a_year_of_growth(self):
        inputs = make_inputs(appreciation=0.03)
        years = project(inputs)
        self.assertAlmostEqual(years[0].property_value, 200000 * 1.03, places=6)
        self.assertNotAlmostEqual(years[0].property_value, 200000, places=2)

    def test_year_thirty_value_is_price_compounded(self):
        years = project(make_inputs(appreciation=0.03, hold_years=30))
        self.assertAlmostEqual(years[-1].property_value, 200000 * (1.03 ** 30), places=4)

    def test_every_year_is_end_of_year(self):
        years = project(make_inputs(appreciation=0.03, hold_years=10))
        for y in years:
            self.assertAlmostEqual(y.property_value, 200000 * (1.03 ** y.year), places=4)

    def test_year_one_equity_carries_the_appreciation(self):
        y1 = project(make_inputs(appreciation=0.03))[0]
        self.assertAlmostEqual(y1.equity, y1.property_value - y1.loan_balance, places=6)
        self.assertGreater(y1.equity, 200000 - 160000)

    def test_zero_appreciation_holds_value_flat(self):
        years = project(make_inputs(appreciation=0.0, hold_years=5))
        for y in years:
            self.assertAlmostEqual(y.property_value, 200000, places=6)


class TestExitsAndIRR(unittest.TestCase):
    def setUp(self):
        self.inputs = make_inputs()
        self.result = underwrite(self.inputs)

    def test_cash_flow_only_irr_is_gone(self):
        self.assertFalse(hasattr(self.result, "irr_cash_flow_only"))
        self.assertFalse(hasattr(self.result, "irr_with_equity"))

    def test_default_exit_cap_is_year_one_cap_plus_fifty_bps(self):
        self.assertAlmostEqual(self.result.exit_cap_rate_used,
                               self.result.cap_rate + 0.005, places=9)

    def test_explicit_exit_cap_is_honoured(self):
        result = underwrite(make_inputs(exit_cap_rate=0.08))
        self.assertAlmostEqual(result.exit_cap_rate_used, 0.08, places=9)

    def test_terminal_value_is_next_year_noi_over_exit_cap(self):
        """NOI(N+1) comes from one extra projection year, not from the table."""
        extra = project(self.inputs, years=self.inputs.hold_years + 1)
        self.assertAlmostEqual(self.result.terminal_noi, extra[-1].noi, places=6)
        self.assertEqual(len(self.result.years), self.inputs.hold_years)  # not appended
        self.assertAlmostEqual(
            self.result.terminal_value_cap_rate,
            self.result.terminal_noi / self.result.exit_cap_rate_used, places=4)

    def test_both_sales_are_net_of_costs_and_payoff(self):
        last = self.result.years[-1]
        self.assertAlmostEqual(
            self.result.net_sale_appreciation,
            last.property_value * 0.94 - last.loan_balance, places=4)
        self.assertAlmostEqual(
            self.result.net_sale_cap_rate,
            self.result.terminal_value_cap_rate * 0.94 - last.loan_balance, places=4)

    def test_screened_irr_is_the_lower_of_the_two(self):
        self.assertEqual(
            self.result.irr_screened,
            min(self.result.irr_appreciation_exit, self.result.irr_cap_rate_exit))

    def test_disagreement_flag_trips_when_the_methods_diverge(self):
        agree = underwrite(make_inputs())
        # A punitive exit cap crushes the cap-rate value against an unchanged
        # appreciation value.
        diverge = underwrite(make_inputs(exit_cap_rate=0.20))
        self.assertFalse(agree.exit_values_disagree)
        self.assertTrue(diverge.exit_values_disagree)

    def test_report_prints_both_exits(self):
        text = format_report(self.result, None, Thresholds())
        self.assertIn("EXIT -- TWO WAYS OF VALUING THE SAME ASSET", text)
        self.assertIn("Appreciation-based", text)
        self.assertIn("Cap-rate-based", text)

    def test_disagreement_is_reported(self):
        text = format_report(underwrite(make_inputs(exit_cap_rate=0.20)), None, Thresholds())
        self.assertIn("differ by more than 25%", text)

    def test_sensitivity_irr_metric_points_at_the_screened_value(self):
        from sensitivity import METRICS
        self.assertEqual(METRICS["IRR"](self.result), self.result.irr_screened)


class TestUnderwriting(unittest.TestCase):
    def setUp(self):
        self.inputs = make_inputs()
        self.result = underwrite(self.inputs)

    def test_defaults_applied(self):
        self.assertEqual(self.inputs.monthly_rent, 2000.0)           # required, not derived
        self.assertEqual(self.inputs.down_payment, 40000.0)          # 20% down
        self.assertEqual(self.inputs.closing_costs, 6000.0)          # 3% closing
        self.assertAlmostEqual(self.inputs.vacancy_rate, 1 / 12)
        self.assertEqual(self.inputs.lease_up_months, 1.0)

    def test_metric_identities(self):
        y1 = self.result.years[0]
        basis = 200000 + self.inputs.initial_capex
        self.assertAlmostEqual(self.result.cap_rate, y1.noi / basis, places=9)
        self.assertAlmostEqual(self.result.cash_on_cash,
                               y1.cash_flow / self.inputs.total_cash_invested, places=9)
        self.assertAlmostEqual(self.result.dscr, y1.noi / self.result.annual_debt_service, places=9)
        self.assertAlmostEqual(y1.cash_flow, y1.noi - y1.debt_service, places=6)

    def test_breakeven_occupancy_consistent(self):
        """At breakeven occupancy, year-1 cash flow should be ~zero."""
        # Breakeven occupancy already charges the lease-up months, so the
        # stress case keeps them and moves only the steady-state rate.
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
        self.inputs = to_property_inputs(sample_enriched())

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

    def test_rent_grid_is_centred_on_the_entered_rent(self):
        grid = rent_level_grid(self.inputs)
        self.assertEqual(len(grid.row_values), len(RENT_LEVEL_DELTAS))
        for delta, rent in zip(RENT_LEVEL_DELTAS, grid.row_values):
            self.assertAlmostEqual(rent, self.inputs.monthly_rent * (1 + delta), places=6)

    def test_rent_grid_zero_row_equals_the_base_case_exactly(self):
        grid = rent_level_grid(self.inputs)
        zero_row = grid.cells[RENT_LEVEL_DELTAS.index(0.00)]
        base = underwrite(self.inputs)
        metrics = ("Cap Rate", "Cash-on-Cash", "DSCR", "IRR", "Monthly CF")
        expected = [base.cap_rate, base.cash_on_cash, base.dscr,
                    base.irr_screened, base.monthly_cash_flow]
        for name, got, want in zip(metrics, zero_row, expected):
            self.assertAlmostEqual(got, want, places=12, msg=name)

    def test_rent_grid_rows_are_labelled_in_dollars_and_ratio(self):
        from report import render_grid
        text = render_grid(rent_level_grid(self.inputs))
        self.assertIn("/mo", text)
        self.assertIn("+0%", text)
        self.assertIn("-20%", text)

    def test_breakeven_rent_includes_lease_up(self):
        """Year 1 carries lease-up, so the breakeven rent must too."""
        with_lease_up = breakeven_rent(self.inputs.copy_with(lease_up_months=1))
        without = breakeven_rent(self.inputs.copy_with(lease_up_months=0))
        self.assertGreater(with_lease_up, without)


class TestReport(unittest.TestCase):
    def test_screen_flags_bad_deal(self):
        result = underwrite(PropertyInputs(
            purchase_price=400000, monthly_rent=1500,
            expenses=OperatingExpenses(property_tax_annual=8000, insurance_annual=2000),
        ))
        checks = screen(result, Thresholds(screen_dscr=True))
        self.assertEqual(checks["DSCR"], "FAIL")
        self.assertEqual(checks["Cash-on-Cash"], "FAIL")

    def test_verdict_says_pass_only_when_every_threshold_clears(self):
        """PASS is reserved for deals that clear. A miss reads FAIL, not 'pass'."""
        self.assertTrue(verdict({"DSCR": "PASS", "IRR": "PASS"}).startswith("PASS"))
        marginal = verdict({"DSCR": "PASS", "IRR": "FAIL"})
        self.assertTrue(marginal.startswith("MARGINAL"))
        bad = verdict({"DSCR": "FAIL", "IRR": "FAIL", "Cap Rate": "FAIL"})
        self.assertTrue(bad.startswith("FAIL"))
        self.assertNotIn("PASS", bad)
        self.assertNotIn("PASS", marginal)

    def test_verdict_is_fail_when_every_enabled_threshold_misses(self):
        """MARGINAL is a proportion, not a count of two.

        The default config screens on two metrics, so a deal that misses
        both cleared nothing at all -- that is a FAIL, not "close".
        """
        swept = verdict({"Cash-on-Cash": "FAIL", "Monthly Cash Flow": "FAIL"})
        self.assertTrue(swept.startswith("FAIL"), swept)
        lone = verdict({"Cash-on-Cash": "FAIL"})
        self.assertTrue(lone.startswith("FAIL"), lone)

    def test_verdict_is_marginal_when_something_still_clears(self):
        """One miss beside a pass is still the close call MARGINAL is for."""
        self.assertTrue(verdict(
            {"Cash-on-Cash": "PASS", "Monthly Cash Flow": "FAIL"},
        ).startswith("MARGINAL"))
        self.assertTrue(verdict(
            {"Cap Rate": "PASS", "Cash-on-Cash": "FAIL", "IRR": "FAIL"},
        ).startswith("MARGINAL"))

    def test_default_screened_deal_that_clears_nothing_reads_fail(self):
        """End to end, on the default thresholds -- not just verdict() alone."""
        result = underwrite(PropertyInputs(
            purchase_price=400000, monthly_rent=1500,
            expenses=OperatingExpenses(property_tax_annual=8000, insurance_annual=2000),
        ))
        checks = screen(result, Thresholds())
        self.assertTrue(all(v == "FAIL" for v in checks.values()), checks)
        self.assertTrue(verdict(checks).startswith("FAIL"), verdict(checks))

    def test_breakeven_occupancy_is_not_screened_by_default(self):
        """It duplicates the cash-flow test, so it is reported, not screened."""
        result = underwrite(PropertyInputs(
            purchase_price=400000, monthly_rent=1500,
            expenses=OperatingExpenses(property_tax_annual=8000, insurance_annual=2000),
        ))
        self.assertNotIn("Breakeven Occupancy", screen(result, Thresholds()))
        self.assertIn("Breakeven Occupancy",
                      screen(result, Thresholds(max_breakeven_occupancy=0.90)))

    def test_cash_flow_and_breakeven_are_the_same_condition(self):
        """CF >= 0 iff breakeven occupancy <= assumed occupancy, at any vacancy."""
        inputs = PropertyInputs(
            purchase_price=239900, monthly_rent=2399,
            expenses=OperatingExpenses(property_tax_annual=5245, insurance_annual=1655),
        )
        for vacancy in (0.0, 0.05, 1 / 12, 0.12, 0.25):
            result = underwrite(inputs.copy_with(vacancy_rate=vacancy))
            self.assertEqual(result.year1_cash_flow >= 0,
                             result.breakeven_occupancy <= (1 - vacancy),
                             f"diverged at vacancy {vacancy}")

    def test_breakeven_still_appears_in_the_report(self):
        text = format_report(underwrite(make_inputs()), None, Thresholds())
        self.assertIn("Breakeven occupancy", text)

    def test_screen_passes_strong_deal(self):
        result = underwrite(PropertyInputs(
            purchase_price=120000, monthly_rent=1800,
            expenses=OperatingExpenses(property_tax_annual=1500, insurance_annual=900),
        ))
        checks = screen(result, Thresholds(screen_dscr=True))
        self.assertEqual(checks["DSCR"], "PASS")
        self.assertEqual(checks["Cash-on-Cash"], "PASS")

    def test_table_renders_aligned(self):
        table = render_table(["A", "B"], [["x", "1"], ["longer", "22"]])
        widths = {len(line) for line in table.splitlines()}
        self.assertEqual(len(widths), 1)

    def test_no_heuristic_language_survives_in_the_report(self):
        enriched = sample_enriched()
        text = format_report(underwrite(to_property_inputs(enriched)), enriched, Thresholds())
        for phrase in ("screening heuristic", "Rent assumed at", "1% of price"):
            self.assertNotIn(phrase, text)


class TestThresholdSwitches(unittest.TestCase):
    """A threshold is only enforced when its switch is on."""

    def setUp(self):
        # A deal weak enough to miss every bar, so an absent check can only
        # mean "switched off", never "passed".
        self.result = underwrite(PropertyInputs(
            purchase_price=400000, monthly_rent=1500,
            expenses=OperatingExpenses(property_tax_annual=8000, insurance_annual=2000),
        ))

    def test_only_the_two_cash_tests_are_on_by_default(self):
        self.assertEqual(set(screen(self.result, Thresholds())),
                         {"Cash-on-Cash", "Monthly Cash Flow"})

    def test_switching_one_on_adds_exactly_that_check(self):
        checks = screen(self.result, Thresholds(screen_irr=True))
        self.assertIn("IRR", checks)
        self.assertNotIn("DSCR", checks)

    def test_switching_one_off_removes_it_from_the_miss_count(self):
        both = screen(self.result, Thresholds())
        self.assertEqual(sum(1 for v in both.values() if v == "FAIL"), 2)
        one = screen(self.result, Thresholds(screen_cash_on_cash=False))
        self.assertEqual(sum(1 for v in one.values() if v == "FAIL"), 1)

    def test_an_off_threshold_is_still_reported(self):
        """Switched off means not judged -- it does not mean hidden."""
        text = format_report(self.result, None, Thresholds(), projection_years=0)
        self.assertIn("DSCR", text)
        self.assertIn(OFF, text)

    def test_everything_off_is_not_a_pass(self):
        t = Thresholds(screen_cash_on_cash=False, screen_monthly_cash_flow=False)
        self.assertEqual(screen(self.result, t), {})
        self.assertTrue(verdict(screen(self.result, t)).startswith("NOT SCREENED"))
        self.assertEqual(one_line_summary(self.result, t)[-1], OFF)


class TestYearOneOnlyCashFlowMiss(unittest.TestCase):
    """A miss year 1 owns alone passes with an asterisk, not a FAIL."""

    def setUp(self):
        # Lease-up drags year 1 under $0; every later year clears.
        self.result = underwrite(make_inputs(purchase_price=239900, monthly_rent=2400,
                                             expenses=OperatingExpenses(
                                                 property_tax_annual=5245,
                                                 insurance_annual=1655)))
        self.assertLess(self.result.monthly_cash_flow, 0)
        self.assertGreater(self.result.years[1].cash_flow, 0)

    def test_screen_marks_it_rather_than_failing_it(self):
        self.assertEqual(screen(self.result, Thresholds())["Monthly Cash Flow"],
                         PASS_LATER)

    def test_it_does_not_count_as_a_missed_threshold(self):
        t = Thresholds(screen_cash_on_cash=False)
        self.assertTrue(verdict(screen(self.result, t)).startswith("PASS"))
        self.assertIn("year 2", verdict(screen(self.result, t)))

    def test_the_note_names_the_year_it_clears_and_the_cash_to_carry_it(self):
        later = cash_flow_clears_later(self.result, Thresholds())
        self.assertEqual(later.first_clear_year, 2)
        self.assertGreater(later.first_clear_monthly, 0)
        self.assertAlmostEqual(later.year1_out_of_pocket,
                               -self.result.year1_cash_flow, places=6)
        text = "\n".join(cash_flow_asterisk_note(self.result, Thresholds()))
        self.assertIn("YEAR 1 ONLY", text)
        self.assertIn("out of pocket", text)

    def test_the_note_reaches_the_report(self):
        text = format_report(self.result, None, Thresholds(), projection_years=0)
        self.assertIn(PASS_LATER, text)
        self.assertIn("YEAR 1 ONLY", text)

    def test_the_batch_row_carries_the_asterisk(self):
        row = one_line_summary(self.result, Thresholds(screen_cash_on_cash=False))
        self.assertEqual(row[-1], PASS_LATER)

    def test_a_deal_that_never_clears_still_fails(self):
        """The asterisk is for timing. A deal priced wrong does not get one."""
        result = underwrite(PropertyInputs(
            purchase_price=400000, monthly_rent=1500,
            expenses=OperatingExpenses(property_tax_annual=8000, insurance_annual=2000),
        ))
        self.assertIsNone(cash_flow_clears_later(result, Thresholds()))
        self.assertEqual(screen(result, Thresholds())["Monthly Cash Flow"], "FAIL")
        self.assertEqual(cash_flow_asterisk_note(result, Thresholds()), [])

    def test_a_later_dip_back_under_the_bar_forfeits_the_asterisk(self):
        """Every year after the first must clear, not just the next one."""
        # Expenses outrun rent, so the deal clears year 2 and then crosses
        # back under mid-hold.
        result = underwrite(make_inputs(purchase_price=239900, monthly_rent=2500,
                                        rent_growth=0.01, expense_growth=0.05,
                                        expenses=OperatingExpenses(
                                            property_tax_annual=5245,
                                            insurance_annual=1655)))
        self.assertLess(result.monthly_cash_flow, 0)
        self.assertGreater(result.years[1].cash_flow, 0)
        self.assertLess(result.years[-1].cash_flow, 0)
        self.assertIsNone(cash_flow_clears_later(result, Thresholds()))

    def test_it_is_measured_against_your_bar_not_against_zero(self):
        """A positive bar year 2 cannot clear is a plain miss, not an asterisk."""
        t = Thresholds(min_monthly_cash_flow=500)
        self.assertIsNone(cash_flow_clears_later(self.result, t))
        self.assertEqual(screen(self.result, t)["Monthly Cash Flow"], "FAIL")

    def test_no_note_when_the_cash_flow_test_is_switched_off(self):
        t = Thresholds(screen_monthly_cash_flow=False)
        self.assertEqual(cash_flow_asterisk_note(self.result, t), [])


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


class TestCLIStillImports(unittest.TestCase):
    def test_main_imports_and_has_no_rent_pct_flag(self):
        import main
        flags = {action.dest for action in main.build_parser()._actions}
        self.assertNotIn("rent_pct", flags)
        self.assertIn("rent", flags)
        self.assertIn("tax", flags)
        self.assertIn("lease_up", flags)
        self.assertIn("exit_cap", flags)

    def test_analyze_refuses_without_rent_or_tax(self):
        import main
        args = main.build_parser().parse_args(["--tax", "5245"])
        with self.assertRaises(SystemExit):
            main.analyze(SAMPLE_LISTING_HTML, args)
        args = main.build_parser().parse_args(["--rent", "2400"])
        with self.assertRaises(SystemExit):
            main.analyze(SAMPLE_LISTING_HTML, args)

    def test_analyze_runs_with_both(self):
        import main
        args = main.build_parser().parse_args(["--rent", "2400", "--tax", "5245"])
        _listing, enriched, inputs, result = main.analyze(SAMPLE_LISTING_HTML, args)
        self.assertEqual(inputs.monthly_rent, 2400.0)
        self.assertEqual(enriched.property_tax_annual, 5245.0)
        self.assertIsNotNone(result.irr_screened)
        # Make-ready defaults in rather than staying at zero.
        self.assertEqual(inputs.initial_capex, default_make_ready(inputs.purchase_price))


if __name__ == "__main__":
    unittest.main()
