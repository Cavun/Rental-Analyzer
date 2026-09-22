"""
report.py

Turns an UnderwritingResult into something you can read in a terminal in ten
seconds and decide: look closer, or walk away.

Nothing here computes a deal metric -- it only formats what
financial_engine.py already produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from enrichment import EnrichedListing
from financial_engine import UnderwritingResult

PASS = "PASS"
FAIL = "FAIL"
OFF = "off"        # the test is switched off: reported, not screened
WIDTH = 78


@dataclass
class Thresholds:
    """
    Screening bar. Tighten or loosen per market.

    Each test has a number AND a switch. The switch is what decides whether
    the test is enforced at all: a bar you are not actually underwriting to
    should not be inflating the missed-threshold count, and a metric you do
    not screen on is still reported, so nothing is hidden by turning one off.

    Only the two tests that describe money in your pocket are on by default --
    cash-on-cash and monthly cash flow. DSCR, cap rate and IRR are off until
    you turn them on: they are comparison numbers first, and which of them is
    a hard bar depends on how you are financing and how long you are holding.
    """

    min_dscr: float = 1.25
    min_cap_rate: float = 0.05
    min_cash_on_cash: float = 0.08
    min_irr: float = 0.10
    min_monthly_cash_flow: float = 0.0

    # The switches. Off by default except the two cash tests.
    screen_dscr: bool = False
    screen_cap_rate: bool = False
    screen_cash_on_cash: bool = True
    screen_irr: bool = False
    screen_monthly_cash_flow: bool = True

    # Wiggle room on the cash-flow test ONLY, as a share of the bar. A deal
    # that misses the cash-flow threshold by less than this still fails --
    # but it reads CLOSE, with the size of the gap and the levers that would
    # close it, because "short $40/mo" and "short $400/mo" are different
    # problems and a bare FAIL tells them apart for you.
    #
    # The percentage needs something to bite on: when the threshold is $0
    # (the common case) a percentage of it is $0, so the band falls back to
    # the same percentage of month-1 gross rent. Set 0 to turn wiggle room
    # off and get a flat FAIL back.
    cash_flow_wiggle_pct: float = 0.10

    # Off by default. The after-tax layer is reported, not screened, unless
    # you deliberately turn this on: after-tax return depends on YOUR bracket
    # and YOUR other passive income, so it is a personal number, not a
    # property number.
    min_after_tax_irr: Optional[float] = None

    # Off by default, because it is not an independent test: breakeven
    # occupancy <= assumed occupancy is algebraically the same condition as
    # cash flow >= 0, so screening on both double-counts one failure and
    # inflates the missed-threshold count. Breakeven occupancy is still
    # reported as a reference metric -- it answers "how much vacancy can this
    # absorb?", which the cash-flow number alone does not.
    #
    # Set this to screen on it anyway. The honest use is as a STRESS test,
    # i.e. a value tighter than your assumed occupancy: 0.85 with a 1-month
    # vacancy assumption means "must still cash flow if vacancy doubles."
    max_breakeven_occupancy: Optional[float] = None


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------

def money(value: Optional[float], decimals: int = 0) -> str:
    """Format dollars with the sign outside the symbol: -$1,234, not $-1,234."""
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.{decimals}f}"


def pct(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{decimals}f}%"


def ratio(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value == float("inf"):
        return "n/a (no debt)"
    return f"{value:.2f}"


def _rule(char: str = "-") -> str:
    return char * WIDTH


def _header(title: str) -> str:
    return f"\n{_rule('=')}\n{title}\n{_rule('=')}"


def _check(actual: Optional[float], minimum: float) -> str:
    if actual is None:
        return FAIL
    return PASS if actual >= minimum else FAIL


def _check_max(actual: Optional[float], maximum: float) -> str:
    if actual is None:
        return FAIL
    return PASS if actual <= maximum else FAIL


def _wrap(text: str, indent: str = "  ") -> str:
    words, lines, current = text.split(), [], indent
    for word in words:
        if len(current) + len(word) + 1 > WIDTH:
            lines.append(current.rstrip())
            current = indent
        current += word + " "
    lines.append(current.rstrip())
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Fixed-width table renderer
# --------------------------------------------------------------------------

def render_table(headers: Sequence[str],
                 rows: Sequence[Sequence[str]],
                 title: str = "",
                 align_right: bool = True) -> str:
    """Minimal fixed-width table. No dependencies, no unicode surprises."""
    cols = len(headers)
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i in range(cols):
            widths[i] = max(widths[i], len(str(row[i])))

    def fmt_row(cells: Sequence[str], right: bool) -> str:
        out = []
        for i, cell in enumerate(cells):
            text = str(cell)
            out.append(text.rjust(widths[i]) if (right and i) else text.ljust(widths[i]))
        return "  ".join(out)

    sep = "  ".join("-" * w for w in widths)
    lines = []
    if title:
        lines.append(title)
    lines.append(fmt_row(headers, align_right))
    lines.append(sep)
    for row in rows:
        lines.append(fmt_row(row, align_right))
    return "\n".join(lines)


def _format_metric(metric: str, value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if metric in ("Cap Rate", "Cash-on-Cash", "IRR"):
        return pct(value, 1)
    if metric == "DSCR":
        return f"{value:.2f}"
    if metric == "Monthly CF":
        return money(value)
    return f"{value:,.2f}"


def render_grid(grid) -> str:
    """Render a SensitivityGrid (either the 2-D metric grid or the metric-column form)."""
    col_headers = getattr(grid, "col_headers", None)
    # A grid may supply its own row labels when the raw row value does not
    # speak for itself (the rent grid labels rows in dollars AND rent/price).
    row_headers = getattr(grid, "row_headers", None)

    if col_headers:  # rows = an input value, columns = different metrics
        headers = [grid.row_label] + list(col_headers)
        rows = []
        for index, (row_value, cells) in enumerate(zip(grid.row_values, grid.cells)):
            if row_headers:
                label = row_headers[index]
            else:
                label = pct(row_value, 2) if row_value < 1 else f"{row_value:,.0f}"
            rows.append([label] + [_format_metric(m, v) for m, v in zip(col_headers, cells)])
        # Row label left, metric columns right: render_table's default.
        return render_table(headers, rows, title=grid.title)

    headers = [f"{grid.row_label} \\ {grid.col_label}"] + [pct(v, 1) for v in grid.col_values]
    rows = []
    for row_value, cells in zip(grid.row_values, grid.cells):
        rows.append([pct(row_value, 1)] + [_format_metric(grid.metric, v) for v in cells])
    return render_table(headers, rows, title=grid.title)


# --------------------------------------------------------------------------
# The underwriting report
# --------------------------------------------------------------------------

def screen(result: UnderwritingResult,
           thresholds: Optional[Thresholds] = None,
           after_tax=None) -> Dict[str, str]:
    """
    Return {metric: PASS|FAIL} for each screening threshold.

    Every metric here is YEAR 1, lease-up included. Screening on the
    stabilized year would clear deals you cannot actually fund through their
    first twelve months; the stabilized figures are reported beside these so
    a lease-up failure is distinguishable from a permanent one.

    IRR is the screened IRR: the LOWER of the appreciation-based and
    cap-rate-based exits.
    """
    t = thresholds or Thresholds()
    checks: Dict[str, str] = {}
    if t.screen_dscr:
        checks["DSCR"] = _check(result.dscr, t.min_dscr)
    if t.screen_cap_rate:
        checks["Cap Rate"] = _check(result.cap_rate, t.min_cap_rate)
    if t.screen_cash_on_cash:
        checks["Cash-on-Cash"] = _check(result.cash_on_cash, t.min_cash_on_cash)
    if t.screen_irr:
        checks["IRR"] = _check(result.irr_screened, t.min_irr)
    if t.screen_monthly_cash_flow:
        checks["Monthly Cash Flow"] = _check(result.monthly_cash_flow,
                                             t.min_monthly_cash_flow)
    if t.max_breakeven_occupancy is not None:
        checks["Breakeven Occupancy"] = _check_max(
            result.breakeven_occupancy, t.max_breakeven_occupancy)
    if t.min_after_tax_irr is not None and after_tax is not None:
        checks["After-tax IRR"] = _check(after_tax.irr_screened, t.min_after_tax_irr)
    return checks


def verdict(checks: Dict[str, str]) -> str:
    if not checks:
        return ("NOT SCREENED -- every threshold is switched off; "
                "the metrics below are reported, not judged.")
    missed = [k for k, v in checks.items() if v == FAIL]
    if not missed:
        return "PASS -- clears every screening threshold; worth a closer look."
    if len(missed) <= 2 and "DSCR" not in missed:
        return f"MARGINAL -- {len(missed)} threshold(s) missed: {', '.join(missed)}."
    return f"FAIL -- {len(missed)} threshold(s) missed: {', '.join(missed)}."


# --------------------------------------------------------------------------
# Wiggle room on the cash-flow test
# --------------------------------------------------------------------------

CLOSE = "CLOSE"


@dataclass
class CashFlowGap:
    """How far year-1 cash flow is from the bar, and whether that is close."""

    threshold: float          # dollars/month the deal had to clear
    actual: float             # dollars/month it actually makes
    short_by: float           # dollars/month missing (always positive)
    band: float               # dollars/month of wiggle room allowed
    band_basis: str           # what the wiggle percentage was applied to
    close: bool               # short_by <= band


def cash_flow_gap(result: UnderwritingResult,
                  thresholds: Optional[Thresholds] = None) -> Optional[CashFlowGap]:
    """
    The cash-flow miss, measured -- or None when there is nothing to measure
    (the test is switched off, or the deal clears it).

    The band is a share of the threshold. A $0 threshold has no size for a
    percentage to work on, so the band falls back to the same share of
    month-1 gross rent: that keeps the question answerable ("how much of a
    rent bump is this?") instead of collapsing to zero.
    """
    t = thresholds or Thresholds()
    if not t.screen_monthly_cash_flow:
        return None
    actual = result.monthly_cash_flow
    if actual >= t.min_monthly_cash_flow:
        return None

    share = max(0.0, t.cash_flow_wiggle_pct)
    if abs(t.min_monthly_cash_flow) >= 1.0:
        band = abs(t.min_monthly_cash_flow) * share
        basis = f"{pct(share, 0)} of the {money(t.min_monthly_cash_flow)}/mo threshold"
    else:
        band = result.inputs.monthly_rent * share
        basis = (f"{pct(share, 0)} of the {money(result.inputs.monthly_rent)}/mo rent, "
                 f"since the threshold itself is {money(t.min_monthly_cash_flow)}")
    short_by = t.min_monthly_cash_flow - actual
    return CashFlowGap(threshold=t.min_monthly_cash_flow, actual=actual,
                       short_by=short_by, band=band, band_basis=basis,
                       close=short_by <= band)


def cash_flow_levers(result: UnderwritingResult, gap: CashFlowGap) -> List[str]:
    """
    One line per lever that would close the gap on its own -- rent, price,
    operating expenses. Each is the FULL move, not a share of it: they are
    alternatives, not a plan to do all three.
    """
    # Imported here so the report module stays importable without re-running
    # the underwrite machinery when nothing asks for a lever.
    from sensitivity import price_for_cash_flow, rent_for_cash_flow

    inputs = result.inputs
    lines: List[str] = []

    rent_needed = rent_for_cash_flow(inputs, gap.threshold)
    bump = rent_needed - inputs.monthly_rent
    if bump > 0:
        share = bump / inputs.monthly_rent if inputs.monthly_rent else 0.0
        lines.append(f"rent {money(inputs.monthly_rent)} -> {money(rent_needed)}/mo "
                     f"(+{money(bump)}, +{share:.1%}) -- and only if a comp supports it")

    price_needed = price_for_cash_flow(inputs, gap.threshold)
    if price_needed is not None and price_needed < inputs.purchase_price:
        cut = inputs.purchase_price - price_needed
        share = cut / inputs.purchase_price if inputs.purchase_price else 0.0
        lines.append(f"price {money(inputs.purchase_price)} -> {money(price_needed)} "
                     f"(-{money(cut)}, -{share:.1%})")
    elif price_needed is None:
        lines.append("price: no offer fixes this one -- the operating numbers miss the "
                     "bar even with the purchase price at zero")

    annual = gap.short_by * 12
    lines.append(f"operating expenses -{money(annual)}/yr ({money(gap.short_by)}/mo) -- "
                 f"insurance shopped, management self-done, a tax appeal")
    return lines


def cash_flow_gap_report(result: UnderwritingResult,
                         thresholds: Optional[Thresholds] = None) -> List[str]:
    """The CLOSE / how-far paragraph, as report lines. Empty when it clears."""
    gap = cash_flow_gap(result, thresholds)
    if gap is None:
        return []

    if gap.close:
        head = (f"{CLOSE} -- but not quite. Monthly cash flow {money(gap.actual)} is "
                f"{money(gap.short_by)}/mo under your {money(gap.threshold)}/mo bar, "
                f"inside the {money(gap.band)}/mo wiggle room ({gap.band_basis}).")
    else:
        head = (f"Monthly cash flow {money(gap.actual)} is {money(gap.short_by)}/mo under "
                f"your {money(gap.threshold)}/mo bar -- past the {money(gap.band)}/mo "
                f"wiggle room ({gap.band_basis}), so this is a miss, not a near miss.")

    lines = ["", _wrap(head)]
    lines.append(_wrap(f"Any ONE of these closes the {money(gap.short_by)}/mo gap:"))
    for lever in cash_flow_levers(result, gap):
        # Hanging indent: the bullet sits at 4, its continuation lines at 6.
        lines.append("    - " + _wrap(lever, indent="      ")[6:])
    return lines


def format_report(result: UnderwritingResult,
                  enriched: Optional[EnrichedListing] = None,
                  thresholds: Optional[Thresholds] = None,
                  projection_years: int = 10,
                  after_tax=None) -> str:
    t = thresholds or Thresholds()
    inputs = result.inputs
    out: List[str] = []

    # --- Property -------------------------------------------------------
    listing = enriched.listing if enriched else None
    title = listing.full_address if listing else (inputs.label or "Subject Property")
    out.append(_header(f"UNDERWRITING REPORT -- {title}"))

    if listing:
        facts = [
            ("MLS #", listing.mls_id or "n/a"),
            ("Status", listing.mls_status or "n/a"),
            ("Days on market", str(listing.days_on_market) if listing.days_on_market is not None else "n/a"),
            ("Type", listing.property_sub_type or "n/a"),
            ("Beds / Baths", f"{listing.beds or '?'} / {listing.baths or '?'}"),
            ("Sq ft", f"{listing.sqft:,.0f}" if listing.sqft else "n/a"),
            ("Year built", str(listing.year_built) if listing.year_built else "n/a"),
            ("Lot", f"{listing.lot_acres} ac" if listing.lot_acres else "n/a"),
            ("County / Muni", " / ".join(x for x in [listing.county, listing.municipality] if x) or "n/a"),
            ("Schools", listing.school_district or "n/a"),
            ("Zoning", listing.zoning or "n/a"),
        ]
        out.append("")
        # Two fact pairs per line; a value too wide for a column gets its own line.
        buffer: List[tuple] = []

        def flush() -> None:
            if buffer:
                out.append(("  " + "".join(f"{k + ':':<17}{v:<20}" for k, v in buffer)).rstrip())
                buffer.clear()

        for key, value in facts:
            if len(value) > 20:
                flush()
                out.append(f"  {key + ':':<17}{value}")
                continue
            buffer.append((key, value))
            if len(buffer) == 2:
                flush()
        flush()

    # --- Capital stack --------------------------------------------------
    out.append(_header("DEAL STRUCTURE"))
    rows = [
        ["Purchase price", money(inputs.purchase_price)],
        [f"Down payment ({pct(inputs.down_payment_pct, 0)})", money(inputs.down_payment)],
        ["Loan amount", money(inputs.loan_amount)],
        [f"Closing costs ({pct(inputs.closing_cost_pct, 0)})", money(inputs.closing_costs)],
        ["Initial make-ready / rehab (capital, pre-tenant)", money(inputs.initial_capex)],
        ["TOTAL CASH IN", money(result.total_cash_invested)],
        [f"Financing ({pct(inputs.interest_rate, 2)}, {inputs.loan_term_years}yr)",
         money(result.monthly_payment, 2) + "/mo"],
        ["Annual debt service", money(result.annual_debt_service)],
    ]
    out.append(render_table(["Item", "Amount"], rows, align_right=True))

    # --- Year 1 operations ----------------------------------------------
    y1 = result.years[0]
    exp = inputs.expenses
    out.append(_header("YEAR 1 OPERATIONS (includes lease-up)"))
    vacancy_note = f"{inputs.vacancy_rate * 12:.1f} mo steady-state"
    if inputs.lease_up_months:
        vacancy_note += f" + {inputs.lease_up_months:.1f} mo lease-up"
    rows = [
        ["Gross scheduled rent", money(y1.gross_rent), money(inputs.monthly_rent) + "/mo"],
        [f"Vacancy ({pct(inputs.vacancy_rate, 1)} + lease-up)",
         "-" + money(y1.vacancy_loss), vacancy_note],
        ["Effective gross income", money(y1.effective_gross_income), ""],
        ["Property tax", "-" + money(exp.property_tax_annual), "your figure"],
        ["Insurance", "-" + money(exp.insurance_annual), ""],
        ["HOA", "-" + money(exp.hoa_annual), ""],
        [f"Management ({pct(exp.management_pct, 0)})", "-" + money(y1.effective_gross_income * exp.management_pct), ""],
        [f"Maintenance ({pct(exp.maintenance_pct, 0)})", "-" + money(y1.effective_gross_income * exp.maintenance_pct), ""],
        [f"Capex reserve ({pct(exp.capex_reserve_pct, 0)})", "-" + money(y1.effective_gross_income * exp.capex_reserve_pct), ""],
    ]
    if inputs.year1_repair_bump_pct:
        rows.append([f"Year-1 extra repairs ({pct(inputs.year1_repair_bump_pct, 0)})",
                     "-" + money(y1.effective_gross_income * inputs.year1_repair_bump_pct),
                     "year 1 only, after move-in"])
    rows += [
        ["NET OPERATING INCOME", money(y1.noi), ""],
        ["Debt service", "-" + money(y1.debt_service), ""],
        ["CASH FLOW", money(y1.cash_flow), money(result.monthly_cash_flow) + "/mo"],
    ]
    out.append(render_table(["Line item", "Year 1", "Note"], rows))

    # --- Screening ------------------------------------------------------
    checks = screen(result, t, after_tax)
    out.append(_header("SCREENING -- YEAR 1 (incl. lease-up)"))
    # Every metric is listed whether or not it is screened: a switched-off
    # test still tells you something, it just does not fail the deal. "off"
    # in the Result column is the honest way to say so.
    rows = [
        ["DSCR", ratio(result.dscr), f">= {t.min_dscr:.2f}", checks.get("DSCR", OFF)],
        ["Cap rate", pct(result.cap_rate), f">= {pct(t.min_cap_rate, 0)}",
         checks.get("Cap Rate", OFF)],
        ["Cash-on-cash", pct(result.cash_on_cash), f">= {pct(t.min_cash_on_cash, 0)}",
         checks.get("Cash-on-Cash", OFF)],
        [f"IRR ({result.horizon}yr, lower exit)", pct(result.irr_screened),
         f">= {pct(t.min_irr, 0)}", checks.get("IRR", OFF)],
        ["Monthly cash flow", money(result.monthly_cash_flow),
         f">= {money(t.min_monthly_cash_flow)}", checks.get("Monthly Cash Flow", OFF)],
    ]
    if "Breakeven Occupancy" in checks:
        rows.append(["Breakeven occupancy", pct(result.breakeven_occupancy, 1),
                     f"<= {pct(t.max_breakeven_occupancy, 0)}", checks["Breakeven Occupancy"]])
    if "After-tax IRR" in checks:
        rows.append([f"After-tax IRR ({result.horizon}yr)", pct(after_tax.irr_screened),
                     f">= {pct(t.min_after_tax_irr, 0)}", checks["After-tax IRR"]])
    out.append(render_table(["Metric", "Value", "Threshold", "Result"], rows))
    out.extend(cash_flow_gap_report(result, t))

    # --- Stabilized reference -------------------------------------------
    # A deal that misses only because of lease-up is a timing problem you can
    # solve with cash in the bank; one that misses here too is priced wrong.
    if result.stabilized_dscr is not None:
        out.append("")
        out.append("  Stabilized (year 2, no lease-up): " + "  ".join([
            f"DSCR {ratio(result.stabilized_dscr)}",
            f"CoC {pct(result.stabilized_cash_on_cash, 1)}",
            f"cash flow {money(result.stabilized_monthly_cash_flow)}/mo",
        ]))

    out.append("")
    out.append("  Reference: " + "  ".join([
        f"GRM {result.gross_rent_multiplier:.1f}",
        f"rent/price {pct(result.one_percent_rule, 2)}",
    ]))
    out.append("  " + "  ".join([
        f"IRR appreciation exit {pct(result.irr_appreciation_exit)}",
        f"IRR cap-rate exit {pct(result.irr_cap_rate_exit)}",
        f"(screened on the lower: {pct(result.irr_screened)})",
    ]))
    if result.multiple_irr_possible:
        out.append(_wrap(
            "! The cash-flow series changes sign more than once, so more than one "
            "rate can solve NPV = 0. The IRR shown is the root nearest 10%; treat "
            "it as one answer, not the answer, and lean on cash flow and DSCR."
        ))
    # Not a screened metric by default (see Thresholds) but worth stating:
    # it converts the cash-flow number into months of vacancy tolerance.
    # Breakeven is computed on year 1, so the lease-up months are already
    # charged against it: the room below is what is left ON TOP of lease-up.
    months = max(0.0, (1 - result.breakeven_occupancy)) * 12
    underwritten = inputs.vacancy_rate * 12 + inputs.lease_up_months
    out.append(_wrap(
        f"Breakeven occupancy {pct(result.breakeven_occupancy, 1)}: rent must be collected "
        f"that share of year 1 to cover costs, leaving room for about {months:.1f} "
        f"vacant month(s) beyond the lease-up already assumed, before cash flow "
        f"turns negative (year 1 is underwritten at {inputs.vacancy_rate * 12:.1f} "
        f"month(s) of steady-state vacancy + {inputs.lease_up_months:.1f} month(s) of "
        f"lease-up = {underwritten:.1f} month(s) of the 12)."
    ))
    out.append("")
    out.append(_wrap("VERDICT: " + verdict(checks)))

    # --- Projection -----------------------------------------------------
    horizon = min(projection_years, result.horizon)
    out.append(_header(f"PROJECTION -- FIRST {horizon} YEARS (of a {result.horizon}-year hold)"))
    rows = []
    for y in result.years[:horizon]:
        rows.append([
            str(y.year), money(y.gross_rent), money(y.operating_expenses), money(y.noi),
            money(y.debt_service), money(y.cash_flow), money(y.cumulative_cash_flow),
            money(y.loan_balance), money(y.equity),
        ])
    out.append(render_table(
        ["Yr", "Gross rent", "Opex", "NOI", "Debt svc", "Cash flow", "Cum CF", "Loan bal", "Equity"],
        rows,
    ))
    last = result.years[-1]
    out.append("")
    out.append(_wrap(
        f"At end of year {result.horizon}: loan balance {money(last.loan_balance)}, "
        f"property value {money(last.property_value)} at {pct(inputs.appreciation, 1)} appreciation, "
        f"equity {money(last.equity)}, cumulative cash flow {money(result.total_cash_flow)}. "
        "Projection values are END OF YEAR, so year 1 already carries a year of "
        "appreciation."
    ))

    # --- Exit cross-check ------------------------------------------------
    out.append(_header("EXIT -- TWO WAYS OF VALUING THE SAME ASSET"))
    exit_rows = [
        ["Appreciation-based", money(last.property_value),
         f"{pct(inputs.appreciation, 1)}/yr compounded",
         money(result.net_sale_appreciation)],
        ["Cap-rate-based", money(result.terminal_value_cap_rate),
         f"yr-{result.horizon + 1} NOI {money(result.terminal_noi)} / "
         f"{pct(result.exit_cap_rate_used, 2)} exit cap",
         money(result.net_sale_cap_rate)],
    ]
    out.append(render_table(
        ["Method", "Gross value", "Basis", f"Net of {pct(inputs.selling_cost_pct, 0)} costs + payoff"],
        exit_rows))
    out.append("")
    out.append(_wrap(
        f"IRR on the appreciation exit {pct(result.irr_appreciation_exit)}; on the "
        f"cap-rate exit {pct(result.irr_cap_rate_exit)}. Screened on the lower, "
        f"{pct(result.irr_screened)}."
    ))
    if result.exit_values_disagree:
        out.append(_wrap(
            "! The two exit values differ by more than 25%. Your appreciation and "
            "rent-growth assumptions disagree about what this building is worth in "
            f"{result.horizon} years: appreciation says the price compounds at "
            f"{pct(inputs.appreciation, 1)}, while rent growing at "
            f"{pct(inputs.rent_growth, 1)} against a {pct(result.exit_cap_rate_used, 2)} "
            "exit cap says otherwise. Reconcile them before believing either IRR."
        ))

    # --- Assumptions and warnings ---------------------------------------
    if enriched:
        out.append(_header("ASSUMPTIONS (not stated on the listing)"))
        for note in enriched.notes:
            out.append(_wrap("* " + note))
        if enriched.seller_property_tax_annual:
            out.append(_wrap(
                f"* Seller's stated annual tax: {money(enriched.seller_property_tax_annual)} "
                "(reference only -- a capped, usually homestead-exempt bill that a rental "
                f"buyer will not pay). Underwritten at "
                f"{money(inputs.expenses.property_tax_annual)}, your figure."
            ))
        if enriched.warnings:
            out.append(_header("WARNINGS"))
            for warning in enriched.warnings:
                out.append(_wrap("! " + warning))

    if after_tax is not None:
        out.append(format_after_tax(after_tax, result))

    out.append("")
    out.append(_wrap(
        "This is a screening model, not an appraisal. Rent and property tax are YOUR "
        "figures and nothing here second-guesses them; insurance, growth rates and the "
        "exit cap are assumptions. Confirm rent against current comps and tax with the "
        "assessor or the state estimator before making an offer."
    ))
    out.append("")
    return "\n".join(out)


def format_after_tax(after_tax, result: UnderwritingResult) -> str:
    """
    Render the after-tax layer. Reported, never mixed into the pre-tax
    numbers above -- after-tax return is a fact about the owner, not the
    building.
    """
    out: List[str] = [_header("AFTER TAX (reported, not screened by default)")]

    a = after_tax.assumptions
    rows = [
        ["Depreciable basis", money(after_tax.depreciable_basis),
         f"{a.building_share:.0%} building share of {money(after_tax.original_basis)}"],
        ["Annual depreciation", money(after_tax.depreciable_basis / a.recovery_period_years),
         f"straight line, {a.recovery_period_years:g} yr"],
        ["Ordinary rate", pct(a.ordinary_rate),
         "federal + state" + (" + city" if a.city_ordinary_rate else "")],
        ["Capital gains rate", pct(a.capital_gains_rate), ""],
        ["Depreciation recapture", pct(a.depreciation_recapture_rate), "unrecaptured section 1250"],
        ["NIIT", pct(a.niit_rate) if a.niit else "off", ""],
        ["Passive losses", "usable now" if a.passive_losses_usable else "suspended",
         (f"allowance {money(a.usable_allowance())}/yr" if a.passive_losses_usable else
          "carried forward to sale")],
    ]
    out.append(render_table(["Input", "Value", "Note"], rows))

    first = after_tax.years[0]
    out.append("")
    out.append(render_table(
        ["Year 1", "Amount"],
        [
            ["NOI", money(first.noi)],
            ["Mortgage interest", "-" + money(first.interest)],
            ["Depreciation", "-" + money(first.depreciation)],
            ["TAXABLE INCOME", money(first.taxable_income)],
            ["Tax (benefit)", money(first.tax)],
            ["Pre-tax cash flow", money(first.pre_tax_cash_flow)],
            ["AFTER-TAX CASH FLOW", money(first.after_tax_cash_flow)],
        ],
    ))
    if first.suspended_loss_added:
        out.append(_wrap(
            f"* {money(first.suspended_loss_added)} of year-1 loss is not usable now "
            "and carries forward."
        ))

    out.append("")
    sale_rows = []
    for sale in (after_tax.sale_appreciation, after_tax.sale_cap_rate):
        sale_rows.append([
            sale.method,
            money(sale.net_sale_price),
            money(sale.adjusted_basis),
            money(sale.total_gain),
            money(sale.recapture_tax),
            money(sale.capital_gains_tax + sale.state_tax + sale.niit_tax),
            money(sale.total_tax),
            money(sale.after_tax_proceeds),
        ])
    out.append(render_table(
        ["Exit", "Net sale", "Adj basis", "Gain", "Recapture tax", "Gains tax",
         "Total tax", "After-tax cash"],
        sale_rows,
        title=f"AT SALE (end of year {result.horizon})",
    ))
    out.append("")
    out.append(_wrap(
        f"Accumulated depreciation {money(after_tax.accumulated_depreciation)}; suspended "
        f"losses released at sale {money(after_tax.suspended_balance_at_sale)} "
        f"(worth {money(after_tax.sale_appreciation.suspended_loss_benefit)} in tax)."
    ))
    out.append("")
    out.append("  After-tax IRR: " + "  ".join([
        f"appreciation exit {pct(after_tax.irr_appreciation_exit)}",
        f"cap-rate exit {pct(after_tax.irr_cap_rate_exit)}",
        f"(lower: {pct(after_tax.irr_screened)})",
    ]))
    out.append(f"  Year-1 after-tax cash flow: {money(after_tax.year1_after_tax_cash_flow)} "
               f"({money(after_tax.year1_after_tax_cash_flow / 12)}/mo)")
    out.append(f"  Total tax on operations over {result.horizon} years: "
               f"{money(after_tax.total_tax_on_operations)}")
    if after_tax.multiple_irr_possible:
        out.append(_wrap("! The after-tax cash-flow series changes sign more than once; "
                         "more than one rate can solve NPV = 0."))

    out.append("")
    for note in after_tax.notes:
        out.append(_wrap("* " + note))
    return "\n".join(out)


def one_line_summary(result: UnderwritingResult,
                     thresholds: Optional[Thresholds] = None,
                     label: str = "") -> List[str]:
    """Row for the batch-mode comparison table."""
    checks = screen(result, thresholds)
    missed = sum(1 for v in checks.values() if v == FAIL)
    if not checks:
        screened = OFF
    else:
        screened = PASS if missed == 0 else f"{FAIL} x{missed}"
    return [
        (label or result.inputs.label or "?")[:34],
        money(result.inputs.purchase_price),
        money(result.inputs.monthly_rent),
        pct(result.cap_rate, 1),
        pct(result.cash_on_cash, 1),
        ratio(result.dscr),
        pct(result.irr_screened, 1),
        money(result.monthly_cash_flow),
        screened,
    ]


BATCH_HEADERS = ["Property", "Price", "Rent", "Cap", "CoC", "DSCR", "IRR", "Mo CF", "Screen"]
