"""
tax_engine.py

The after-tax layer. It READS a finished UnderwritingResult and never changes
a single pre-tax number -- pre-tax underwriting is a property question, after
-tax return is a question about YOU, and mixing the two makes a deal look
different depending on whose spreadsheet it lands in.

What it models
--------------
  * Straight-line depreciation over 27.5 years on the BUILDING share of
    basis. Land is not depreciable; the county assessor's record usually
    carries the land/improvement split, so put the real number in.
  * Annual taxable income = NOI - mortgage interest - depreciation.
    Principal is NOT deducted; it is a balance-sheet movement, not an
    expense. This is the line that turns a positive-cash-flow rental into a
    paper loss, which is most of the point.
  * Passive activity loss rules, in the one form that matters to a
    small landlord: the $25,000 active-participation allowance, phased out
    between $100k and $150k MAGI. Losses you cannot use are SUSPENDED and
    carried forward, offsetting future passive income first and releasing in
    full on a complete disposition.
  * Sale: gain up to accumulated depreciation is unrecaptured section 1250
    gain (25% federal maximum); the rest is long-term capital gain. State
    tax and, optionally, the 3.8% net investment income tax apply on top.

Deliberate simplifications, each of which slightly OVERSTATES deductions
-----------------------------------------------------------------------
  * The capex reserve is treated as deductible when accrued. Strictly, capex
    is capitalized and depreciated over its own recovery period, and an
    unspent reserve is not deductible at all.
  * Loan costs are folded into closing costs and depreciated with the
    building. Strictly they amortize over the loan term.
  * Depreciation is a full year in year 1 unless you supply a purchase
    month, in which case the mid-month convention applies.

Nothing here is tax advice. It is a model, and it is wrong at the edges by
design; take the output to someone who signs returns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from financial_engine import UnderwritingResult, count_sign_changes, irr

# Federal statutory rates that are not really "assumptions".
SECTION_1250_MAX_RATE = 0.25
NIIT_RATE = 0.038
RECOVERY_PERIOD_YEARS = 27.5

# Active-participation allowance and its MAGI phase-out band.
ACTIVE_PARTICIPATION_ALLOWANCE = 25000.0
PHASEOUT_START = 100000.0
PHASEOUT_END = 150000.0


@dataclass
class TaxAssumptions:
    """Your tax position. Defaults are a middle-bracket Michigan filer."""

    # Land is not depreciable. 80% building is a common default; the
    # assessor's land/improvement split is better.
    building_share: float = 0.80

    # Combined marginal ORDINARY rate on rental income.
    federal_ordinary_rate: float = 0.22
    state_ordinary_rate: float = 0.0425        # Michigan
    # Add a city income tax where one applies. Grand Rapids levies one, and
    # it reaches a non-resident's rental income from property in the city.
    city_ordinary_rate: float = 0.0

    # At sale.
    capital_gains_rate: float = 0.15           # federal long-term
    depreciation_recapture_rate: float = SECTION_1250_MAX_RATE
    state_gain_rate: float = 0.0425            # Michigan taxes gains as income
    niit: bool = False                         # 3.8% net investment income tax
    niit_rate: float = NIIT_RATE

    # Passive activity losses.
    passive_losses_usable: bool = True         # active participation, under the MAGI cap
    magi: Optional[float] = None               # None -> no phase-out applied
    allowance: float = ACTIVE_PARTICIPATION_ALLOWANCE

    # Depreciation timing. None -> full-year approximation in year 1.
    purchase_month: Optional[int] = None       # 1-12, mid-month convention
    recovery_period_years: float = RECOVERY_PERIOD_YEARS

    def __post_init__(self) -> None:
        if not 0 < self.building_share <= 1:
            raise ValueError("building_share must be in (0, 1]")
        if self.recovery_period_years <= 0:
            raise ValueError("recovery_period_years must be positive")
        if self.purchase_month is not None and not 1 <= self.purchase_month <= 12:
            raise ValueError("purchase_month must be 1-12")

    @property
    def ordinary_rate(self) -> float:
        """Combined marginal rate applied to rental ordinary income."""
        return self.federal_ordinary_rate + self.state_ordinary_rate + self.city_ordinary_rate

    def usable_allowance(self) -> float:
        """
        The $25k active-participation allowance after MAGI phase-out: it drops
        by $0.50 for every $1 of MAGI over $100k and is gone at $150k.
        """
        if not self.passive_losses_usable:
            return 0.0
        if self.magi is None or self.magi <= PHASEOUT_START:
            return self.allowance
        if self.magi >= PHASEOUT_END:
            return 0.0
        return max(0.0, self.allowance - 0.5 * (self.magi - PHASEOUT_START))


@dataclass
class TaxYear:
    year: int
    depreciation: float
    interest: float
    noi: float
    taxable_income: float          # after suspended losses are applied
    suspended_loss_used: float     # released against this year's passive income
    suspended_loss_added: float    # not usable this year, carried forward
    suspended_balance: float       # running carryforward at year end
    tax: float                     # negative == a tax benefit taken this year
    pre_tax_cash_flow: float
    after_tax_cash_flow: float


@dataclass
class SaleTax:
    """Tax due on a hypothetical sale at horizon, for one exit method."""

    method: str
    gross_sale_price: float
    selling_costs: float
    net_sale_price: float          # gross less selling costs, BEFORE loan payoff
    loan_balance: float
    net_proceeds: float            # cash in hand before tax
    original_basis: float
    accumulated_depreciation: float
    adjusted_basis: float
    total_gain: float
    recapture_gain: float
    capital_gain: float
    recapture_tax: float
    capital_gains_tax: float
    state_tax: float
    niit_tax: float
    suspended_loss_released: float
    suspended_loss_benefit: float  # the carryforward finally deducted, as tax saved
    total_tax: float
    after_tax_proceeds: float


@dataclass
class AfterTaxResult:
    assumptions: TaxAssumptions
    years: List[TaxYear]
    depreciable_basis: float
    original_basis: float
    accumulated_depreciation: float
    suspended_balance_at_sale: float

    year1_after_tax_cash_flow: float
    total_tax_on_operations: float

    sale_appreciation: SaleTax
    sale_cap_rate: SaleTax

    irr_appreciation_exit: Optional[float]
    irr_cap_rate_exit: Optional[float]
    irr_screened: Optional[float]
    multiple_irr_possible: bool

    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Depreciation
# --------------------------------------------------------------------------

def depreciable_basis(result: UnderwritingResult, assumptions: TaxAssumptions) -> float:
    """
    (price + closing costs + make-ready) * building share.

    Closing costs and make-ready are added to basis rather than expensed,
    which is right for the acquisition costs that dominate them. Loan costs
    strictly amortize over the loan term instead; they are small enough
    relative to the rest that they ride along here.
    """
    inputs = result.inputs
    total = inputs.purchase_price + inputs.closing_costs + inputs.initial_capex
    return total * assumptions.building_share


def total_basis(result: UnderwritingResult) -> float:
    """Original basis INCLUDING land -- the figure the sale gain works from."""
    inputs = result.inputs
    return inputs.purchase_price + inputs.closing_costs + inputs.initial_capex


def annual_depreciation(basis: float, year: int, assumptions: TaxAssumptions) -> float:
    """
    Straight line over the recovery period. Year 1 is a full year unless a
    purchase month is supplied, in which case the mid-month convention gives
    (12.5 - month) / 12 of a year, and the tail year picks up the remainder.
    """
    if year < 1:
        return 0.0
    full = basis / assumptions.recovery_period_years
    first_year_share = (1.0 if assumptions.purchase_month is None
                        else max(0.0, (12.5 - assumptions.purchase_month) / 12.0))
    share = first_year_share if year == 1 else 1.0

    # Years already written off before this one, so the last year takes only
    # the stub that is left and nothing depreciates past its basis.
    already = 0.0 if year == 1 else full * (first_year_share + (year - 2))
    remaining = max(0.0, basis - already)
    return max(0.0, min(full * share, remaining))


# --------------------------------------------------------------------------
# Annual operations
# --------------------------------------------------------------------------

def _operating_years(result: UnderwritingResult,
                     assumptions: TaxAssumptions,
                     basis: float) -> List[TaxYear]:
    rate = assumptions.ordinary_rate
    allowance = assumptions.usable_allowance()
    suspended = 0.0
    rows: List[TaxYear] = []

    for y in result.years:
        depreciation = annual_depreciation(basis, y.year, assumptions)
        raw_taxable = y.noi - y.interest_paid - depreciation

        used = added = 0.0
        if raw_taxable > 0:
            # Suspended losses offset positive passive income first.
            used = min(suspended, raw_taxable)
            suspended -= used
            taxable = raw_taxable - used
            tax = taxable * rate
        else:
            loss = -raw_taxable
            deductible = min(loss, allowance) if allowance > 0 else 0.0
            added = loss - deductible
            suspended += added
            taxable = -deductible
            tax = taxable * rate      # negative: a benefit against other income

        rows.append(TaxYear(
            year=y.year,
            depreciation=depreciation,
            interest=y.interest_paid,
            noi=y.noi,
            taxable_income=taxable,
            suspended_loss_used=used,
            suspended_loss_added=added,
            suspended_balance=suspended,
            tax=tax,
            pre_tax_cash_flow=y.cash_flow,
            after_tax_cash_flow=y.cash_flow - tax,
        ))
    return rows


# --------------------------------------------------------------------------
# Sale
# --------------------------------------------------------------------------

def _sale_tax(method: str,
              gross_sale_price: float,
              result: UnderwritingResult,
              assumptions: TaxAssumptions,
              accumulated_depreciation: float,
              suspended: float) -> SaleTax:
    inputs = result.inputs
    last = result.years[-1]

    selling_costs = gross_sale_price * inputs.selling_cost_pct
    net_sale_price = gross_sale_price - selling_costs
    net_proceeds = net_sale_price - last.loan_balance

    original = total_basis(result)
    adjusted = original - accumulated_depreciation
    total_gain = net_sale_price - adjusted

    if total_gain > 0:
        recapture_gain = min(accumulated_depreciation, total_gain)
        capital_gain = total_gain - recapture_gain
    else:
        # A loss: nothing to recapture, and the loss is ordinary under §1231.
        recapture_gain = 0.0
        capital_gain = total_gain

    recapture_tax = recapture_gain * assumptions.depreciation_recapture_rate
    capital_gains_tax = capital_gain * assumptions.capital_gains_rate
    state_tax = max(total_gain, 0.0) * assumptions.state_gain_rate
    niit_tax = (max(total_gain, 0.0) * assumptions.niit_rate) if assumptions.niit else 0.0

    # A complete disposition frees every suspended passive loss, deductible
    # against income of any kind at the ordinary rate.
    suspended_benefit = suspended * assumptions.ordinary_rate

    total_tax = recapture_tax + capital_gains_tax + state_tax + niit_tax - suspended_benefit

    return SaleTax(
        method=method,
        gross_sale_price=gross_sale_price,
        selling_costs=selling_costs,
        net_sale_price=net_sale_price,
        loan_balance=last.loan_balance,
        net_proceeds=net_proceeds,
        original_basis=original,
        accumulated_depreciation=accumulated_depreciation,
        adjusted_basis=adjusted,
        total_gain=total_gain,
        recapture_gain=recapture_gain,
        capital_gain=capital_gain,
        recapture_tax=recapture_tax,
        capital_gains_tax=capital_gains_tax,
        state_tax=state_tax,
        niit_tax=niit_tax,
        suspended_loss_released=suspended,
        suspended_loss_benefit=suspended_benefit,
        total_tax=total_tax,
        after_tax_proceeds=net_proceeds - total_tax,
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def after_tax(result: UnderwritingResult,
              assumptions: Optional[TaxAssumptions] = None) -> AfterTaxResult:
    """
    Run the after-tax layer over a finished underwrite. `result` is not
    modified and no pre-tax figure is recomputed.
    """
    assumptions = assumptions or TaxAssumptions()
    basis = depreciable_basis(result, assumptions)
    rows = _operating_years(result, assumptions, basis)

    accumulated = sum(row.depreciation for row in rows)
    suspended = rows[-1].suspended_balance if rows else 0.0

    sale_appreciation = _sale_tax(
        "Appreciation exit", result.years[-1].property_value,
        result, assumptions, accumulated, suspended)
    sale_cap_rate = _sale_tax(
        "Cap-rate exit", result.terminal_value_cap_rate,
        result, assumptions, accumulated, suspended)

    operating_flows = [-result.total_cash_invested] + [row.after_tax_cash_flow for row in rows]

    appreciation_flows = list(operating_flows)
    appreciation_flows[-1] += sale_appreciation.after_tax_proceeds
    cap_rate_flows = list(operating_flows)
    cap_rate_flows[-1] += sale_cap_rate.after_tax_proceeds

    irr_appreciation = irr(appreciation_flows)
    irr_cap = irr(cap_rate_flows)
    candidates = [r for r in (irr_appreciation, irr_cap) if r is not None]
    screened = min(candidates) if candidates else None

    notes = [
        f"Depreciable basis {basis:,.0f} = (price + closing costs + make-ready) x "
        f"{assumptions.building_share:.0%} building share; land is not depreciable.",
        f"Straight line over {assumptions.recovery_period_years:g} years"
        + (f", mid-month convention from month {assumptions.purchase_month}."
           if assumptions.purchase_month else ", full-year approximation in year 1."),
        f"Ordinary rate {assumptions.ordinary_rate:.2%} = "
        f"{assumptions.federal_ordinary_rate:.2%} federal + "
        f"{assumptions.state_ordinary_rate:.2%} state"
        + (f" + {assumptions.city_ordinary_rate:.2%} city." if assumptions.city_ordinary_rate
           else "."),
        "Capex reserve is treated as deductible when accrued. Strictly capex is "
        "capitalized, so this slightly overstates deductions.",
        "Loan costs ride in the depreciable basis; strictly they amortize over the "
        "loan term.",
    ]
    if assumptions.passive_losses_usable:
        allowance = assumptions.usable_allowance()
        notes.append(
            f"Passive losses usable now, up to {allowance:,.0f} a year"
            + (f" ({ACTIVE_PARTICIPATION_ALLOWANCE:,.0f} allowance phased out at "
               f"MAGI {assumptions.magi:,.0f})." if assumptions.magi is not None else
               " (active-participation allowance).")
        )
    else:
        notes.append("Passive losses suspended and carried forward; they offset future "
                     "passive income first and release in full at sale.")
    notes.append(
        "A property you never sell gets a basis step-up at death, which wipes out "
        "the exit tax entirely. The after-tax IRR below assumes a TAXABLE SALE at "
        "horizon, so it is the pessimistic end of the range for a forever hold."
    )

    return AfterTaxResult(
        assumptions=assumptions,
        years=rows,
        depreciable_basis=basis,
        original_basis=total_basis(result),
        accumulated_depreciation=accumulated,
        suspended_balance_at_sale=suspended,
        year1_after_tax_cash_flow=rows[0].after_tax_cash_flow if rows else 0.0,
        total_tax_on_operations=sum(row.tax for row in rows),
        sale_appreciation=sale_appreciation,
        sale_cap_rate=sale_cap_rate,
        irr_appreciation_exit=irr_appreciation,
        irr_cap_rate_exit=irr_cap,
        irr_screened=screened,
        multiple_irr_possible=(count_sign_changes(appreciation_flows) > 1
                               or count_sign_changes(cap_rate_flows) > 1),
        notes=notes,
    )
