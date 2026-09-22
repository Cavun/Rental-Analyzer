"""
extraction.py

Turns a raw MLS listing into structured fields.

IMPORTANT: flexmls (link.flexmls.com / www.flexmls.com) disallows automated
access via robots.txt, and most consumer portals (Zillow, Realtor.com,
Redfin) prohibit scraping in their terms of service. This module does NOT
auto-fetch listing URLs and makes no network calls of any kind. It supports
three honest paths:

  1. parse_flexmls_html() -- you open the link yourself and save/paste the
     page HTML ("View Source" or "Inspect Element" -> copy outer HTML).
     This is the primary, most reliable path.
  2. parse_pasted_text()  -- you copy the visible listing details as text.
     Best-effort regex fallback for non-flexmls layouts.
  3. from_structured_input() -- you already have clean fields (e.g. from a
     licensed data API such as the Spark Platform / RESO Web API, RentCast,
     ATTOM or Estated) and just wire the response into this shape.

parse_listing() sniffs which of (1) or (2) applies so callers don't have to.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

from bs4 import BeautifulSoup


@dataclass
class ListingData:
    """Everything we know about a property *before* any assumption is layered on."""

    # --- Core identity / pricing ---
    address: str = ""
    city: Optional[str] = None
    state: Optional[str] = None
    postal_code: Optional[str] = None
    price: float = 0.0
    beds: Optional[float] = None
    baths: Optional[float] = None
    sqft: Optional[float] = None
    year_built: Optional[int] = None
    property_tax_annual: Optional[float] = None  # the SELLER'S actual annual bill
    hoa_monthly: Optional[float] = None
    days_on_market: Optional[int] = None
    raw_text: str = ""

    # --- Fields reliably available only from the HTML/DOM source ---
    mls_id: Optional[str] = None
    mls_status: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    county: Optional[str] = None
    municipality: Optional[str] = None
    lot_acres: Optional[float] = None
    taxable_value: Optional[float] = None  # current assessed value -- NOT what you'll pay post-sale
    sev: Optional[float] = None            # State Equalized Value (MI) -- ~= 50% of market value
    homestead_pct: Optional[float] = None  # 100 => seller's bill is the *homestead* (owner-occupied) rate
    tax_year: Optional[int] = None
    hoa_yn: Optional[str] = None
    zoning: Optional[str] = None

    # --- Condition / desirability signals used by the underwriting notes ---
    property_sub_type: Optional[str] = None
    school_district: Optional[str] = None
    garage_spaces: Optional[float] = None
    basement: Optional[str] = None
    stories: Optional[float] = None
    new_construction: Optional[str] = None
    waterfront: Optional[str] = None
    public_remarks: Optional[str] = None

    # Every label/value pair we found, so nothing is silently thrown away.
    extra_fields: Dict[str, str] = field(default_factory=dict)
    # Field names the parser could not find; surfaced in the report as caveats.
    missing_fields: list = field(default_factory=list)

    @property
    def full_address(self) -> str:
        locality = ", ".join(p for p in [self.address, self.city] if p)
        tail = " ".join(p for p in [self.state, self.postal_code] if p)
        return " ".join(p for p in [locality, tail] if p).strip()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("raw_text", None)
        return d


# --------------------------------------------------------------------------
# Plain-text (fallback) parsing
# --------------------------------------------------------------------------

_PRICE_RE = re.compile(r"\$\s?([\d,]{3,})(?!\s*/\s*(?:mo|month|yr|year))", re.IGNORECASE)
_TAX_RE = re.compile(r"(?:tax(?:es)?)[^\d$]{0,15}\$?\s?([\d,]+)\s*(?:/\s*(?:yr|year))?", re.IGNORECASE)
_HOA_RE = re.compile(r"HOA[^\d$]{0,15}\$?\s?([\d,]+)\s*(?:/\s*(?:mo|month))?", re.IGNORECASE)
_BEDBATH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:bed|bd|br)\w*.{0,20}?(\d+(?:\.\d+)?)\s*(?:bath|ba)\w*", re.IGNORECASE)
_SQFT_RE = re.compile(r"([\d,]{3,6})\s*(?:sq\.?\s?ft|sqft)", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:built|year built)[^\d]{0,10}(\d{4})", re.IGNORECASE)
_DOM_RE = re.compile(r"(\d+)\s*days?\s*on\s*market", re.IGNORECASE)
_CSZ_RE = re.compile(r"([A-Za-z .'-]+),\s*([A-Z]{2})\s+(\d{5})")

# Fields we consider important enough that their absence is worth reporting.
CRITICAL_FIELDS = ("price", "property_tax_annual", "beds", "baths", "sqft", "year_built")


def _to_float(s: str) -> float:
    return float(s.replace(",", ""))


def _to_num(s: Optional[str]) -> Optional[float]:
    """Parse '1,925', '$285,000', '0.18', '60x130' (-> None) into a float or None."""
    if s is None:
        return None
    cleaned = str(s).replace(",", "").replace("$", "").strip()
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_int(s: Optional[str]) -> Optional[int]:
    v = _to_num(s)
    return int(v) if v is not None else None


def _note_missing(data: ListingData) -> ListingData:
    data.missing_fields = [
        name for name in CRITICAL_FIELDS if not getattr(data, name, None)
    ]
    return data


def parse_pasted_text(text: str) -> ListingData:
    """
    Best-effort regex parse of listing text a human copied from the page.
    Intentionally simple; it will miss fields on unusual layouts. It is a fast
    first pass, not a guarantee -- always sanity-check price, taxes and HOA
    against what you actually saw before underwriting.
    """
    data = ListingData(raw_text=text)

    price_matches = _PRICE_RE.findall(text)
    if price_matches:
        # First plausible price is usually the list price.
        data.price = _to_float(price_matches[0])

    tax_match = _TAX_RE.search(text)
    if tax_match:
        data.property_tax_annual = _to_float(tax_match.group(1))

    hoa_match = _HOA_RE.search(text)
    if hoa_match:
        data.hoa_monthly = _to_float(hoa_match.group(1))

    bedbath_match = _BEDBATH_RE.search(text)
    if bedbath_match:
        data.beds = float(bedbath_match.group(1))
        data.baths = float(bedbath_match.group(2))

    sqft_match = _SQFT_RE.search(text)
    if sqft_match:
        data.sqft = _to_float(sqft_match.group(1))

    year_match = _YEAR_RE.search(text)
    if year_match:
        data.year_built = int(year_match.group(1))

    dom_match = _DOM_RE.search(text)
    if dom_match:
        data.days_on_market = int(dom_match.group(1))

    csz = _CSZ_RE.search(text)
    if csz:
        data.city, data.state, data.postal_code = (
            csz.group(1).strip(),
            csz.group(2),
            csz.group(3),
        )

    # Naive address guess: first line that isn't purely numbers/symbols.
    for line in text.splitlines():
        line = line.strip()
        if len(line) > 8 and any(c.isalpha() for c in line) and not line.lower().startswith(("$", "price")):
            data.address = line
            break

    return _note_missing(data)


def from_structured_input(**kwargs) -> ListingData:
    """Direct constructor for when you already have clean fields (from a real API)."""
    return _note_missing(ListingData(**kwargs))


# --------------------------------------------------------------------------
# flexmls / Spark Platform HTML parsing (the reliable path)
# --------------------------------------------------------------------------

def _extract_embedded_listing_json(soup: BeautifulSoup) -> Dict[str, Any]:
    container = soup.find(attrs={"data-map--ldp-listing": True})
    if container is None:
        return {}
    # bs4 already HTML-unescapes attribute values.
    raw_json = container.get("data-map--ldp-listing") or ""
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _extract_detail_fields(soup: BeautifulSoup) -> Dict[str, str]:
    """
    Walk the '.listing-detail-field-line' / '.listing-detail-field-label' pairs.

    A label can legitimately repeat across cards (e.g. 'Tax ID #'); the first
    non-empty value wins so a later blank card cannot clobber a real value.
    """
    fields: Dict[str, str] = {}
    for line in soup.select(".listing-detail-field-line"):
        label_el = line.select_one(".listing-detail-field-label")
        if label_el is None:
            continue
        label = label_el.get_text(strip=True).rstrip(":")
        full_text = line.get_text(" ", strip=True)
        label_raw = label_el.get_text(strip=True)
        if full_text.startswith(label_raw):
            value = full_text[len(label_raw):].strip()
        else:
            value = full_text.replace(label_raw, "", 1).strip()
        value = value.lstrip(":").strip()
        if value and not fields.get(label):
            fields[label] = value
    return fields


def _first(fields: Dict[str, str], *labels: str) -> Optional[str]:
    """Return the first present, non-empty value among several candidate labels."""
    for label in labels:
        value = fields.get(label)
        if value:
            return value
    return None


def parse_flexmls_html(html: str) -> ListingData:
    """
    Parse a flexmls / Spark Platform listing-detail page.

    Two structural reasons this beats parsing rendered text:

    1. The page embeds a clean JSON payload in a `data-map--ldp-listing`
       attribute (address, city/state/zip, price, beds/baths, MLS ID, status,
       lat/long) -- effectively a private API response sitting in the HTML,
       typed and unambiguous, with none of the regex guesswork rendered text
       requires.
    2. Every other field (taxes, lot size, year built, zoning, HOA) sits in a
       consistent `.listing-detail-field-line` / `.listing-detail-field-label`
       structure that a CSS selector walks reliably, instead of guessing at
       whatever spacing the rendered text happened to use.

    Caveat: this parser is specific to the Spark Platform / flexmls template.
    A different IDX vendor's HTML will need its own parser -- the class names
    and JSON shape here won't transfer.
    """
    soup = BeautifulSoup(html, "html.parser")
    data = ListingData(raw_text="")

    # 1. Embedded JSON payload -- the reliable core fields.
    listing_json = _extract_embedded_listing_json(soup)
    if listing_json:
        std = listing_json.get("StandardFields") or {}
        data.address = listing_json.get("StreetAddress") or data.address
        data.city = listing_json.get("City")
        data.state = listing_json.get("StateOrProvince")
        data.postal_code = listing_json.get("PostalCode")
        price = (
            listing_json.get("ListPrice")
            or listing_json.get("CurrentPrice")
            or std.get("CurrentPrice")
        )
        if price:
            data.price = float(price)
        beds = listing_json.get("BedsTotal")
        baths = listing_json.get("BathsTotal")
        if beds not in (None, ""):
            data.beds = _to_num(beds)
        if baths not in (None, ""):
            data.baths = _to_num(baths)
        data.mls_id = listing_json.get("ListingId")
        data.mls_status = listing_json.get("MlsStatus") or std.get("StandardStatus")
        data.latitude = std.get("Latitude", listing_json.get("Latitude"))
        data.longitude = std.get("Longitude", listing_json.get("Longitude"))

    # 2. Labeled field/value pairs scattered across the "Listing Details" cards.
    fields = _extract_detail_fields(soup)
    data.extra_fields = fields

    data.year_built = _to_int(_first(fields, "Year Built"))
    data.days_on_market = _to_int(_first(fields, "DOM", "Days on Market"))
    data.sqft = _to_num(
        _first(fields, "Total Fin SqFt All Levels", "SqFt Above Grade", "Building Total SqFt")
    ) or data.sqft
    data.county = _first(fields, "County") or data.county
    data.municipality = _first(fields, "Municipality")
    data.lot_acres = _to_num(_first(fields, "Lot Acres"))
    data.taxable_value = _to_num(_first(fields, "Taxable Value"))
    data.sev = _to_num(_first(fields, "SEV"))
    data.homestead_pct = _to_num(_first(fields, "Homestead %"))
    data.tax_year = _to_int(_first(fields, "Tax Year", "For Tax Year"))
    data.hoa_yn = _first(fields, "Association YN", "HOA Y/N")
    data.zoning = _first(fields, "Zoning")
    data.property_sub_type = _first(fields, "Property Sub-Type", "Property Type")
    data.school_district = _first(fields, "School District")
    data.garage_spaces = _to_num(_first(fields, "Garage Spaces"))
    data.basement = _first(fields, "Basement")
    data.stories = _to_num(_first(fields, "Stories"))
    data.new_construction = _first(fields, "New Construction")
    data.waterfront = _first(fields, "Waterfront")
    data.public_remarks = _first(fields, "Public Remarks")

    # Beds/baths sometimes only appear in the detail cards (older templates).
    if data.beds is None:
        data.beds = _to_num(_first(fields, "Total Bedrooms", "Total Beds Above Grade"))
    if data.baths is None:
        data.baths = _to_num(_first(fields, "Total Baths"))
    if not data.price:
        data.price = _to_num(_first(fields, "Current Price", "List Price")) or 0.0

    # HOA dues, when the template states them.
    hoa = _to_num(_first(fields, "Association Fee", "HOA Fee", "Association Dues"))
    if hoa:
        period = (_first(fields, "Association Fee Frequency", "HOA Fee Period") or "").lower()
        if "year" in period or "annual" in period:
            hoa = hoa / 12.0
        elif "quarter" in period:
            hoa = hoa / 3.0
        data.hoa_monthly = hoa
    elif (data.hoa_yn or "").strip().lower() in ("no", "n", "false"):
        data.hoa_monthly = 0.0

    # Prefer the SELLER'S ACTUAL current tax bill over anything derived from
    # taxable value -- it is what's stated, not an estimate. (enrichment.py
    # then re-estimates the POST-SALE bill, which is the number that matters.)
    seller_tax = _to_num(
        _first(fields, "Seller's Annual Property Tax", "Sellers Annual Property Tax",
               "Annual Property Tax", "Taxes")
    )
    if seller_tax:
        data.property_tax_annual = seller_tax

    return _note_missing(data)


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------

def looks_like_html(blob: str) -> bool:
    head = blob.lstrip()[:2000].lower()
    return "<html" in head or "<!doctype html" in head or "<div" in head or "data-map--ldp-listing" in blob[:200000]


def parse_listing(blob: str) -> ListingData:
    """
    Parse either flexmls HTML or plain pasted text -- whichever was handed in.

    If the blob looks like HTML but the flexmls parser comes up empty (a
    different IDX template), fall back to a text parse of the rendered text so
    the caller still gets something usable.
    """
    if looks_like_html(blob):
        data = parse_flexmls_html(blob)
        if data.price:
            return data
        text = BeautifulSoup(blob, "html.parser").get_text("\n", strip=True)
        fallback = parse_pasted_text(text)
        # Keep whatever the structured parse *did* find.
        for key, value in data.to_dict().items():
            if value and not getattr(fallback, key, None):
                setattr(fallback, key, value)
        return _note_missing(fallback)
    return parse_pasted_text(blob)
