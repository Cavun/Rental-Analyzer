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
- **Rent and property tax are required, and nothing fills them in for you.**
  Both boxes are cleared on every listing load, so last property's figures
  cannot follow you onto this one. A button opens the state tax estimator
  right next to the tax box. Underwrite refuses, with a message, until both
  are filled.
- **Year 1 is pessimistic on purpose** — it carries lease-up vacancy and the
  make-ready spend. The banner labels the headline metrics *Year 1 (incl.
  lease-up)* and prints a *Stabilized (year 2)* line beside them, so a deal
  that fails only on timing is distinguishable from one that fails on price.
- **An "After tax" tab** reports depreciation, passive losses and the tax due
  at sale. Reported, never mixed into the pre-tax numbers.
- **Comparison tab** stacks deals as you screen them, one row each, colored
  by PASS/FAIL. Export it to CSV.
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

### Or let CI build it for you

`.github/workflows/build.yml` does all of the above on every merge into
`main`: it runs the test suite, then builds the app on Windows, macOS and
Linux runners in parallel.

- **Every run** (including pull requests) attaches the three builds to the
  workflow run as artifacts, kept for 90 days — Actions tab → the run →
  *Artifacts*.
- **Merges into `main`** additionally publish a GitHub Release tagged
  `build-<run number>`, marked as the latest release, holding
  `RentalAnalyzer-windows.exe`, `RentalAnalyzer-macos.zip` and
  `RentalAnalyzer-linux`. That is the link to hand someone who just wants
  the app.

You can also trigger it by hand from the Actions tab (*Run workflow*). The
Linux job runs the packaged binary once as a smoke test and fails the build
if the bundle is missing a module — the `bs4` gotcha above, caught in CI
rather than by whoever downloads it.

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
# --rent and --tax are REQUIRED on every run.
python3 main.py listing.html --rent 1650 --tax 4884

python3 main.py listing.html --rent 1650 --tax 4884 --price 265000  # your offer, not the ask
python3 main.py listing.html --rent 1650 --tax 4884 --rate 0.0665   # the rate you were quoted
python3 main.py listing.html --rent 1650 --tax 4884 --lease-up 2    # slower lease-up
python3 main.py listing.html --rent 1650 --tax 4884 --after-tax     # add the tax layer
python3 main.py listing.html --rent 1650 --tax 4884 --min-coc 0.06  # loosen the screen
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
| Make-ready / initial capex | $2,500 or 1% of price, whichever is higher |
| Monthly rent | **Required input** — from comps, never derived |
| Property tax | **Required input** — from the state estimator, used verbatim |
| Vacancy | 1 month/year (8.33%) steady state |
| Lease-up | 1 month, year 1 only, on top of the vacancy rate |
| Exit cap rate | Year-1 cap rate + 0.50% |
| Hold period | Forever — projected over the full 30-year loan term |
| Management / maintenance / capex reserve | 0% (self-managed) / 8% / 8% of effective gross income |
| Rent growth / expense growth / appreciation | 3% / 2.5% / 3% |

## Four things this gets right that a naive read does not

**1. The two numbers that decide the deal are yours to supply.** Rent and
property tax move a deal further than anything else in the model, and both
used to be estimated. Neither is anymore.

*Rent* used to default to 1% of purchase price. That made the single most
load-bearing number in the deal a function of the seller's asking price:
raise the price and the model politely raised the rent to match. Pull rent
from actual comps and pass `--rent`.

*Tax* used to be estimated from the seller's bill, the taxable value, the
SEV and an assumed non-homestead millage. The correction was right in
principle — in Michigan, and in every state with an assessment cap or an
owner-occupancy exemption, the taxable value uncaps to the SEV on transfer
and a rental loses the homestead exemption (~18 mills of school operating
tax), so the seller's bill is never your bill. But the chain had four
places to be quietly wrong and the state's own estimator is a two-minute
lookup that is simply correct. Run the parcel through
<https://treas-secure.state.mi.us/ptestimator> and pass `--tax`.

```bash
python3 main.py listing.html --rent 1650 --tax 4884
```

