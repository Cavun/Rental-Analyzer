"""
financial_engine.py

ALL the arithmetic lives here. Pure Python, no third-party imports, no LLM
involvement, deterministic: the same PropertyInputs always produce the same
UnderwritingResult.

Conventions used throughout:
  * Rates are decimals (0.07 == 7%).
  * "Year 1" is the first 12 months of ownership, and it is underwritten
    PESSIMISTICALLY: it carries the lease-up vacancy you actually eat on a
    property you just closed on, on top of the steady-state vacancy rate.
    Year 2 is the stabilized year; both are reported.
  * NOI excludes debt service and excludes capex reserves is a common
    convention, but here capex reserve IS deducted before NOI (see
    OperatingExpenses.capex_reserve_pct) because a reserve you don't fund is
    a bill you take later. Cap rate is therefore slightly conservative
    versus a broker's pro forma -- that is intentional.
  * Property value is END OF YEAR: year N value == price * (1 + g) ** N.
  * Rent is a REQUIRED input. There is no 1%-of-price default anywhere in
    this codebase -- a made-up rent is the fastest way to underwrite a deal
    that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------
# House assumptions (the ones the underwriting spec fixes for every listing)
# --------------------------------------------------------------------------

DOWN_PAYMENT_PCT = 0.20        # 20% down on every deal
INTEREST_RATE = 0.07           # 7% fixed
LOAN_TERM_YEARS = 30           # 30-year amortization
CLOSING_COST_PCT = 0.03        # 3% of purchase price, same on every listing
INITIAL_CAPEX = 0.0            # make-ready; the GUI defaults it (see below)
VACANCY_RATE = 1.0 / 12.0      # one vacant month per year (8.33%)
HOLD_YEARS = 30                # "forever hold" -> project the full loan term
LEASE_UP_MONTHS = 1.0          # year 1 only: months to get the first tenant in
EXIT_CAP_SPREAD = 0.005        # default exit cap == year-1 cap rate + 0.50%

# Make-ready default: $2,500 or 1% of price, whichever is HIGHER. No property
# goes from someone else's house to a rentable unit for free.
MAKE_READY_FLOOR = 2500.0
MAKE_READY_PCT_OF_PRICE = 0.01

MONTHS = 12


def default_make_ready(purchase_price: float) -> float:
    """$2,500 or 1% of price, whichever is higher. Used to seed the GUI box."""
    return round(max(MAKE_READY_FLOOR, purchase_price * MAKE_READY_PCT_OF_PRICE), 2)


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
    monthly_rent: float                    # REQUIRED -- from comps, never derived
    down_payment_pct: float = DOWN_PAYMENT_PCT
    interest_rate: float = INTEREST_RATE
    loan_term_years: int = LOAN_TERM_YEARS
    closing_cost_pct: float = CLOSING_COST_PCT
    # Make-ready / rehab BEFORE the first tenant. Capital, not an operating
    # expense: it lands in total cash invested and in the cap-rate basis and
    # never touches NOI. Distinct from year1_repair_bump_pct below, which is
    # the running repair load AFTER move-in.
    initial_capex: float = INITIAL_CAPEX
    vacancy_rate: float = VACANCY_RATE
    hold_years: int = HOLD_YEARS

    expenses: OperatingExpenses = field(default_factory=OperatingExpenses)

    # --- Year-1 pessimism -------------------------------------------------
    # Extra vacancy in year 1 only, on top of the steady-state rate: the
    # months between closing and a paying tenant. Years 2+ are unaffected.
    lease_up_months: float = LEASE_UP_MONTHS
    # Optional extra repair load in year 1 only, as a share of EGI: an
    # OPERATING expense on top of the steady-state maintenance percentage,
    # for the punch list the tenant finds after move-in. It is not a second
    # helping of initial_capex -- that one is pre-tenant capital and is spent
    # before this ever applies. 0 keeps lease-up as the only year-1 penalty.
    year1_repair_bump_pct: float = 0.0

    # Growth assumptions applied year over year.
    rent_growth: float = 0.03
    expense_growth: float = 0.0125
    appreciation: float = 0.03

    # Exit assumptions. Both exits below are hypothetical liquidations at the
    # end of the projection, not a plan to sell -- they are the only honest
    # way to put a return number on a forever hold.
    selling_cost_pct: float = 0.06
    # None -> year-1 cap rate + EXIT_CAP_SPREAD (the property is older at exit).
    exit_cap_rate: Optional[float] = None

    # Metadata carried along for the report (never used in arithmetic).
    label: str = ""

    def __post_init__(self) -> None:
        if self.purchase_price <= 0:
            raise ValueError("purchase_price must be positive")
        if self.monthly_rent is None or self.monthly_rent <= 0:
            raise ValueError("monthly_rent is required")
        if not 0 <= self.vacancy_rate < 1:
            raise ValueError("vacancy_rate must be in [0, 1)")
        if not 0 <= self.down_payment_pct <= 1:
            raise ValueError("down_payment_pct must be in [0, 1]")
        if not 0 <= self.lease_up_months <= 11:
            raise ValueError("lease_up_months must be in [0, 11]")
        if self.vacancy_rate * MONTHS + self.lease_up_months >= MONTHS:
            raise ValueError(
                "total year-1 vacancy must be under 12 months "
                f"(vacancy {self.vacancy_rate * MONTHS:.1f} + lease-up "
                f"{self.lease_up_months:.1f})"
            )
        if self.exit_cap_rate is not None and self.exit_cap_rate <= 0:
            raise ValueError("exit_cap_rate must be positive")

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
    property_value: float      # END of year: price * (1 + appreciation) ** year
    cumulative_cash_flow: float
    equity: float


@dataclass
class UnderwritingResult:
    inputs: PropertyInputs
    years: List[YearResult]

    monthly_payment: float
    annual_debt_service: float
    total_cash_invested: float

    # Headline metrics are YEAR 1, which includes lease-up vacancy.
    year1_noi: float
    year1_cash_flow: float
    monthly_cash_flow: float
    cap_rate: float
    cash_on_cash: float
    dscr: float
    gross_rent_multiplier: float
    one_percent_rule: float         # monthly rent / purchase price (reference ratio)
    breakeven_occupancy: float      # share of gross rent needed to cover all costs

    # Stabilized (year 2) reference: the same deal once the lease-up month is
    # behind you. A deal that fails year 1 but clears year 2 is a timing
    # problem; one that fails both is a pricing problem.
    stabilized_noi: Optional[float]
    stabilized_cash_flow: Optional[float]
    stabilized_monthly_cash_flow: Optional[float]
    stabilized_dscr: Optional[float]
    stabilized_cash_on_cash: Optional[float]

    # Exits. Two independent estimates of what the asset is worth at horizon.
    exit_cap_rate_used: float
    terminal_noi: float             # NOI of year N+1, the buyer's year 1
    terminal_value_cap_rate: float  # terminal_noi / exit cap
    net_sale_appreciation: float    # net of selling costs and loan payoff
    net_sale_cap_rate: float
    exit_values_disagree: bool      # the two differ by more than 25%

    irr_appreciation_exit: Optional[float]
    irr_cap_rate_exit: Optional[float]
    irr_screened: Optional[float]   # the LOWER of the two -- what we screen on
    multiple_irr_possible: bool

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
# IRR -- Newton-Raphson with a real bisection safety net
# --------------------------------------------------------------------------

def npv(rate: float, cash_flows: List[float]) -> float:
    return sum(cf / ((1 + rate) ** i) for i, cf in enumerate(cash_flows))


def _npv_derivative(rate: float, cash_flows: List[float]) -> float:
    return sum(-i * cf / ((1 + rate) ** (i + 1)) for i, cf in enumerate(cash_flows) if i)


def count_sign_changes(cash_flows: List[float]) -> int:
    """
    Sign changes in the cash-flow series. Descartes' rule of signs: more than
    one means the NPV polynomial can have more than one real root, so the
    single number an IRR solver returns is not the whole story.
    """
    changes = 0
    previous = 0.0
    for cf in cash_flows:
        if cf == 0:
            continue
        if previous and (cf > 0) != (previous > 0):
            changes += 1
        previous = cf
    return changes


_IRR_LOW = -0.99
_IRR_HIGH = 10.0


def _bracket_roots(cash_flows: List[float],
                   low: float = _IRR_LOW,
                   high: float = _IRR_HIGH,
                   steps: int = 400) -> List[Tuple[float, float]]:
    """
    Scan NPV across [low, high] and return every (a, b) interval where it
    changes sign. Assuming the endpoints bracket a root is how a perfectly
    solvable deal comes back as "no IRR".
    """
    brackets: List[Tuple[float, float]] = []
    width = (high - low) / steps
    previous_rate = low
    try:
        previous_value = npv(low, cash_flows)
    except (OverflowError, ZeroDivisionError):
        return brackets
    for i in range(1, steps + 1):
        rate = low + i * width
        try:
            value = npv(rate, cash_flows)
        except (OverflowError, ZeroDivisionError):
            previous_rate = rate
            continue
        if value == 0.0:
            brackets.append((rate, rate))
        elif previous_value and (value > 0) != (previous_value > 0):
            brackets.append((previous_rate, rate))
        previous_rate, previous_value = rate, value
    return brackets


def _bisect(cash_flows: List[float], low: float, high: float,
            tol: float = 1e-7, max_iter: int = 400) -> float:
    if low == high:
        return low
    f_low = npv(low, cash_flows)
    for _ in range(max_iter):
        mid = (low + high) / 2
        f_mid = npv(mid, cash_flows)
        if abs(f_mid) < tol or (high - low) < 1e-12:
            return mid
        if f_low * f_mid < 0:
            high = mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2


def irr(cash_flows: List[float], guess: float = 0.10,
        tol: float = 1e-7, max_iter: int = 100) -> Optional[float]:
    """
    Internal rate of return.

    Newton-Raphson first (fast). If Newton wanders out of the domain OR
    simply runs out of iterations without converging, fall through to
    bisection -- exhausting max_iter is a reason to try harder, not a reason
    to report "no IRR". Bisection brackets are found by scanning NPV, not by
    assuming the endpoints straddle a root.

    When the series changes sign more than once there may be several real
    IRRs; the root nearest `guess` is returned. Callers that care should
    check count_sign_changes() and say so (UnderwritingResult does).

    Returns None only when there genuinely is no real root in [-99%, 1000%].
    """
    if not cash_flows or all(cf >= 0 for cf in cash_flows) or all(cf <= 0 for cf in cash_flows):
        return None

    # --- Newton-Raphson ---------------------------------------------------
    converged = False
    rate = guess
    for _ in range(max_iter):
        try:
            value = npv(rate, cash_flows)
            derivative = _npv_derivative(rate, cash_flows)
        except (OverflowError, ZeroDivisionError):
            break
        if abs(value) < tol:
            converged = True
            break
        if derivative == 0:
            break
        new_rate = rate - value / derivative
        if new_rate <= -0.9999 or new_rate > 100:
            break
        rate = new_rate
    else:
        # max_iter exhausted. Accept it only if it is actually a root;
        # otherwise fall through to bisection rather than giving up.
        try:
            converged = abs(npv(rate, cash_flows)) < 1e-4
        except (OverflowError, ZeroDivisionError):
            converged = False

    if converged and count_sign_changes(cash_flows) <= 1:
        return rate

    # --- Bisection over every bracket the scan actually found -------------
    brackets = _bracket_roots(cash_flows)
    if not brackets:
        return rate if converged else None

    roots = [_bisect(cash_flows, low, high, tol=tol) for low, high in brackets]
    if converged:
        roots.append(rate)
    # With several real roots, none is "the" IRR. Return the one nearest the
    # guess so the answer is at least stable and reproducible.
    return min(roots, key=lambda r: abs(r - guess))


# --------------------------------------------------------------------------
# The underwrite
# --------------------------------------------------------------------------

def project(inputs: PropertyInputs, years: Optional[int] = None) -> List[YearResult]:
    """
    Year-by-year NOI / cash-flow projection with rent and expense growth.

    `years` overrides inputs.hold_years -- used to run one extra year past the
    horizon for the terminal-value calculation without polluting the table.

    Year 1 carries the lease-up penalty; years 2+ are stabilized. Property
    value is end-of-year, so year 1 already includes one year of appreciation.
    """
    horizon = inputs.hold_years if years is None else years
    payment = monthly_payment(inputs.loan_amount, inputs.interest_rate, inputs.loan_term_years)
    balance = inputs.loan_amount
    value = inputs.purchase_price
    cumulative = 0.0
    results: List[YearResult] = []

    for year in range(1, horizon + 1):
        rent_factor = (1 + inputs.rent_growth) ** (year - 1)
        expense_factor = (1 + inputs.expense_growth) ** (year - 1)

        gross_rent = inputs.monthly_rent * MONTHS * rent_factor
        vacancy_loss = gross_rent * inputs.vacancy_rate
        if year == 1:
            # Months of no rent at all while the unit is turned and leased.
            vacancy_loss += inputs.monthly_rent * inputs.lease_up_months
        egi = gross_rent - vacancy_loss

        fixed = inputs.expenses.fixed_annual * expense_factor
        variable_pct = inputs.expenses.variable_pct
        if year == 1:
            variable_pct += inputs.year1_repair_bump_pct
        variable = egi * variable_pct
        opex = fixed + variable
        noi = egi - opex

        interest, principal, balance = amortize_year(balance, inputs.interest_rate, payment)
        debt_service = interest + principal
        cash_flow = noi - debt_service
        cumulative += cash_flow

        # End-of-year value: year N == price * (1 + g) ** N. Year 1 earns a
        # year of appreciation like every other year -- you hold it for 12
        # months, not zero.
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
    second = years[1] if len(years) > 1 else None

    payment = monthly_payment(inputs.loan_amount, inputs.interest_rate, inputs.loan_term_years)
    annual_debt_service = payment * MONTHS
    cash_invested = inputs.total_cash_invested
    basis = inputs.purchase_price + inputs.initial_capex

    cap_rate = first.noi / basis if basis else 0.0
    cash_on_cash = first.cash_flow / cash_invested if cash_invested else 0.0
    dscr = (first.noi / annual_debt_service) if annual_debt_service else float("inf")
    grm = basis / (inputs.monthly_rent * MONTHS) if inputs.monthly_rent else 0.0

    # Share of gross scheduled rent needed to cover opex + debt service, in
    # year 1 -- so it inherits the lease-up penalty like every other year-1
    # number. Lease-up is a fixed dollar cost, not a share of rent, so it
    # lands on the numerator.
    fixed = inputs.expenses.fixed_annual
    variable_pct = inputs.expenses.variable_pct + inputs.year1_repair_bump_pct
    lease_up_cost = inputs.monthly_rent * inputs.lease_up_months * (1 - variable_pct)
    denom = first.gross_rent * (1 - variable_pct)
    breakeven = (((fixed + first.debt_service + lease_up_cost) / denom)
                 if denom > 0 else float("inf"))

    # --- Exits ------------------------------------------------------------
    exit_cap = inputs.exit_cap_rate if inputs.exit_cap_rate is not None else cap_rate + EXIT_CAP_SPREAD
    # The buyer at horizon prices off THEIR year 1, which is our year N+1.
    # Run one extra year and throw the row away.
    extra = project(inputs, years=inputs.hold_years + 1)
    terminal_noi = extra[-1].noi
    terminal_value = (terminal_noi / exit_cap) if exit_cap > 0 else 0.0

    net_sale_appreciation = last.property_value * (1 - inputs.selling_cost_pct) - last.loan_balance
    net_sale_cap_rate = terminal_value * (1 - inputs.selling_cost_pct) - last.loan_balance

    reference = max(abs(net_sale_appreciation), abs(net_sale_cap_rate))
    disagree = bool(reference) and abs(net_sale_appreciation - net_sale_cap_rate) > 0.25 * reference

    operating = [-cash_invested] + [y.cash_flow for y in years]

    appreciation_flows = list(operating)
    appreciation_flows[-1] += net_sale_appreciation
    cap_rate_flows = list(operating)
    cap_rate_flows[-1] += net_sale_cap_rate

    irr_appreciation = irr(appreciation_flows)
    irr_cap = irr(cap_rate_flows)
    candidates = [r for r in (irr_appreciation, irr_cap) if r is not None]
    # Screen on the LOWER of the two: if the two exit methods disagree, the
    # conservative one is the one you can afford to be wrong about.
    screened = min(candidates) if candidates else None
    multiple = (count_sign_changes(appreciation_flows) > 1
                or count_sign_changes(cap_rate_flows) > 1)

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
        stabilized_noi=second.noi if second else None,
        stabilized_cash_flow=second.cash_flow if second else None,
        stabilized_monthly_cash_flow=(second.cash_flow / MONTHS) if second else None,
        stabilized_dscr=((second.noi / annual_debt_service) if annual_debt_service else float("inf"))
        if second else None,
        stabilized_cash_on_cash=((second.cash_flow / cash_invested) if cash_invested else 0.0)
        if second else None,
        exit_cap_rate_used=exit_cap,
        terminal_noi=terminal_noi,
        terminal_value_cap_rate=terminal_value,
        net_sale_appreciation=net_sale_appreciation,
        net_sale_cap_rate=net_sale_cap_rate,
        exit_values_disagree=disagree,
        irr_appreciation_exit=irr_appreciation,
        irr_cap_rate_exit=irr_cap,
        irr_screened=screened,
        multiple_irr_possible=multiple,
        total_cash_flow=last.cumulative_cash_flow,
        equity_at_horizon=last.equity,
    )
