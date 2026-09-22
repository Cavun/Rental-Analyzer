"""
report.py

Turns an UnderwritingResult into something you can read in a terminal in ten
seconds and decide: look closer, or pass.

Nothing here computes a deal metric -- it only formats what
financial_engine.py already produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from enrichment import EnrichedListing
from financial_engine import UnderwritingResult

PASS = "PASS"
FLAG = "FLAG"
WIDTH = 78


@dataclass
class Thresholds:
    """Screening bar. Tighten or loosen per market."""

    min_dscr: float = 1.25
    min_cap_rate: float = 0.05
    min_cash_on_cash: float = 0.08
    min_irr: float = 0.10
    min_monthly_cash_flow: float = 0.0
    max_breakeven_occupancy: float = 0.90


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
        return FLAG
    return PASS if actual >= minimum else FLAG


def _check_max(actual: Optional[float], maximum: float) -> str:
    if actual is None:
        return FLAG
    return PASS if actual <= maximum else FLAG


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

    if col_headers:  # rows = an input value, columns = different metrics
        headers = [grid.row_label] + list(col_headers)
        rows = []
        for row_value, cells in zip(grid.row_values, grid.cells):
            label = pct(row_value, 2) if row_value < 1 else f"{row_value:,.0f}"
            rows.append([label] + [_format_metric(m, v) for m, v in zip(col_headers, cells)])
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
           thresholds: Optional[Thresholds] = None) -> Dict[str, str]:
    """Return {metric: PASS|FLAG} for each screening threshold."""
    t = thresholds or Thresholds()
    return {
        "DSCR": _check(result.dscr, t.min_dscr),
        "Cap Rate": _check(result.cap_rate, t.min_cap_rate),
        "Cash-on-Cash": _check(result.cash_on_cash, t.min_cash_on_cash),
        "IRR": _check(result.irr_with_equity, t.min_irr),
        "Monthly Cash Flow": _check(result.monthly_cash_flow, t.min_monthly_cash_flow),
        "Breakeven Occupancy": _check_max(result.breakeven_occupancy, t.max_breakeven_occupancy),
    }


def verdict(checks: Dict[str, str]) -> str:
    flags = [k for k, v in checks.items() if v == FLAG]
    if not flags:
        return "INVESTIGATE FURTHER -- clears every screening threshold."
    if len(flags) <= 2 and "DSCR" not in flags:
        return f"MARGINAL -- {len(flags)} threshold(s) missed: {', '.join(flags)}."
    return f"PASS ON IT -- {len(flags)} threshold(s) missed: {', '.join(flags)}."


def format_report(result: UnderwritingResult,
                  enriched: Optional[EnrichedListing] = None,
                  thresholds: Optional[Thresholds] = None,
                  projection_years: int = 10) -> str:
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
        ["Initial capex (turn-key)", money(inputs.initial_capex)],
        ["TOTAL CASH IN", money(result.total_cash_invested)],
        [f"Financing ({pct(inputs.interest_rate, 2)}, {inputs.loan_term_years}yr)",
         money(result.monthly_payment, 2) + "/mo"],
        ["Annual debt service", money(result.annual_debt_service)],
    ]
    out.append(render_table(["Item", "Amount"], rows, align_right=True))

    # --- Year 1 operations ----------------------------------------------
    y1 = result.years[0]
    exp = inputs.expenses
    out.append(_header("YEAR 1 OPERATIONS"))
    rows = [
        ["Gross scheduled rent", money(y1.gross_rent), money(inputs.monthly_rent) + "/mo"],
        [f"Vacancy ({pct(inputs.vacancy_rate, 1)})", "-" + money(y1.vacancy_loss), ""],
        ["Effective gross income", money(y1.effective_gross_income), ""],
        ["Property tax", "-" + money(exp.property_tax_annual), "post-transfer estimate"],
        ["Insurance", "-" + money(exp.insurance_annual), ""],
        ["HOA", "-" + money(exp.hoa_annual), ""],
        [f"Management ({pct(exp.management_pct, 0)})", "-" + money(y1.effective_gross_income * exp.management_pct), ""],
        [f"Maintenance ({pct(exp.maintenance_pct, 0)})", "-" + money(y1.effective_gross_income * exp.maintenance_pct), ""],
        [f"Capex reserve ({pct(exp.capex_reserve_pct, 0)})", "-" + money(y1.effective_gross_income * exp.capex_reserve_pct), ""],
        ["NET OPERATING INCOME", money(y1.noi), ""],
        ["Debt service", "-" + money(y1.debt_service), ""],
        ["CASH FLOW", money(y1.cash_flow), money(result.monthly_cash_flow) + "/mo"],
    ]
    out.append(render_table(["Line item", "Year 1", "Note"], rows))

    # --- Screening ------------------------------------------------------
    checks = screen(result, t)
    out.append(_header("SCREENING"))
    rows = [
        ["DSCR", ratio(result.dscr), f">= {t.min_dscr:.2f}", checks["DSCR"]],
        ["Cap rate", pct(result.cap_rate), f">= {pct(t.min_cap_rate, 0)}", checks["Cap Rate"]],
        ["Cash-on-cash", pct(result.cash_on_cash), f">= {pct(t.min_cash_on_cash, 0)}", checks["Cash-on-Cash"]],
        [f"IRR ({result.horizon}yr, w/ equity)", pct(result.irr_with_equity),
         f">= {pct(t.min_irr, 0)}", checks["IRR"]],
        ["Monthly cash flow", money(result.monthly_cash_flow),
         f">= {money(t.min_monthly_cash_flow)}", checks["Monthly Cash Flow"]],
        ["Breakeven occupancy", pct(result.breakeven_occupancy, 1),
         f"<= {pct(t.max_breakeven_occupancy, 0)}", checks["Breakeven Occupancy"]],
    ]
    out.append(render_table(["Metric", "Value", "Threshold", "Result"], rows))

    out.append("")
    out.append("  Reference: " + "  ".join([
        f"GRM {result.gross_rent_multiplier:.1f}",
        f"rent/price {pct(result.one_percent_rule, 2)}",
        f"IRR cash-flow-only {pct(result.irr_cash_flow_only)}",
    ]))
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
        f"At year {result.horizon}: loan balance {money(last.loan_balance)}, "
        f"property value {money(last.property_value)} at {pct(inputs.appreciation, 1)} appreciation, "
        f"equity {money(last.equity)}, cumulative cash flow {money(result.total_cash_flow)}."
    ))

    # --- Assumptions and warnings ---------------------------------------
    if enriched:
        out.append(_header("ASSUMPTIONS (not stated on the listing)"))
        for note in enriched.notes:
            out.append(_wrap("* " + note))
        if enriched.seller_property_tax_annual:
            out.append(_wrap(
                f"* Seller's stated annual tax: {money(enriched.seller_property_tax_annual)}. "
                f"Underwritten at {money(inputs.expenses.property_tax_annual)}."
            ))
        if enriched.warnings:
            out.append(_header("WARNINGS"))
            for warning in enriched.warnings:
                out.append(_wrap("! " + warning))

    out.append("")
    out.append(_wrap(
        "This is a screening model, not an appraisal. Rent, insurance and post-transfer "
        "taxes are estimates; verify rent against local comps and taxes with the assessor "
        "before making an offer."
    ))
    out.append("")
    return "\n".join(out)


def one_line_summary(result: UnderwritingResult,
                     thresholds: Optional[Thresholds] = None,
                     label: str = "") -> List[str]:
    """Row for the batch-mode comparison table."""
    checks = screen(result, thresholds)
    flags = sum(1 for v in checks.values() if v == FLAG)
    return [
        (label or result.inputs.label or "?")[:34],
        money(result.inputs.purchase_price),
        money(result.inputs.monthly_rent),
        pct(result.cap_rate, 1),
        pct(result.cash_on_cash, 1),
        ratio(result.dscr),
        pct(result.irr_with_equity, 1),
        money(result.monthly_cash_flow),
        PASS if flags == 0 else f"{FLAG} x{flags}",
    ]


BATCH_HEADERS = ["Property", "Price", "Rent", "Cap", "CoC", "DSCR", "IRR", "Mo CF", "Screen"]
