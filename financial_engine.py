"""
financial_engine.py

ALL the arithmetic lives here. Pure Python, no third-party imports, no LLM
involvement, deterministic: the same PropertyInputs always produce the same
UnderwritingResult.

Conventions used throughout:
  * Rates are decimals (0.07 == 7%).
  * "Year 1" is the first 12 months of ownership.
  * NOI excludes debt service and excludes capex reserves is a common
    convention, but here capex reserve IS deducted before NOI (see
    OperatingExpenses.capex_reserve_pct) because a reserve you don't fund is
    a bill you take later. Cap rate is therefore slightly conservative
    versus a broker's pro forma -- that is intentional.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional

# --------------------------------------------------------------------------
# House assumptions (the ones the underwriting spec fixes for every listing)
# --------------------------------------------------------------------------

DOWN_PAYMENT_PCT = 0.20        # 20% down on every deal
INTEREST_RATE = 0.07           # 7% fixed
LOAN_TERM_YEARS = 30           # 30-year amortization
CLOSING_COST_PCT = 0.03        # 3% of purchase price, same on every listing
INITIAL_CAPEX = 0.0            # turn-key: no rehab budget
RENT_PCT_OF_PRICE = 0.01       # monthly rent == 1% of purchase price
VACANCY_RATE = 1.0 / 12.0      # one vacant month per year (8.33%)
HOLD_YEARS = 30                # "forever hold" -> project the full loan term

MONTHS = 12


@dataclass
class OperatingExpenses:
    """Annual operating costs. Percentages apply to effective gross income."""

    property_tax_annual: float = 0.0
    insurance_annual: float = 0.0
    hoa_annual: float = 0.0
    other_fixed_annual: float = 0.0        # lawn/snow, utilities landlord pays, etc.
    management_pct: float = 0.0            # off by default; set per deal
    maintenance_pct: float = 0.08          # 8% repairs/turnover
    capex_reserve_pct: float = 0.08        # 8% roof/HVAC/appliance reserve

    @property
    def variable_pct(self) -> float:
        return self.management_pct + self.maintenance_pct + self.capex_reserve_pct

    @property
    def fixed_annual(self) -> float:
        return (
            self.property_tax_annual
            + self.insurance_annual
            + self.hoa_annual
            + self.other_fixed_annual
        )


@dataclass
class PropertyInputs:
    """Everything the engine needs. Defaults encode the standing assumptions."""

    purchase_price: float
    monthly_rent: Optional[float] = None   # defaults to 1% of purchase price
    down_payment_pct: float = DOWN_PAYMENT_PCT
    interest_rate: float = INTEREST_RATE
    loan_term_years: int = LOAN_TERM_YEARS
    closing_cost_pct: float = CLOSING_COST_PCT
    initial_capex: float = INITIAL_CAPEX
    vacancy_rate: float = VACANCY_RATE
    hold_years: int = HOLD_YEARS

    expenses: OperatingExpenses = field(default_factory=OperatingExpenses)

    # Growth assumptions applied year over year.
    rent_growth: float = 0.03
    expense_growth: float = 0.025
    appreciation: float = 0.03

    # Only used for the "with equity" IRR, which assumes a hypothetical
    # liquidation at the end of the projection so equity paydown and
    # appreciation are not silently ignored by a cash-flow-only IRR.
    selling_cost_pct: float = 0.06

    # Metadata carried along for the report (never used in arithmetic).
    label: str = ""

    def __post_init__(self) -> None:
        if self.purchase_price <= 0:
            raise ValueError("purchase_price must be positive")
        if self.monthly_rent is None:
            self.monthly_rent = self.purchase_price * RENT_PCT_OF_PRICE
        if not 0 <= self.vacancy_rate < 1:
            raise ValueError("vacancy_rate must be in [0, 1)")
        if not 0 <= self.down_payment_pct <= 1:
            raise ValueError("down_payment_pct must be in [0, 1]")

    # --- Derived capital-stack figures -------------------------------------

    @property
    def down_payment(self) -> float:
        return self.purchase_price * self.down_payment_pct

    @property
    def loan_amount(self) -> float:
        return self.purchase_price - self.down_payment

    @property
    def closing_costs(self) -> float:
        return self.purchase_price * self.closing_cost_pct

    @property
    def total_cash_invested(self) -> float:
        return self.down_payment + self.closing_costs + self.initial_capex

    def copy_with(self, **changes) -> "PropertyInputs":
        """Return an independent copy with fields overridden (never mutates self)."""
        expenses = changes.pop("expenses", None)
        new = replace(self, **changes)
        new.expenses = replace(expenses or self.expenses)
        return new


@dataclass
class YearResult:
    year: int
    gross_rent: float
    vacancy_loss: float
    effective_gross_income: float
    operating_expenses: float
    noi: float
    debt_service: float
    principal_paid: float
    interest_paid: float
    cash_flow: float
    loan_balance: float
    property_value: float
    cumulative_cash_flow: float
    equity: float


@dataclass
class UnderwritingResult:
    inputs: PropertyInputs
    years: List[YearResult]

    monthly_payment: float
    annual_debt_service: float
    total_cash_invested: float

    year1_noi: float
    year1_cash_flow: float
    monthly_cash_flow: float
    cap_rate: float
    cash_on_cash: float
    dscr: float
    gross_rent_multiplier: float
    one_percent_rule: float         # monthly rent / purchase price
    breakeven_occupancy: float      # share of gross rent needed to cover all costs
    irr_cash_flow_only: Optional[float]
    irr_with_equity: Optional[float]
    total_cash_flow: float
    equity_at_horizon: float

    @property
    def horizon(self) -> int:
        return len(self.years)


# --------------------------------------------------------------------------
# Loan math
# --------------------------------------------------------------------------

def monthly_payment(principal: float, annual_rate: float, term_years: int) -> float:
    """Standard fully-amortizing payment. Handles the 0% edge case."""
    if principal <= 0:
        return 0.0
    n = term_years * MONTHS
    if n <= 0:
        return principal
    r = annual_rate / MONTHS
    if r == 0:
        return principal / n
    factor = (1 + r) ** n
    return principal * r * factor / (factor - 1)


def amortize_year(balance: float, annual_rate: float, payment: float) -> tuple:
    """Run 12 monthly payments. Returns (interest, principal, ending_balance)."""
    r = annual_rate / MONTHS
    interest_total = 0.0
    principal_total = 0.0
    for _ in range(MONTHS):
        if balance <= 0:
            break
        interest = balance * r
        principal = min(payment - interest, balance)
        if principal < 0:  # negative amortization guard
            principal = 0.0
        balance -= principal
        interest_total += interest
        principal_total += principal
    return interest_total, principal_total, max(balance, 0.0)


# --------------------------------------------------------------------------
# IRR -- self-contained Newton-Raphson with a bisection safety net
# --------------------------------------------------------------------------

def npv(rate: float, cash_flows: List[float]) -> float:
    return sum(cf / ((1 + rate) ** i) for i, cf in enumerate(cash_flows))


def _npv_derivative(rate: float, cash_flows: List[float]) -> float:
    return sum(-i * cf / ((1 + rate) ** (i + 1)) for i, cf in enumerate(cash_flows) if i)


def irr(cash_flows: List[float], guess: float = 0.10,
        tol: float = 1e-7, max_iter: int = 100) -> Optional[float]:
    """
    Internal rate of return. Newton-Raphson first (fast), bisection fallback
    (robust) when Newton wanders outside the domain or fails to converge.
    Returns None when no sign change exists, i.e. no real IRR.
    """
    if not cash_flows or all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        return None

    rate = guess
    for _ in range(max_iter):
        try:
            value = npv(rate, cash_flows)
            derivative = _npv_derivative(rate, cash_flows)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(value) < tol:
            return rate
        if derivative == 0:
            break
        step = value / derivative
        new_rate = rate - step
        if new_rate <= -0.9999 or new_rate > 100:
            break
        rate = new_rate
    else:
        return None if abs(npv(rate, cash_flows)) > 1e-4 else rate

    # Bisection fallback over a wide, safe bracket.
    low, high = -0.9999, 10.0
    f_low, f_high = npv(low, cash_flows), npv(high, cash_flows)
    if f_low * f_high > 0:
        return None
    for _ in range(400):
        mid = (low + high) / 2
        f_mid = npv(mid, cash_flows)
        if abs(f_mid) < tol:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


# --------------------------------------------------------------------------
# The underwrite
# --------------------------------------------------------------------------

def project(inputs: PropertyInputs) -> List[YearResult]:
    """Year-by-year NOI / cash-flow projection with rent and expense growth."""
    payment = monthly_payment(inputs.loan_amount, inputs.interest_rate, inputs.loan_term_years)
    balance = inputs.loan_amount
    value = inputs.purchase_price
    cumulative = 0.0
    results: List[YearResult] = []

    for year in range(1, inputs.hold_years + 1):
        rent_factor = (1 + inputs.rent_growth) ** (year - 1)
        expense_factor = (1 + inputs.expense_growth) ** (year - 1)

        gross_rent = inputs.monthly_rent * MONTHS * rent_factor
        vacancy_loss = gross_rent * inputs.vacancy_rate
        egi = gross_rent - vacancy_loss

        fixed = inputs.expenses.fixed_annual * expense_factor
        variable = egi * inputs.expenses.variable_pct
        opex = fixed + variable
        noi = egi - opex

        interest, principal, balance = amortize_year(balance, inputs.interest_rate, payment)
        debt_service = interest + principal
        cash_flow = noi - debt_service
        cumulative += cash_flow

        if year > 1:
            value *= (1 + inputs.appreciation)

        results.append(
            YearResult(
                year=year,
                gross_rent=gross_rent,
                vacancy_loss=vacancy_loss,
                effective_gross_income=egi,
                operating_expenses=opex,
                noi=noi,
                debt_service=debt_service,
                principal_paid=principal,
                interest_paid=interest,
                cash_flow=cash_flow,
                loan_balance=balance,
                property_value=value,
                cumulative_cash_flow=cumulative,
                equity=value - balance,
            )
        )
    return results


def underwrite(inputs: PropertyInputs) -> UnderwritingResult:
    """Run the full underwrite and return every headline metric."""
    years = project(inputs)
    first = years[0]
    last = years[-1]

    payment = monthly_payment(inputs.loan_amount, inputs.interest_rate, inputs.loan_term_years)
    annual_debt_service = payment * MONTHS
    cash_invested = inputs.total_cash_invested
    basis = inputs.purchase_price + inputs.initial_capex

    cap_rate = first.noi / basis if basis else 0.0
    cash_on_cash = first.cash_flow / cash_invested if cash_invested else 0.0
    dscr = (first.noi / annual_debt_service) if annual_debt_service else float("inf")
    grm = basis / (inputs.monthly_rent * MONTHS) if inputs.monthly_rent else 0.0

    # Share of gross scheduled rent needed to cover opex + debt service.
    fixed = inputs.expenses.fixed_annual
    variable_pct = inputs.expenses.variable_pct
    denom = first.gross_rent * (1 - variable_pct)
    breakeven = ((fixed + first.debt_service) / denom) if denom > 0 else float("inf")

    # Cash-flow-only IRR: what the deal returns if you truly never sell.
    cf_only = [-cash_invested] + [y.cash_flow for y in years]
    # With-equity IRR: identical, plus a hypothetical liquidation in the final
    # year so equity paydown and appreciation are represented. Not a plan to
    # sell -- just the only honest way to put a number on a forever hold.
    net_sale = last.property_value * (1 - inputs.selling_cost_pct) - last.loan_balance
    with_equity = list(cf_only)
    with_equity[-1] += net_sale

    return UnderwritingResult(
        inputs=inputs,
        years=years,
        monthly_payment=payment,
        annual_debt_service=annual_debt_service,
        total_cash_invested=cash_invested,
        year1_noi=first.noi,
        year1_cash_flow=first.cash_flow,
        monthly_cash_flow=first.cash_flow / MONTHS,
        cap_rate=cap_rate,
        cash_on_cash=cash_on_cash,
        dscr=dscr,
        gross_rent_multiplier=grm,
        one_percent_rule=(inputs.monthly_rent / inputs.purchase_price) if inputs.purchase_price else 0.0,
        breakeven_occupancy=breakeven,
        irr_cash_flow_only=irr(cf_only),
        irr_with_equity=irr(with_equity),
        total_cash_flow=last.cumulative_cash_flow,
        equity_at_horizon=last.equity,
    )
