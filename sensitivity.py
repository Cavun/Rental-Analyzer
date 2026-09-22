"""
sensitivity.py

Grid sensitivity analysis. Every run deep-copies the base PropertyInputs --
the base case is never mutated, so a grid can never contaminate the headline
underwrite or a later grid.

Three grids ship by default:
  1. rent growth x vacancy rate   -- how fragile is the operating assumption?
  2. interest rate                -- what does the rate you actually lock cost?
  3. rent level                   -- your entered rent, stepped DOWN, because
                                     rent is the single most load-bearing
                                     assumption in the model and the risk is
                                     that your comp was optimistic. Centred on
                                     what you actually entered, not on a
                                     percentage of the asking price.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence

from financial_engine import PropertyInputs, UnderwritingResult, underwrite

# Default axes.
RENT_GROWTH_VALUES = (0.00, 0.01, 0.02, 0.03, 0.04)
VACANCY_VALUES = (1 / 12, 2 / 12, 3 / 12)          # 1, 2, 3 vacant months a year
INTEREST_RATE_VALUES = (0.055, 0.0625, 0.07, 0.075, 0.08, 0.085)
# Multipliers applied to the ENTERED rent. The 0% row is the base case, so it
# must reproduce the headline result exactly.
RENT_LEVEL_DELTAS = (-0.20, -0.15, -0.10, -0.05, 0.00, 0.05)

# Metric name -> extractor. Used to build one grid per metric.
METRICS = {
    "Cap Rate": lambda r: r.cap_rate,
    "Cash-on-Cash": lambda r: r.cash_on_cash,
    "DSCR": lambda r: r.dscr,
    # The screened IRR is the LOWER of the two exit methods (see
    # financial_engine.underwrite) -- sensitivity must move with the number
    # the deal is actually judged on.
    "IRR": lambda r: r.irr_screened,
    "Monthly CF": lambda r: r.monthly_cash_flow,
}


@dataclass
class SensitivityGrid:
    """A 2-D grid of one metric over two varying inputs."""

    title: str
    row_label: str
    col_label: str
    row_values: List[float]
    col_values: List[float]
    metric: str
    cells: List[List[Optional[float]]] = field(default_factory=list)


def _run(base: PropertyInputs, **changes) -> UnderwritingResult:
    """Underwrite an independent deep copy of the base inputs."""
    candidate = copy.deepcopy(base)
    for key, value in changes.items():
        setattr(candidate, key, value)
    return underwrite(candidate)


def rent_growth_vs_vacancy(base: PropertyInputs,
                           metric: str = "IRR",
                           rent_growth_values: Sequence[float] = RENT_GROWTH_VALUES,
                           vacancy_values: Sequence[float] = VACANCY_VALUES) -> SensitivityGrid:
    extract: Callable = METRICS[metric]
    grid = SensitivityGrid(
        title=f"{metric}: rent growth x vacancy",
        row_label="Rent growth",
        col_label="Vacancy",
        row_values=list(rent_growth_values),
        col_values=list(vacancy_values),
        metric=metric,
    )
    for growth in grid.row_values:
        row = []
        for vacancy in grid.col_values:
            row.append(extract(_run(base, rent_growth=growth, vacancy_rate=vacancy)))
        grid.cells.append(row)
    return grid


def interest_rate_grid(base: PropertyInputs,
                       metrics: Sequence[str] = ("Cap Rate", "Cash-on-Cash", "DSCR", "IRR", "Monthly CF"),
                       rate_values: Sequence[float] = INTEREST_RATE_VALUES) -> SensitivityGrid:
    """One row per interest rate, one column per metric."""
    grid = SensitivityGrid(
        title="Interest rate sensitivity",
        row_label="Rate",
        col_label="Metric",
        row_values=list(rate_values),
        col_values=list(range(len(metrics))),
        metric="mixed",
    )
    grid.col_headers = list(metrics)  # type: ignore[attr-defined]
    for rate in grid.row_values:
        result = _run(base, interest_rate=rate)
        grid.cells.append([METRICS[m](result) for m in metrics])
    return grid


def rent_level_grid(base: PropertyInputs,
                    metrics: Sequence[str] = ("Cap Rate", "Cash-on-Cash", "DSCR", "IRR", "Monthly CF"),
                    deltas: Sequence[float] = RENT_LEVEL_DELTAS) -> SensitivityGrid:
    """
    One row per rent level, centred on the rent you entered.

    Rows are percentage moves off YOUR rent, labelled in dollars with the
    resulting rent/price ratio beside each. Anchoring the grid to a share of
    the purchase price (as this used to) puts every row below a real comp
    whenever the comp beats 1% of price, which makes the whole grid useless
    exactly when the deal is good.
    """
    grid = SensitivityGrid(
        title="Rent level sensitivity (moves off your entered rent)",
        row_label="Rent",
        col_label="Metric",
        row_values=[base.monthly_rent * (1 + d) for d in deltas],
        col_values=list(range(len(metrics))),
        metric="mixed",
    )
    grid.row_headers = [  # type: ignore[attr-defined]
        f"{f'{d:+.0%}':>4}  ${rent:,.0f}/mo  ({rent / base.purchase_price:.2%})"
        for d, rent in zip(deltas, grid.row_values)
    ]
    grid.col_headers = list(metrics)  # type: ignore[attr-defined]
    for rent in grid.row_values:
        result = _run(base, monthly_rent=rent)
        grid.cells.append([METRICS[m](result) for m in metrics])
    return grid


def breakeven_rent(base: PropertyInputs, tolerance: float = 1.0) -> float:
    """
    Lowest monthly rent at which YEAR-1 cash flow is still >= $0 -- year 1
    includes the lease-up months, so this breakeven inherits them
    automatically and is stricter than a stabilized breakeven would be.

    Bisection on rent; independent of the base inputs (deep-copied per run).
    The 5%-of-price upper bracket stays safe: no residential rent comes in
    at 60% of purchase price a year.
    """
    low, high = 0.0, base.purchase_price * 0.05
    if underwrite(copy.deepcopy(base)).year1_cash_flow >= 0 and base.monthly_rent:
        high = base.monthly_rent
    for _ in range(80):
        mid = (low + high) / 2
        if _run(base, monthly_rent=mid).year1_cash_flow >= 0:
            high = mid
        else:
            low = mid
        if high - low < tolerance:
            break
    return high
