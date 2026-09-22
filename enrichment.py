"""
enrichment.py

Layers your figures and a small number of transparent assumptions onto a
parsed listing, and flags what the listing implies but does not say.
Everything here is deterministic -- no network calls, no APIs, no LLM. Every
value carries a note saying where it came from so the report can show its
work.

What changed, and why it matters:

  1. RENT is a REQUIRED INPUT. It used to default to 1% of purchase price.
     That is a screening heuristic, not a comp, and it made every deal's
     most load-bearing number a function of the seller's asking price --
     raise the price and the model politely raised the rent to match. Pull
     rent from actual comps and pass it in.

  2. PROPERTY TAX is a REQUIRED INPUT. It used to be estimated from the
     seller's bill, taxable value, SEV and an assumed non-homestead millage.
     That chain had too many places to be quietly wrong, and in Michigan
     (and every state with an assessment cap or an owner-occupancy
     exemption) the seller's bill is not what a buyer pays: on transfer the
     taxable value uncaps to the SEV, and a rental loses the homestead
     exemption. Run the parcel through the official estimator --

         https://treas-secure.state.mi.us/ptestimator

     -- and pass that figure in (`enrich(..., property_tax_annual=4884)` or
     `main.py --tax 4884`). It is used verbatim.

  3. INSURANCE is still estimated from purchase price with a floor, bumped
     for older housing stock (pre-1960 wiring/plumbing/roof risk), unless
     you pass a figure of your own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from extraction import ListingData
from financial_engine import OperatingExpenses, PropertyInputs

# A tax figure outside this band, as a share of price, is far more likely a
# typo (a monthly bill, an extra zero) than a real assessment.
TAX_SANITY_LOW = 0.005
TAX_SANITY_HIGH = 0.04


@dataclass
class EnrichmentAssumptions:
    """Every knob this module turns. Override per-market as you learn."""

    # Insurance: landlord (DP-3) policy on a single-family rental.
    insurance_pct_of_price: float = 0.006   # 0.6% of price per year
    insurance_floor: float = 1200.0
    insurance_old_home_surcharge: float = 0.15  # +15% if built before...
    insurance_old_home_year: int = 1960

    # Where to get a verified tax number. There is no estimator here anymore.
    tax_estimator_url: str = "https://treas-secure.state.mi.us/ptestimator"

    # Operating expense ratios (share of effective gross income).
    management_pct: float = 0.0
    maintenance_pct: float = 0.08
    capex_reserve_pct: float = 0.08
    # Landlord-paid utilities, lawn/snow, rental certification. $0 by default
    # because on a single-family rental these are normally tenant-paid or
    # nominal -- set it when a particular property needs it.
    other_fixed_annual: float = 0.0


@dataclass
class EnrichedListing:
    listing: ListingData
    monthly_rent: float                 # your figure
    insurance_annual: float
    property_tax_annual: float          # your figure
    seller_property_tax_annual: Optional[float]   # reference only, never used
    hoa_monthly: float
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    detail: Dict[str, float] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Individual estimators
# --------------------------------------------------------------------------

def estimate_insurance(listing: ListingData, assumptions: EnrichmentAssumptions) -> float:
    base = max(listing.price * assumptions.insurance_pct_of_price, assumptions.insurance_floor)
    if listing.year_built and listing.year_built < assumptions.insurance_old_home_year:
        base *= (1 + assumptions.insurance_old_home_surcharge)
    return round(base, 2)


# --------------------------------------------------------------------------
# Warnings -- pure inspection, computes nothing
# --------------------------------------------------------------------------

def listing_warnings(listing: ListingData) -> List[str]:
    """
    Everything worth flagging about a listing that does not depend on a
    single underwriting assumption. Computes no values, so a caller that
    owns all the numbers itself (the GUI) can use it on its own.
    """
    warnings: List[str] = []

    if listing.hoa_monthly is None and (listing.hoa_yn or "").strip().lower() not in (
            "no", "n", "false", ""):
        warnings.append("Listing says there IS an HOA but states no dues; assumed $0 -- verify.")

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
    return warnings


def tax_sanity_warning(property_tax_annual: float, price: float) -> Optional[str]:
    """
    Non-blocking typo catcher. Outside roughly 0.5%-4% of price, a tax figure
    is usually a monthly bill or has a digit too many/few.
    """
    if not price or property_tax_annual <= 0:
        return None
    share = property_tax_annual / price
    if share < TAX_SANITY_LOW:
        return (
            f"Property tax ${property_tax_annual:,.0f}/yr is only {share:.2%} of price -- "
            "unusually low. Did you enter a monthly figure, or drop a digit?"
        )
    if share > TAX_SANITY_HIGH:
        return (
            f"Property tax ${property_tax_annual:,.0f}/yr is {share:.2%} of price -- "
            "unusually high. Check for an extra zero, or a figure that includes "
            "special assessments."
        )
    return None


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def enrich(listing: ListingData,
           assumptions: Optional[EnrichmentAssumptions] = None,
           *,
           property_tax_annual: float,
           monthly_rent: float,
           insurance_annual: Optional[float] = None) -> EnrichedListing:
    """
    Layer your figures onto a parsed listing.

    property_tax_annual: REQUIRED annual tax in dollars, e.g. from Michigan's
        official estimator at https://treas-secure.state.mi.us/ptestimator.
        Used verbatim. Nothing here estimates tax.
    monthly_rent: REQUIRED monthly rent in dollars, from local comps. Nothing
        here estimates rent either.
    insurance_annual: optional; estimated from price and age when omitted.
    """
    assumptions = assumptions or EnrichmentAssumptions()
    notes: List[str] = []
    warnings: List[str] = []

    if not listing.price:
        raise ValueError("Listing has no price; cannot underwrite.")

    if monthly_rent is None or monthly_rent <= 0:
        raise ValueError(
            "monthly_rent is required -- enter a rent from local comps "
            "(this model does not invent one)."
        )
    if property_tax_annual is None or property_tax_annual <= 0:
        raise ValueError(
            "property_tax_annual is required -- run the parcel through "
            f"{assumptions.tax_estimator_url} and enter the figure."
        )

    rent = round(float(monthly_rent), 2)
    notes.append(f"Rent (your figure): ${rent:,.0f}/mo.")

    if insurance_annual is not None:
        insurance = round(float(insurance_annual), 2)
        notes.append(f"Insurance (your figure): ${insurance:,.0f}/yr.")
    else:
        insurance = estimate_insurance(listing, assumptions)
        ins_note = (f"Insurance estimated at ${insurance:,.0f}/yr "
                    f"({assumptions.insurance_pct_of_price:.2%} of price")
        if listing.year_built and listing.year_built < assumptions.insurance_old_home_year:
            ins_note += (f", +{assumptions.insurance_old_home_surcharge:.0%} for "
                         f"pre-{assumptions.insurance_old_home_year} construction")
        notes.append(ins_note + ").")

    tax = round(float(property_tax_annual), 2)
    notes.append(f"Property tax (your figure): ${tax:,.0f}/yr -- used verbatim.")
    sanity = tax_sanity_warning(tax, listing.price)
    if sanity:
        warnings.append(sanity)

    hoa_monthly = listing.hoa_monthly
    if hoa_monthly is None:
        hoa_monthly = 0.0
        if (listing.hoa_yn or "").strip().lower() in ("no", "n", "false", ""):
            notes.append("No HOA dues found; assumed $0/mo.")

    warnings.extend(listing_warnings(listing))

    return EnrichedListing(
        listing=listing,
        monthly_rent=rent,
        insurance_annual=insurance,
        property_tax_annual=tax,
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