Both are used verbatim. The seller's stated tax is still parsed and printed
for reference, and nothing computes with it. A non-blocking warning fires if
your tax figure falls outside ~0.5–4% of price, which catches a monthly
figure typed into an annual box or a stray zero.

**2. Year 1 is not the stabilized year, and pretending otherwise flatters
every deal.** You do not close on a property and collect rent the next
morning. Year 1 carries **lease-up months** (default 1) on top of the
steady-state vacancy rate, and a **make-ready** spend (default $2,500 or 1%
of price, whichever is higher) in total cash invested and the cap-rate
basis. Years 2+ are unaffected.

Make-ready and the optional **year-1 extra repairs** box are not the same
input and do not overlap. Make-ready is *capital* spent before the first
tenant: it leaves your pocket at closing, so it shows up in cash invested
and in the cap-rate basis and never touches NOI. Year-1 extra repairs is an
*operating* expense after move-in — a temporary bump on top of the
steady-state maintenance percentage that drops away in year 2, so it lowers
year-1 NOI, DSCR and cash flow but costs no additional cash at closing. It
defaults to 0, so nothing is double-counted unless you deliberately turn it
on.

The headline metrics — DSCR, cash-on-cash, monthly cash flow, cap rate —
are year 1, and the verdict screens on them. A **Stabilized (year 2)** line
prints beside them, so a deal that fails only because of lease-up reads
differently from one that fails permanently. Set `--lease-up 0 --capex 0`
to get the old, sunnier numbers back.

**3. Two ways to value the exit, and the deal is judged on the worse one.**
A cash-flow-only IRR truncated value at loan payoff and misstated a forever
hold, so it is gone. In its place are two independent terminal values:
appreciation-based (price compounded at your appreciation rate) and
cap-rate-based (year N+1 NOI over an exit cap, defaulting to the year-1 cap
rate + 0.50%). Both are reported net of selling costs and loan payoff, an
IRR is computed under each, and **the screen uses the lower one**. If the
two differ by more than ~25%, the report says so: that gap means your
appreciation and rent-growth assumptions disagree about what the building
is worth, and one of them is wrong.

Property value is **end of year** throughout: year *N* value is
price × (1 + g)^*N*, so year 1 earns a year of appreciation like every
other year. The IRR solver falls through to bisection when Newton runs out
of iterations rather than reporting "no IRR", finds its brackets by
scanning NPV instead of assuming the endpoints straddle a root, and flags
when the cash-flow series changes sign more than once — several rates can
solve NPV = 0 and the report says so rather than printing one as if it were
the answer.

**4. After-tax return is a fact about you, not about the building.** So it
is reported and never mixed into the pre-tax numbers, and it is not screened
unless you set a threshold yourself. `tax_engine.py` reads the finished
projection and models straight-line depreciation on the building share of
basis, taxable income as NOI less interest less depreciation (principal is
not a deduction), the $25,000 active-participation allowance with its
$100k–$150k MAGI phase-out, suspended losses carried forward and released at
sale, and the split at exit between unrecaptured §1250 gain at 25% and
long-term capital gain, plus state tax and NIIT.

It deliberately overstates deductions in two places, both noted in the
output: the capex reserve is treated as deductible when accrued (strictly,
capex is capitalized), and loan costs ride in the depreciable basis
(strictly, they amortize over the loan term). It also assumes a **taxable
sale** — a property you never sell gets a basis step-up at death that wipes
out the exit tax entirely, so the after-tax IRR is the pessimistic end of
the range for a genuine forever hold.

## Screening thresholds

Defaults (tunable via `report.Thresholds` or the `--min-*` flags):

| Metric | Bar |
|---|---|
| DSCR | ≥ 1.25 |
| Cap rate | ≥ 5% |
| Cash-on-cash | ≥ 8% |
| IRR (lower of the two exits) | ≥ 10% |
| Monthly cash flow | ≥ $0 |
| After-tax IRR | off by default |

All of these are **year 1, lease-up included**. Every threshold has a box in
the GUI; the CLI exposes the first three as `--min-*` flags and takes the
rest from `report.Thresholds`.

**After-tax IRR is reported, not screened,** unless you set
`Thresholds.min_after_tax_irr` or fill the GUI box. It depends on your
bracket and your other passive income, so it is a personal number rather
than a property one.

