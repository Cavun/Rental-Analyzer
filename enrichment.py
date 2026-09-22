"""
enrichment.py

Fills in what the listing does not state, and corrects what it states
misleadingly. Everything here is a transparent, deterministic assumption --
no network calls, no APIs, no LLM. Every filled value carries a note saying
where it came from so the report can show its work.

The three things this module gets right that a naive read of the listing
gets wrong:

  1. RENT. The listing never states rent. House rule: monthly rent == 1% of
     purchase price. This is a screening heuristic, not a market comp -- the
     report labels it as such.
  2. INSURANCE. Never stated. Estimated from purchase price with a floor,
     bumped for older housing stock (pre-1960 wiring/plumbing/roof risk).
  3. PROPERTY TAX -- the big one. The listing states the SELLER'S bill. In
     Michigan (and every state with an assessment cap or an owner-occupancy
     exemption) that bill is NOT what a buyer pays. On transfer the taxable
     value uncaps to the SEV, and a rental loses the homestead/principal-
     residence exemption (~18 mills of school operating tax in MI). Using the
     seller's number is the single most common way a deal that doesn't work
     looks like it does.

     Tax is therefore an OPTIONAL INPUT. If you have run the property through
     Michigan's official estimator --

         https://treas-secure.state.mi.us/ptestimator

     -- pass that figure in (`enrich(..., property_tax_annual=4884)` or
     `main.py --tax 4884`) and it is used verbatim, with no estimating at all.
     Only when no figure is supplied does the estimate chain below run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from extraction import ListingData
from financial_engine import (
    OperatingExpenses,
    PropertyInputs,
    RENT_PCT_OF_PRICE,
)


@dataclass
class EnrichmentAssumptions:
    """Every knob this module turns. Override per-market as you learn."""

    rent_pct_of_price: float = RENT_PCT_OF_PRICE

    # Insurance: landlord (DP-3) policy on a single-family rental.
    insurance_pct_of_price: float = 0.006   # 0.6% of price per year
    insurance_floor: float = 1200.0
    insurance_old_home_surcharge: float = 0.15  # +15% if built before...
    insurance_old_home_year: int = 1960

    # Post-transfer property tax. Only used when no verified figure is
    # supplied -- see estimate_post_transfer_tax().
    uncap_taxable_to_sev: bool = True       # taxable value resets to SEV at sale
    non_homestead_mills_adder: float = 18.0  # MI school operating millage a rental owes
    fallback_tax_rate_pct_of_price: float = 0.015  # used only when tax data is missing
    tax_growth_note_mills_cap: float = 80.0  # sanity ceiling on implied millage

    # Where to get a verified number instead of any estimate.
    tax_estimator_url: str = "https://treas-secure.state.mi.us/ptestimator"

    # Operating expense ratios (share of effective gross income).
    management_pct: float = 0.0
    maintenance_pct: float = 0.08
    capex_reserve_pct: float = 0.08
    other_fixed_annual: float = 0.0


@dataclass
class EnrichedListing:
    listing: ListingData
    monthly_rent: float
    insurance_annual: float
    property_tax_annual: float          # the number used for underwriting
    property_tax_source: str            # "provided" (verified) or "estimated"
    seller_property_tax_annual: Optional[float]
    hoa_monthly: float
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    detail: Dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Individual estimators
# --------------------------------------------------------------------------

def estimate_rent(price: float, assumptions: EnrichmentAssumptions) -> float:
    """House rule: monthly rent is 1% of purchase price."""
    return price * assumptions.rent_pct_of_price


def estimate_insurance(listing: ListingData, assumptions: EnrichmentAssumptions) -> float:
    base = max(listing.price * assumptions.insurance_pct_of_price, assumptions.insurance_floor)
    if listing.year_built and listing.year_built < assumptions.insurance_old_home_year:
        base *= (1 + assumptions.insurance_old_home_surcharge)
    return round(base, 2)


def estimate_post_transfer_tax(listing: ListingData,
                               assumptions: EnrichmentAssumptions) -> tuple:
    """
    Estimate the annual property tax a BUYER holding this as a RENTAL pays.

    Returns (annual_tax, note, warning_or_None).

    Method, best case first:
      A. Seller's bill + taxable value + SEV all known
         -> implied millage = seller_tax / (taxable_value / 1000)
         -> add the non-homestead adder if the seller had a homestead exemption
         -> apply that millage to the uncapped (SEV) taxable value.
      B. SEV known but no seller bill -> fall back to a % of price.
      C. Nothing usable -> % of price, flagged loudly.
    """
    seller_tax = listing.property_tax_annual
    taxable = listing.taxable_value
    sev = listing.sev

    if seller_tax and taxable and taxable > 0:
        implied_mills = seller_tax / (taxable / 1000.0)
        if implied_mills > assumptions.tax_growth_note_mills_cap:
            return (
                round(listing.price * assumptions.fallback_tax_rate_pct_of_price, 2),
                f"Implied millage ({implied_mills:.1f}) looked wrong; "
                f"fell back to {assumptions.fallback_tax_rate_pct_of_price:.2%} of price.",
                "Property tax is an ESTIMATE -- verify with the assessor.",
            )

        had_homestead = (listing.homestead_pct or 0) > 0
        mills = implied_mills + (assumptions.non_homestead_mills_adder if had_homestead else 0.0)

        new_taxable = sev if (assumptions.uncap_taxable_to_sev and sev) else taxable
        tax = mills * (new_taxable / 1000.0)

        bits = [f"implied {implied_mills:.1f} mills from seller's bill"]
        if had_homestead:
            bits.append(f"+{assumptions.non_homestead_mills_adder:.0f} mills non-homestead (rental)")
        if new_taxable != taxable:
            bits.append(f"taxable uncaps ${taxable:,.0f} -> SEV ${new_taxable:,.0f} at sale")
        note = "Post-transfer tax: " + "; ".join(bits) + f" = ${tax:,.0f}/yr"

        warning = None
        if seller_tax and tax > seller_tax * 1.25:
            warning = (
                f"Seller pays ${seller_tax:,.0f}/yr; a rental buyer should expect about "
                f"${tax:,.0f}/yr ({tax / seller_tax:.1f}x). Underwriting uses the higher figure."
            )
        return round(tax, 2), note, warning

    if seller_tax:
        note = (
            f"No taxable value/SEV on the listing; used the seller's ${seller_tax:,.0f}/yr "
            "as-is. This is almost certainly LOW for a non-homestead buyer."
        )
        return (
            round(seller_tax, 2),
            note,
            "Taxes not uncapped (missing assessment data) -- verify with the assessor.",
        )

    est = listing.price * assumptions.fallback_tax_rate_pct_of_price
    return (
        round(est, 2),
        f"No tax data on the listing; estimated at {assumptions.fallback_tax_rate_pct_of_price:.2%} of price.",
        "Property tax is a blind ESTIMATE -- verify before making an offer.",
    )


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def enrich(listing: ListingData,
           assumptions: Optional[EnrichmentAssumptions] = None,
           property_tax_annual: Optional[float] = None) -> EnrichedListing:
    """
    Layer assumptions onto a parsed listing.

    property_tax_annual: a VERIFIED annual tax figure, e.g. from Michigan's
        official estimator at https://treas-secure.state.mi.us/ptestimator.
        When supplied it is used verbatim and no tax estimating happens.
        When omitted, estimate_post_transfer_tax() fills it in.
    """
    assumptions = assumptions or EnrichmentAssumptions()
    notes: List[str] = []
    warnings: List[str] = []

    if not listing.price:
        raise ValueError("Listing has no price; cannot underwrite.")

    rent = estimate_rent(listing.price, assumptions)
    notes.append(
        f"Rent assumed at {assumptions.rent_pct_of_price:.2%} of price = ${rent:,.0f}/mo "
        "(screening heuristic, NOT a market comp -- confirm against local rent comps)."
    )

    insurance = estimate_insurance(listing, assumptions)
    ins_note = f"Insurance estimated at ${insurance:,.0f}/yr ({assumptions.insurance_pct_of_price:.2%} of price"
    if listing.year_built and listing.year_built < assumptions.insurance_old_home_year:
        ins_note += f", +{assumptions.insurance_old_home_surcharge:.0%} for pre-{assumptions.insurance_old_home_year} construction"
    notes.append(ins_note + ").")

    if property_tax_annual is not None:
        if property_tax_annual < 0:
            raise ValueError("property_tax_annual cannot be negative")
        tax = round(float(property_tax_annual), 2)
        tax_source = "provided"
        notes.append(
            f"Property tax PROVIDED: ${tax:,.0f}/yr -- used verbatim, not estimated."
        )
        # Still show what the listing implied, so a typo or a stale estimator
        # run stands out instead of quietly setting the whole underwrite.
        estimated, estimate_note, _ = estimate_post_transfer_tax(listing, assumptions)
        notes.append(f"(For contrast, the listing-derived estimate would be: {estimate_note})")
        if estimated and abs(tax - estimated) > max(0.35 * estimated, 750):
            warnings.append(
                f"Provided tax ${tax:,.0f}/yr differs sharply from the listing-derived "
                f"estimate ${estimated:,.0f}/yr. Worth a second look at the estimator "
                "inputs (taxable value, homestead status, millage) before trusting either."
            )
    else:
        tax, tax_note, tax_warning = estimate_post_transfer_tax(listing, assumptions)
        tax_source = "estimated"
        notes.append(tax_note)
        notes.append(
            "Tax is an ESTIMATE. For a verified figure, run the parcel through "
            f"{assumptions.tax_estimator_url} and pass it back in with --tax."
        )
        if tax_warning:
            warnings.append(tax_warning)

    hoa_monthly = listing.hoa_monthly
    if hoa_monthly is None:
        hoa_monthly = 0.0
        if (listing.hoa_yn or "").strip().lower() not in ("no", "n", "false", ""):
            warnings.append("Listing says there IS an HOA but states no dues; assumed $0 -- verify.")
        else:
            notes.append("No HOA dues found; assumed $0/mo.")

    for name in listing.missing_fields:
        warnings.append(f"Listing did not state '{name}' -- parsed value is missing or zero.")

    if listing.year_built and listing.year_built < 1960:
        warnings.append(
            f"Built {listing.year_built}: budget for knob-and-tube wiring, galvanized supply "
            "lines and lead paint disclosure on any turn."
        )
    if (listing.mls_status or "").strip().lower() not in ("active", ""):
        warnings.append(f"MLS status is '{listing.mls_status}' -- not an active listing.")
    if (listing.zoning or "").strip().lower() not in ("res", "residential", "r-1", ""):
        warnings.append(f"Zoning '{listing.zoning}' -- confirm long-term rental is permitted.")

    return EnrichedListing(
        listing=listing,
        monthly_rent=round(rent, 2),
        insurance_annual=insurance,
        property_tax_annual=tax,
        property_tax_source=tax_source,
        seller_property_tax_annual=listing.property_tax_annual,
        hoa_monthly=float(hoa_monthly),
        notes=notes,
        warnings=warnings,
        detail={
            "rent_to_price": rent / listing.price,
            "tax_to_price": tax / listing.price,
        },
    )


def to_property_inputs(enriched: EnrichedListing,
                       assumptions: Optional[EnrichmentAssumptions] = None,
                       **overrides) -> PropertyInputs:
    """Bridge enrichment output into the financial engine's input dataclass."""
    assumptions = assumptions or EnrichmentAssumptions()
    expenses = OperatingExpenses(
        property_tax_annual=enriched.property_tax_annual,
        insurance_annual=enriched.insurance_annual,
        hoa_annual=enriched.hoa_monthly * 12,
        other_fixed_annual=assumptions.other_fixed_annual,
        management_pct=assumptions.management_pct,
        maintenance_pct=assumptions.maintenance_pct,
        capex_reserve_pct=assumptions.capex_reserve_pct,
    )
    return PropertyInputs(
        purchase_price=enriched.listing.price,
        monthly_rent=enriched.monthly_rent,
        expenses=expenses,
        label=enriched.listing.full_address or enriched.listing.address,
        **overrides,
    )
