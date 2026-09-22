"""
sensitivity.py

Grid sensitivity analysis. Every run deep-copies the base PropertyInputs --
the base case is never mutated, so a grid can never contaminate the headline
underwrite or a later grid.

Three grids ship by default:
  1. rent growth x vacancy rate   -- how fragile is the operating assumption?
  2. interest rate                -- what does the rate you actually lock cost?
  3. rent level (% of price)      -- the 1% rule is a screen, not a comp; this
                                     shows where the deal breaks if real rents
                                     come in below it. (Added on top of the
                                     two requested grids because rent is the
                                     single most load-bearing assumption in
                                     the whole model.)
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
RENT_PCT_VALUES = (0.006, 0.007, 0.008, 0.009, 0.010)

# Metric name -> extractor. Used to build one grid per metric.
METRICS = {
    "Cap Rate": lambda r: r.cap_rate,
    "Cash-on-Cash": lambda r: r.cash_on_cash,
    "DSCR": lambda r: r.dscr,
    "IRR": lambda r: r.irr_with_equity,
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
                    rent_pct_values: Sequence[float] = RENT_PCT_VALUES) -> SensitivityGrid:
    """One row per rent level expressed as a percentage of purchase price."""
    grid = SensitivityGrid(
        title="Rent level sensitivity (rent as % of price)",
        row_label="Rent %",
        col_label="Metric",
        row_values=list(rent_pct_values),
        col_values=list(range(len(metrics))),
        metric="mixed",
    )
    grid.col_headers = list(metrics)  # type: ignore[attr-defined]
    for pct in grid.row_values:
        result = _run(base, monthly_rent=base.purchase_price * pct)
        grid.cells.append([METRICS[m](result) for m in metrics])
    return grid


def breakeven_rent(base: PropertyInputs, tolerance: float = 1.0) -> float:
    """
    Lowest monthly rent at which year-1 cash flow is still >= $0.
    Bisection on rent; independent of the base inputs (deep-copied per run).
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
