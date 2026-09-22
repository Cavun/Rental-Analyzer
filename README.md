# Rental Analyzer

Screens single-family rental deals from an MLS listing page. Paste in one
listing after another and get a fast, consistent read: **look closer**, or
**pass**.

No network calls, no API keys, no environment variables, no LLM in the
arithmetic. One dependency: `beautifulsoup4`.

```bash
pip install -r requirements.txt
python3 main.py                      # runs the built-in sample listing
```

## Using it on a real listing

flexmls disallows automated access in robots.txt, and the consumer portals
prohibit scraping in their terms. So this tool never fetches anything. You
open the listing yourself and hand it the page:

1. Open the listing in your browser.
2. Right-click → **View Page Source** (or **Inspect** → copy the outer HTML).
3. Save it as `listing.html`, or paste it straight in.

```bash
python3 main.py listing.html                 # full report + sensitivity grids
python3 main.py a.html b.html c.html         # batch screen: one row per property
cat listing.html | python3 main.py -         # stdin
python3 main.py --paste                      # paste, then Ctrl-D
python3 main.py listing.html --json          # machine-readable output
```

Common overrides:

```bash
python3 main.py listing.html --price 265000  # underwrite your offer, not the ask
python3 main.py listing.html --rent 1650     # a real rent comp beats the 1% rule
python3 main.py listing.html --rate 0.0665   # the rate you were actually quoted
python3 main.py listing.html --min-coc 0.06  # loosen the screen
```

## Standing assumptions

Fixed for every listing so deals stay comparable (all in
`financial_engine.py`):

| Assumption | Value |
|---|---|
| Down payment | 20% |
| Interest rate | 7.00% |
| Loan term | 30 years, fully amortizing |
| Closing costs | 3% of purchase price |
| Initial capex | $0 (turn-key) |
| Monthly rent | 1% of purchase price |
| Vacancy | 1 month/year (8.33%) |
| Hold period | Forever — projected over the full 30-year loan term |
| Management / maintenance / capex reserve | 8% / 8% / 8% of effective gross income |
| Rent growth / expense growth / appreciation | 3% / 2.5% / 3% |

## Three things this gets right that a naive read does not

**1. The seller's tax bill is not your tax bill.** The listing states the
*seller's* annual property tax. In Michigan — and in every state with an
assessment cap or an owner-occupancy exemption — that number resets when the
property sells. Taxable value uncaps to the SEV, and a rental loses the
homestead exemption (~18 mills of school operating tax). `enrichment.py`
backs out the implied millage from the seller's bill, adds the non-homestead
adder, and applies it to the uncapped value.

On the sample listing that is **$2,184/yr stated → $5,245/yr actual**, a
$255/month swing that turns a marginal deal into a losing one. The report
always shows both numbers and underwrites the higher one.

**2. A cash-flow-only IRR undersells a forever hold; an appreciation-only one
oversells it.** With no sale date, a pure cash-flow IRR ignores 30 years of
principal paydown. The report prints both: `IRR (w/ equity)` assumes a
hypothetical liquidation at year 30 purely so equity is represented, and
`IRR cash-flow-only` for the true never-sell case.

**3. The 1% rule is a screen, not a comp.** Rent at 1% of price is what
you asked for and it is what the model uses — but it is the single most
load-bearing assumption in the deal, so there is a dedicated rent-level
sensitivity grid and a breakeven-rent figure showing exactly where the deal
stops working. Override it with `--rent` the moment you have a real comp.

## Screening thresholds

Defaults (tunable via `report.Thresholds` or the `--min-*` flags):

| Metric | Bar |
|---|---|
| DSCR | ≥ 1.25 |
| Cap rate | ≥ 5% |
| Cash-on-cash | ≥ 8% |
| IRR (with equity) | ≥ 10% |
| Monthly cash flow | ≥ $0 |
| Breakeven occupancy | ≤ 90% |

Verdicts: **INVESTIGATE FURTHER** (clears everything) · **MARGINAL** (one or
two misses, DSCR intact) · **PASS ON IT**.

## Layout

| File | Role |
|---|---|
| `extraction.py` | flexmls/Spark HTML → `ListingData`. Reads the `data-map--ldp-listing` JSON blob for core fields, walks `.listing-detail-field-line` pairs for the rest. Plain-text fallback for other templates. |
| `enrichment.py` | Fills in what the listing omits: rent, insurance, and the post-transfer tax correction. Every value carries a note. |
| `financial_engine.py` | All arithmetic. `PropertyInputs` → year-by-year projection, cap rate, CoC, DSCR, breakeven occupancy, IRR via self-contained Newton-Raphson (bisection fallback). No numpy. |
| `sensitivity.py` | Grids: rent growth × vacancy, interest rate, rent level. Deep-copies the base inputs per run. |
| `report.py` | Text report with PASS/FLAG markers and a fixed-width table renderer. |
| `main.py` | CLI wiring: extract → enrich → finance → report → sensitivity. |
| `sample_listing.py` | A realistic fabricated flexmls page so `python3 main.py` runs with no setup. |
| `tests/` | `python3 -m unittest discover tests` — 30 tests over loan math, IRR, the tax correction, and grid isolation. |

## Extending it

- **Another MLS vendor**: write a `parse_<vendor>_html()` returning a
  `ListingData` and add it to `parse_listing()`. The class names and JSON
  shape in `parse_flexmls_html` are specific to the Spark Platform template.
- **Real data feeds**: if you get licensed access (Spark/RESO Web API,
  RentCast, ATTOM), write an adapter that returns `ListingData` via
  `from_structured_input()` — nothing downstream changes.
- **Per-market tuning**: `EnrichmentAssumptions` holds every estimate knob
  (insurance rate, millage adder, expense ratios) in one place.

## Caveats

This is a screening model, not an appraisal. Rent, insurance and
post-transfer taxes are estimates. Verify rent against local comps and taxes
with the county assessor before making an offer.