**Breakeven occupancy is reported, not screened.** Vacancy is already an
input, deducted from gross rent before every metric is computed, so the deal
is judged at your assumed occupancy throughout. Screening breakeven
occupancy on top of that would flag one weakness twice. It is still printed
as a reference figure, because it answers something the other numbers do not
— how much vacancy the deal can absorb before cash flow turns negative. To
use it as a genuine stress test, set `Thresholds.max_breakeven_occupancy`
*tighter* than your assumed occupancy (e.g. 0.85 with a 1-month vacancy
assumption: "does this still work if vacancy doubles?").

Verdicts: **PASS** (clears every threshold — worth a closer look) ·
**MARGINAL** (one or two misses, DSCR intact) · **FAIL** (anything worse, or
any DSCR miss). PASS means the listing cleared the screen; a listing that
misses thresholds reads FAIL, never "pass".

## Layout

| File | Role |
|---|---|
| `extraction.py` | flexmls/Spark HTML → `ListingData`. Reads the `data-map--ldp-listing` JSON blob for core fields, walks `.listing-detail-field-line` pairs for the rest. Plain-text fallback for other templates. |
| `enrichment.py` | Takes your required rent and tax verbatim, estimates insurance, and collects the notes and warnings (HOA, pre-1960 stock, MLS status, zoning, tax sanity). Computes no deal figure you did not supply. |
| `financial_engine.py` | All arithmetic. `PropertyInputs` → year-by-year projection, cap rate, CoC, DSCR, breakeven occupancy, both exit IRRs via self-contained Newton-Raphson with a scanning bisection fallback. No numpy. |
| `tax_engine.py` | The after-tax layer. Reads a finished `UnderwritingResult` and never changes a pre-tax number: depreciation, passive-loss carryforward, recapture and capital gains at sale, after-tax IRRs. |
| `sensitivity.py` | Grids: rent growth × vacancy, interest rate, and rent level centred on **your** entered rent (−20% to +5%, labelled in dollars). Deep-copies the base inputs per run. |
| `report.py` | Text report with PASS/FAIL markers and a fixed-width table renderer. |
| `gui.py` | Tkinter desktop app over the same pipeline — editable fields, live report, comparison table, CSV/JSON export. |
| `main.py` | CLI wiring: extract → enrich → finance → report → sensitivity. `--gui` launches the desktop app. |
| `rental_analyzer.spec` | PyInstaller recipe for a standalone double-clickable build. Preflights the parser, bundles bs4 via `collect_all`, and uses onedir on macOS / onefile elsewhere. |
| `.github/workflows/build.yml` | CI: tests every push and pull request, then packages the app on Windows, macOS and Linux. Merges into `main` publish a GitHub Release with all three builds. |
| `sample_listing.py` | A realistic fabricated flexmls page so `python3 main.py` runs with no setup. |
| `tests/` | `python3 -m unittest discover tests` — 160 tests over loan math, the IRR solver, the required rent and tax inputs, year-1 pessimism, end-of-year appreciation, both exits, the after-tax layer, grid isolation, and the GUI (widget tests skip automatically on a headless box). |

## Extending it

- **Another MLS vendor**: write a `parse_<vendor>_html()` returning a
  `ListingData` and add it to `parse_listing()`. The class names and JSON
  shape in `parse_flexmls_html` are specific to the Spark Platform template.
- **Real data feeds**: if you get licensed access (Spark/RESO Web API,
  RentCast, ATTOM), write an adapter that returns `ListingData` via
  `from_structured_input()` — nothing downstream changes.
- **Per-market tuning**: `EnrichmentAssumptions` holds the remaining estimate
  knobs (insurance rate and surcharge, expense ratios) in one place;
  `TaxAssumptions` holds the whole after-tax position.

## Caveats

This is a screening model, not an appraisal. Rent and property tax are your
figures and nothing here second-guesses them — which means a bad comp or a
mistyped tax bill propagates straight through to the verdict. Insurance,
growth rates, the exit cap and every tax rate are assumptions. Confirm rent
against current comps and tax with the
[state estimator](https://treas-secure.state.mi.us/ptestimator) or the county
assessor before making an offer, and take the after-tax output to someone who
signs returns.
