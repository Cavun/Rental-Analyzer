#!/usr/bin/env python3
"""
main.py -- single-family rental underwriting, end to end.

    paste/point at listing HTML -> extract -> enrich -> finance -> report
                                -> sensitivity grids

Usage
-----
    python3 main.py                        # runs the built-in sample listing
    python3 main.py listing.html           # one saved listing page
    python3 main.py a.html b.html c.html   # batch screen: one row per property
    cat listing.html | python3 main.py -   # read from stdin
    python3 main.py --paste                # paste HTML/text, end with Ctrl-D

--rent and --tax are REQUIRED: this model does not invent a rent from the
asking price, and it does not estimate a post-transfer tax bill. Pull rent
from local comps and run the parcel through your state's tax estimator.

Other financing assumptions can be overridden per run (--rate, --down,
--price, ...); everything else uses the standing house assumptions in
financial_engine.py. No network calls anywhere in this pipeline.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional, Tuple

from enrichment import EnrichedListing, EnrichmentAssumptions, enrich, to_property_inputs
from extraction import ListingData, parse_listing
from financial_engine import (
    PropertyInputs,
    UnderwritingResult,
    default_make_ready,
    underwrite,
)
from report import (
    BATCH_HEADERS,
    Thresholds,
    format_report,
    one_line_summary,
    render_grid,
    render_table,
    money,
)
from sample_listing import SAMPLE_LISTING_HTML
from sensitivity import (
    breakeven_rent,
    interest_rate_grid,
    rent_growth_vs_vacancy,
    rent_level_grid,
)


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------

def analyze(blob: str,
            args: Optional[argparse.Namespace] = None
            ) -> Tuple[ListingData, EnrichedListing, PropertyInputs, UnderwritingResult]:
    """Run one listing all the way through. Returns every intermediate stage."""
    listing = parse_listing(blob)

    if args and args.price:
        listing.price = args.price
    if not listing.price:
        raise SystemExit(
            "Could not find a price in that listing. If this is a non-flexmls page, "
            "supply --price to underwrite it anyway."
        )

    assumptions = EnrichmentAssumptions()
    rent = args.rent if args else None
    tax = args.tax if args else None
    if not rent:
        raise SystemExit(
            "--rent is required: give the monthly rent from local comps. "
            "This model will not derive one from the asking price."
        )
    if not tax:
        raise SystemExit(
            "--tax is required: give the annual property tax a RENTAL buyer will "
            "pay. Run the parcel through "
            f"{assumptions.tax_estimator_url} -- the seller's bill on the listing "
            "is capped and usually homestead-exempt, so it is not your bill."
        )

    enriched = enrich(listing, assumptions, property_tax_annual=tax, monthly_rent=rent)

    overrides = {}
    if args:
        if args.rate is not None:
            overrides["interest_rate"] = args.rate
        if args.down is not None:
            overrides["down_payment_pct"] = args.down
        if args.term is not None:
            overrides["loan_term_years"] = args.term
        if args.closing is not None:
            overrides["closing_cost_pct"] = args.closing
        if args.capex is not None:
            overrides["initial_capex"] = args.capex
        else:
            overrides["initial_capex"] = default_make_ready(listing.price)
        if args.lease_up is not None:
            overrides["lease_up_months"] = args.lease_up
        if args.exit_cap is not None:
            overrides["exit_cap_rate"] = args.exit_cap
        if args.vacancy is not None:
            overrides["vacancy_rate"] = args.vacancy
        if args.hold is not None:
            overrides["hold_years"] = args.hold

    inputs = to_property_inputs(enriched, assumptions, **overrides)
    return listing, enriched, inputs, underwrite(inputs)


# CLI name -> the pair of Thresholds fields it drives.
SCREEN_SWITCHES = {
    "dscr": "screen_dscr",
    "cap": "screen_cap_rate",
    "coc": "screen_cash_on_cash",
    "irr": "screen_irr",
    "cf": "screen_monthly_cash_flow",
}


def thresholds_from_args(args: argparse.Namespace) -> Thresholds:
    t = Thresholds()
    if args.min_dscr is not None:
        t.min_dscr = args.min_dscr
    if args.min_cap is not None:
        t.min_cap_rate = args.min_cap
    if args.min_coc is not None:
        t.min_cash_on_cash = args.min_coc
    if args.min_irr is not None:
        t.min_irr = args.min_irr
    if args.min_cf is not None:
        t.min_monthly_cash_flow = args.min_cf
    # --screen replaces the default set outright rather than adding to it:
    # "screen on exactly these" is the only reading that lets you turn the
    # two defaults OFF from the command line.
    if args.screen is not None:
        for switch in SCREEN_SWITCHES.values():
            setattr(t, switch, False)
        for name in args.screen:
            setattr(t, SCREEN_SWITCHES[name], True)
    return t


# --------------------------------------------------------------------------
# Output modes
# --------------------------------------------------------------------------

def print_single(blob: str, args: argparse.Namespace) -> None:
    listing, enriched, inputs, result = analyze(blob, args)
    thresholds = thresholds_from_args(args)

    if args.json:
        print(json.dumps({
            "listing": listing.to_dict(),
            "assumptions": {
                "monthly_rent": inputs.monthly_rent,
                "insurance_annual": enriched.insurance_annual,
                "property_tax_annual": enriched.property_tax_annual,
                "seller_property_tax_annual": enriched.seller_property_tax_annual,
                "initial_capex": inputs.initial_capex,
                "lease_up_months": inputs.lease_up_months,
                "exit_cap_rate": result.exit_cap_rate_used,
                "down_payment_pct": inputs.down_payment_pct,
                "interest_rate": inputs.interest_rate,
                "loan_term_years": inputs.loan_term_years,
                "closing_cost_pct": inputs.closing_cost_pct,
                "vacancy_rate": inputs.vacancy_rate,
                "hold_years": inputs.hold_years,
            },
            "metrics": {
                "cap_rate": result.cap_rate,
                "cash_on_cash": result.cash_on_cash,
                "dscr": result.dscr,
                "irr_appreciation_exit": result.irr_appreciation_exit,
                "irr_cap_rate_exit": result.irr_cap_rate_exit,
                "irr_screened": result.irr_screened,
                "multiple_irr_possible": result.multiple_irr_possible,
                "stabilized_dscr": result.stabilized_dscr,
                "stabilized_cash_on_cash": result.stabilized_cash_on_cash,
                "terminal_value_cap_rate": result.terminal_value_cap_rate,
                "net_sale_appreciation": result.net_sale_appreciation,
                "net_sale_cap_rate": result.net_sale_cap_rate,
                "exit_values_disagree": result.exit_values_disagree,
                "year1_noi": result.year1_noi,
                "monthly_cash_flow": result.monthly_cash_flow,
                "breakeven_occupancy": result.breakeven_occupancy,
                "breakeven_rent": breakeven_rent(inputs),
            },
            "notes": enriched.notes,
            "warnings": enriched.warnings,
        }, indent=2, default=str))
        return

    tax_layer = None
    if args.after_tax:
        from tax_engine import after_tax as compute_after_tax
        tax_layer = compute_after_tax(result)
    print(format_report(result, enriched, thresholds, projection_years=args.show_years,
                        after_tax=tax_layer))

    if args.no_sensitivity:
        return

    print("=" * 78)
    print("SENSITIVITY")
    print("=" * 78)
    print()
    print(render_grid(rent_growth_vs_vacancy(inputs, metric=args.grid_metric)))
    if args.grid_metric in ("Cap Rate", "Cash-on-Cash", "DSCR", "Monthly CF"):
        print("  (Year-1 metrics don't move with rent growth -- only the vacancy axis bites.")
        print("   Use --grid-metric IRR to see the growth axis.)")
    print()
    print(render_grid(interest_rate_grid(inputs)))
    print()
    print(render_grid(rent_level_grid(inputs)))
    print()
    be_rent = breakeven_rent(inputs)
    gap = (be_rent - inputs.monthly_rent) / inputs.monthly_rent if inputs.monthly_rent else 0.0
    print(f"  Breakeven rent (year-1 cash flow = $0, lease-up included): {money(be_rent)}/mo "
          f"vs your {money(inputs.monthly_rent)}/mo ({gap:+.1%}).")
    print()


def print_batch(blobs: List[Tuple[str, str]], args: argparse.Namespace) -> None:
    """One row per listing so you can rank a stack of them at a glance."""
    thresholds = thresholds_from_args(args)
    rows, failures = [], []
    for name, blob in blobs:
        try:
            listing, _enriched, _inputs, result = analyze(blob, args)
            label = listing.full_address or name
            rows.append(one_line_summary(result, thresholds, label))
        except Exception as exc:  # a bad paste shouldn't kill the whole batch
            failures.append((name, str(exc)))

    print()
    print(render_table(BATCH_HEADERS, rows, title=f"BATCH SCREEN -- {len(rows)} listing(s)"))
    print()
    print("  Screen column: PASS = clears every threshold it is screened on; "
          "PASS* = clears,\n  but cash flow only from year 2; FAIL xN = N thresholds "
          "missed; off = nothing screened.")
    print("  Re-run a single listing without other files for the full report.")
    if failures:
        print()
        for name, err in failures:
            print(f"  ! {name}: {err}")
    print()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def read_blob(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    with open(source, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Underwrite a single-family rental from an MLS listing page.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("sources", nargs="*",
                   help="Listing HTML file(s), or '-' for stdin. Omit to run the built-in sample.")
    p.add_argument("--paste", action="store_true",
                   help="Paste listing HTML or text interactively, then Ctrl-D.")
    p.add_argument("--gui", action="store_true", help="Launch the desktop GUI instead of the CLI.")
    p.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a report.")
    p.add_argument("--no-sensitivity", action="store_true", help="Skip the sensitivity grids.")
    p.add_argument("--after-tax", action="store_true",
                   help="Also report the after-tax layer (depreciation, passive losses, "
                        "recapture at sale). Reported, not screened.")
    p.add_argument("--show-years", type=int, default=10, help="Projection years to print (default 10).")
    p.add_argument("--grid-metric", default="IRR",
                   choices=["Cap Rate", "Cash-on-Cash", "DSCR", "IRR", "Monthly CF"],
                   help="Metric for the rent-growth x vacancy grid.")

    g = p.add_argument_group("deal overrides")
    g.add_argument("--price", type=float, help="Override the list price (e.g. your offer price).")
    g.add_argument("--rent", type=float,
                   help="REQUIRED (except with --gui). Monthly rent in dollars, from "
                        "local comps.")
    g.add_argument("--tax", type=float, metavar="ANNUAL",
                   help="REQUIRED. Annual property tax in dollars for a RENTAL buyer, "
                        "e.g. from https://treas-secure.state.mi.us/ptestimator. Used "
                        "verbatim -- the seller's bill on the listing is not your bill. "
                        "REQUIRED except with --gui.")
    g.add_argument("--rate", type=float, help="Interest rate as a decimal (default 0.07).")
    g.add_argument("--down", type=float, help="Down payment share (default 0.20).")
    g.add_argument("--term", type=int, help="Loan term in years (default 30).")
    g.add_argument("--closing", type=float, help="Closing costs as a share of price (default 0.03).")
    g.add_argument("--capex", type=float,
                   help="One-time make-ready / rehab dollars spent BEFORE the first "
                        "tenant. Capital, not an operating expense: it lands in cash "
                        "invested and in the cap-rate basis, never in NOI. Default is "
                        "$2,500 or 1% of price, whichever is higher -- set 0 for a "
                        "genuinely turn-key unit.")
    g.add_argument("--lease-up", type=float, metavar="MONTHS",
                   help="Months of vacancy in YEAR 1 only, on top of the steady-state "
                        "vacancy rate (default 1).")
    g.add_argument("--exit-cap", type=float,
                   help="Exit cap rate as a decimal for the cap-rate terminal value "
                        "(default: year-1 cap rate + 0.50%%).")
    g.add_argument("--vacancy", type=float, help="Vacancy rate (default 0.0833 = 1 month).")
    g.add_argument("--hold", type=int, help="Projection years (default 30).")

    s = p.add_argument_group("screening thresholds")
    s.add_argument("--min-dscr", type=float, help="Default 1.25.")
    s.add_argument("--min-cap", type=float, help="Default 0.05.")
    s.add_argument("--min-coc", type=float, help="Default 0.08.")
    s.add_argument("--min-irr", type=float, help="Default 0.10.")
    s.add_argument("--min-cf", type=float, metavar="DOLLARS",
                   help="Minimum year-1 monthly cash flow, lease-up included "
                        "(default 0). Negative allows a deal you are willing to feed.")
    s.add_argument("--screen", nargs="+", choices=sorted(SCREEN_SWITCHES),
                   metavar="METRIC",
                   help="Screen on exactly these metrics and no others: "
                        + ", ".join(sorted(SCREEN_SWITCHES))
                        + ". Default is coc and cf; the rest are reported, not screened.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gui:
        try:
            from gui import main as gui_main
        except ImportError as exc:  # tkinter missing from this Python build
            print(f"Could not start the GUI: {exc}\n"
                  "Tkinter ships with python.org and Windows installers; on Debian/Ubuntu "
                  "install it with: sudo apt install python3-tk", file=sys.stderr)
            return 1
        return gui_main()

    if args.paste:
        print("Paste the listing HTML (or plain text), then press Ctrl-D:", file=sys.stderr)
        print_single(sys.stdin.read(), args)
        return 0

    if not args.sources:
        print("No listing supplied -- running the built-in sample listing.\n"
              "(Point it at a saved listing page: python3 main.py my_listing.html)\n", file=sys.stderr)
        print_single(SAMPLE_LISTING_HTML, args)
        return 0

    if len(args.sources) == 1:
        print_single(read_blob(args.sources[0]), args)
        return 0

    print("Warning: --rent and --tax apply the same figures to every listing in a "
          "batch. Both are per-property; run listings one at a time to give each "
          "its own.", file=sys.stderr)
    print_batch([(src, read_blob(src)) for src in args.sources], args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
