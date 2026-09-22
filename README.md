# Rental Analyzer

Screens single-family rental deals from an MLS listing page. Paste in one
listing after another and get a fast, consistent read: **look closer**, or
**pass**.

No network calls, no API keys, no environment variables, no LLM in the
arithmetic. One dependency: `beautifulsoup4`.

```bash
pip install -r requirements.txt
python3 gui.py                       # desktop app (recommended)
python3 main.py                      # command line, built-in sample listing
```

## The GUI

`python3 gui.py` (or `python3 main.py --gui`) opens a desktop window: load a
listing, every extracted field lands in an editable box, adjust any
assumption, and the report, projection and sensitivity grids update on
**Underwrite** (or just press Enter in any box).

- **Nothing is locked.** Every parsed field is editable, so you can correct a
  bad parse, underwrite your offer instead of the asking price, or type a
  deal in by hand with no listing at all.
- **The tax box says where its number came from** — *estimated from listing*
  in amber, or **VERIFIED (your figure)** in green once you type over it.
  There is a button that opens the state estimator right next to it.
- **Comparison tab** stacks deals as you screen them, one row each, colored
  by PASS/FLAG. Export it to CSV.
- **Export** the full report as text or JSON.

Tkinter ships with Python, so there is nothing extra to install on Windows or
with a python.org build. On Debian/Ubuntu: `sudo apt install python3-tk`.

### Building a double-clickable app

```bash
python -m pip install -r requirements.txt   # bs4 must be in THIS interpreter
python -m pip install pyinstaller
python -m PyInstaller rental_analyzer.spec
```

`dist/` then holds `RentalAnalyzer.exe` (Windows), `RentalAnalyzer.app`
(macOS) or a standalone binary (Linux) — no Python needed on the machine
that runs it.

**The one gotcha:** PyInstaller bundles only what the interpreter running it
can import. If `beautifulsoup4` is installed in a different Python than the
one invoking PyInstaller, the app builds fine and then dies on launch with
`No module named 'bs4'`. Run the `pip install -r requirements.txt` line above
with the *same* `python` you build with, and the spec's preflight check will
stop the build with instructions rather than shipping a broken app. If the
build log says `Hidden import 'soupsieve' not found`, that is the symptom.

## Using it from the command line

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
python3 main.py listing.html --tax 4884      # a verified tax figure beats any estimate
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
| Management / maintenance / capex reserve | 0% (self-managed) / 8% / 8% of effective gross income |
| Rent growth / expense growth / appreciation | 3% / 2.5% / 3% |

## Three things this gets right that a naive read does not

**1. The seller's tax bill is not your tax bill.** The listing states the
*seller's* annual property tax. In Michigan — and in every state with an
assessment cap or an owner-occupancy exemption — that number resets when the
property sells. Taxable value uncaps to the SEV, and a rental loses the
homestead exemption (~18 mills of school operating tax).

Because this number moves the deal more than any other expense, **tax is an
optional input**. Run the parcel through Michigan's official estimator —
<https://treas-secure.state.mi.us/ptestimator> — and pass the result in:

```bash
python3 main.py listing.html --tax 4884
```

It is then used verbatim, with no estimating at all. The report labels that
line **VERIFIED (provided)**, still prints the listing-derived estimate
alongside it for contrast, and warns if the two are more than ~35% apart —
that gap usually means a wrong homestead flag or a stale taxable value in
one of them.

Omit `--tax` and the original estimate chain runs unchanged: back the implied
millage out of the seller's bill, add the non-homestead adder, apply it to
the uncapped (SEV) value, and fall back to a percentage of price only when
the listing states no assessment data at all.

On the sample listing the estimate is **$2,184/yr stated → $5,245/yr
actual**, a $255/month swing that turns a marginal deal into a losing one.
The report always shows both numbers and underwrites the higher one.

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

Every threshold has a box in the GUI; the CLI exposes the first three as
`--min-*` flags and takes the rest from `report.Thresholds`.

**Breakeven occupancy is reported, not screened.** Vacancy is already an
input, deducted from gross rent before every metric is computed, so the deal
is judged at your assumed occupancy throughout. Screening breakeven
occupancy on top of that would flag one weakness twice. It is still printed
as a reference figure, because it answers something the other numbers do not
— how much vacancy the deal can absorb before cash flow turns negative. To
use it as a genuine stress test, set `Thresholds.max_breakeven_occupancy`
*tighter* than your assumed occupancy (e.g. 0.85 with a 1-month vacancy
assumption: "does this still work if vacancy doubles?").

Verdicts: **INVESTIGATE FURTHER** (clears everything) · **MARGINAL** (one or
two misses, DSCR intact) · **PASS ON IT**.

## Layout

| File | Role |
|---|---|
| `extraction.py` | flexmls/Spark HTML → `ListingData`. Reads the `data-map--ldp-listing` JSON blob for core fields, walks `.listing-detail-field-line` pairs for the rest. Plain-text fallback for other templates. |
| `enrichment.py` | Fills in what the listing omits: rent, insurance, and the post-transfer tax correction. Accepts a verified tax figure via `property_tax_annual=` / `--tax`. Every value carries a note. |
| `financial_engine.py` | All arithmetic. `PropertyInputs` → year-by-year projection, cap rate, CoC, DSCR, breakeven occupancy, IRR via self-contained Newton-Raphson (bisection fallback). No numpy. |
| `sensitivity.py` | Grids: rent growth × vacancy, interest rate, rent level. Deep-copies the base inputs per run. |
| `report.py` | Text report with PASS/FLAG markers and a fixed-width table renderer. |
| `gui.py` | Tkinter desktop app over the same pipeline — editable fields, live report, comparison table, CSV/JSON export. |
| `main.py` | CLI wiring: extract → enrich → finance → report → sensitivity. `--gui` launches the desktop app. |
| `rental_analyzer.spec` | PyInstaller recipe for a standalone double-clickable build. Preflights the parser, bundles bs4 via `collect_all`, and uses onedir on macOS / onefile elsewhere. |
| `sample_listing.py` | A realistic fabricated flexmls page so `python3 main.py` runs with no setup. |
| `tests/` | `python3 -m unittest discover tests` — 47 tests over loan math, IRR, the tax input and correction, grid isolation, and the GUI (widget tests skip automatically on a headless box). |

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

This is a screening model, not an appraisal. Rent and insurance are always
estimates, and so is tax unless you pass `--tax`. Verify rent against local
comps, and get a real tax figure from the
[state estimator](https://treas-secure.state.mi.us/ptestimator) or the county
assessor before making an offer.
