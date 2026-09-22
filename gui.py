#!/usr/bin/env python3
"""
gui.py -- desktop front end for the rental underwriting model.

Tkinter only, so it ships with Python and adds no runtime dependency beyond
the beautifulsoup4 the parser already needs. Everything it computes runs
through the same extraction / enrichment / financial_engine / sensitivity
modules the CLI uses -- this is a window onto that pipeline, not a second
implementation of it.

    python3 gui.py

Workflow:
    1. Load or paste a listing page, hit Parse.
    2. Every extracted field lands in an editable box. Fix anything the
       parser got wrong, or type a deal in by hand with no listing at all.
    3. Fill in MONTHLY RENT (from comps) and ANNUAL PROPERTY TAX (from the
       state estimator). Nothing fills these in for you, and both are
       cleared on every new listing -- a rent or tax carried over from the
       last property is an underwrite of a deal that does not exist.
    4. Adjust assumptions, hit Underwrite (or just press Enter in any box).
    5. Add the deal to the comparison tab and move on to the next listing.

Year 1 is deliberately pessimistic: it carries lease-up vacancy and the
make-ready spend. The stabilized (year 2) figures sit beside the headline
ones so a deal that fails only on timing is distinguishable from one that
fails on price.
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from enrichment import EnrichmentAssumptions, enrich, estimate_insurance
from extraction import BS4_AVAILABLE, ListingData, ParserUnavailableError, parse_listing
from financial_engine import (
    OperatingExpenses,
    PropertyInputs,
    UnderwritingResult,
    default_make_ready,
    underwrite,
)
from report import (
    BATCH_HEADERS,
    Thresholds,
    cash_flow_gap,
    format_after_tax,
    format_report,
    money,
    one_line_summary,
    render_grid,
    render_table,
    screen,
    verdict,
)
from sample_listing import SAMPLE_LISTING_HTML
from sensitivity import (
    breakeven_rent,
    interest_rate_grid,
    rent_growth_vs_vacancy,
    rent_level_grid,
)
from tax_engine import TaxAssumptions, after_tax as compute_after_tax

TAX_ESTIMATOR_URL = EnrichmentAssumptions().tax_estimator_url
MONO = ("Courier New", 10) if sys.platform == "win32" else ("Menlo" if sys.platform == "darwin" else "DejaVu Sans Mono", 10)

OK_COLOR = "#1b7f3b"
WARN_COLOR = "#b06000"
BAD_COLOR = "#a11414"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def parse_number(text: str, default: Optional[float] = None) -> Optional[float]:
    """Tolerant numeric parse: '$285,000', '7.0%', ' 1,392 ' all work."""
    if text is None:
        return default
    cleaned = str(text).replace(",", "").replace("$", "").replace("%", "").strip()
    if not cleaned:
        return default
    try:
        return float(cleaned)
    except ValueError:
        return default


class LabeledEntry:
    """One label + entry cell in a grid, with tolerant get/set."""

    def __init__(self, parent: tk.Widget, row: int, col: int, label: str,
                 width: int = 12, suffix: str = "", tooltip: str = "", wide: bool = False,
                 toggle: Optional[bool] = None):
        self.var = tk.StringVar()
        # With toggle=, the label IS a checkbox: it says whether this number
        # is enforced at all. The box greys out when it is off, so an
        # unscreened bar can never be mistaken for one that is being applied.
        self.enabled: Optional[tk.BooleanVar] = None
        if toggle is None:
            label_widget = ttk.Label(parent, text=label)
        else:
            self.enabled = tk.BooleanVar(value=toggle)
            label_widget = ttk.Checkbutton(parent, text=label, variable=self.enabled,
                                           command=self._sync_state)
        label_widget.grid(row=row, column=col * 3, sticky="w", padx=(6, 4), pady=2)
        justify = "left" if wide else "right"
        self.entry = ttk.Entry(parent, textvariable=self.var, width=width, justify=justify)
        if wide:
            # Span the remaining columns of the row instead of a single cell.
            self.entry.grid(row=row, column=col * 3 + 1, columnspan=5, sticky="we",
                            pady=2, padx=(0, 8))
            return
        self.entry.grid(row=row, column=col * 3 + 1, sticky="we", pady=2)
        if suffix:
            ttk.Label(parent, text=suffix).grid(row=row, column=col * 3 + 2, sticky="w", padx=(2, 8))
        else:
            ttk.Label(parent, text=" ").grid(row=row, column=col * 3 + 2, padx=(2, 8))
        if tooltip:
            Tooltip(self.entry, tooltip)
            Tooltip(label_widget, tooltip)
        self._sync_state()

    def _sync_state(self) -> None:
        """Grey the entry while its switch is off."""
        if self.enabled is None:
            return
        self.entry.configure(state="normal" if self.enabled.get() else "disabled")

    def is_on(self) -> bool:
        """True when this field is switched on (always, if it has no switch)."""
        return True if self.enabled is None else bool(self.enabled.get())

    def set_on(self, on: bool) -> None:
        if self.enabled is not None:
            self.enabled.set(bool(on))
            self._sync_state()

    def get(self, default: Optional[float] = None) -> Optional[float]:
        return parse_number(self.var.get(), default)

    def get_text(self) -> str:
        return self.var.get().strip()

    def set(self, value, decimals: int = 2) -> None:
        if value is None or value == "":
            self.var.set("")
        elif isinstance(value, str):
            self.var.set(value)
        elif float(value) == int(float(value)) and decimals == 0:
            self.var.set(f"{int(value):,}")
        else:
            self.var.set(f"{float(value):,.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{value:,.0f}")


class Tooltip:
    """Minimal hover tooltip -- no dependency, no theme assumptions."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget, self.text, self.window = widget, text, None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None) -> None:
        if self.window or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, justify="left", background="#ffffe0",
                 relief="solid", borderwidth=1, wraplength=380, font=("TkDefaultFont", 9),
                 padx=6, pady=3).pack()

    def _hide(self, _event=None) -> None:
        if self.window:
            self.window.destroy()
            self.window = None


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------

class RentalAnalyzerGUI(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=6)
        self.master = master
        master.title("Rental Analyzer -- single-family underwriting")
        master.geometry("1360x900")
        master.minsize(1100, 720)
        self.pack(fill="both", expand=True)

        self.listing = ListingData()
        self.result: Optional[UnderwritingResult] = None
        self.enriched = None
        self.comparison: List[Tuple[str, List[str]]] = []
        self.after_tax = None
        # Insurance is the one figure still auto-filled; tracking the last
        # auto value lets a listing reload refresh it without stomping on a
        # number the user typed. Rent and tax are never auto-filled.
        self._auto_insurance: Optional[float] = None
        self.status = tk.StringVar(
            value="Load a listing, or type a price, rent and tax, then hit Underwrite.")

        self._build_toolbar()
        panes = ttk.PanedWindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, pady=(6, 0))
        left = ttk.Frame(panes)
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)
        self._build_inputs(left)
        self._build_output(right)
        self._build_statusbar()

        if not BS4_AVAILABLE:
            self.status.set("HTML parsing unavailable (beautifulsoup4 missing from this "
                            "build) -- plain text and manual entry still work.")
            self.after(400, self._warn_missing_parser)

        master.bind("<Return>", lambda _e: self.underwrite())
        master.bind("<Control-o>", lambda _e: self.open_file())
        master.bind("<Control-s>", lambda _e: self.export_report())

    # --- Chrome ---------------------------------------------------------

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Open listing HTML…", command=self.open_file).pack(side="left", padx=2)
        ttk.Button(bar, text="Paste listing…", command=self.paste_dialog).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Underwrite", command=self.underwrite).pack(side="left", padx=2)
        ttk.Button(bar, text="Add to comparison", command=self.add_to_comparison).pack(side="left", padx=2)
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="Export report…", command=self.export_report).pack(side="left", padx=2)
        ttk.Button(bar, text="Export JSON…", command=self.export_json).pack(side="left", padx=2)
        ttk.Button(bar, text="Reset", command=self.reset_fields).pack(side="left", padx=2)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(4, 0))
        ttk.Separator(bar, orient="horizontal").pack(fill="x", pady=(0, 3))
        ttk.Label(bar, textvariable=self.status, foreground="#444").pack(side="left", padx=4)

    # --- Input panel ----------------------------------------------------

    def _section(self, parent: tk.Widget, title: str) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=title, padding=(2, 4))
        frame.pack(fill="x", pady=4)
        for col in (1, 4):
            frame.columnconfigure(col, weight=1)
        return frame

    @staticmethod
    def _bind_mousewheel(canvas: tk.Canvas, *extra: tk.Widget) -> None:
        """
        Wheel scrolling over the input panel.

        Tk delivers a wheel event to the widget under the pointer, which in a
        scrolled canvas is almost always an entry or a label inside it, never
        the canvas -- so binding the canvas alone scrolls nothing. Bind once at
        the application level and scroll only when the pointer is over this
        canvas or one of its children; every other widget (the report panes,
        the comparison tree) keeps its own wheel behaviour.
        """
        owners = (str(canvas),) + tuple(str(w) for w in extra)

        def _scroll(event):
            path = str(event.widget)
            if not any(path == own or path.startswith(own + ".") for own in owners):
                return None
            if canvas.yview() == (0.0, 1.0):        # nothing to scroll
                return "break"
            if event.num == 4:                      # X11 wheel up
                delta = -1
            elif event.num == 5:                    # X11 wheel down
                delta = 1
            elif abs(event.delta) >= 120:           # Windows: multiples of 120
                delta = -int(event.delta / 120)
            else:                                   # macOS: small raw deltas
                delta = -1 if event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")
            return "break"

        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(sequence, _scroll, add="+")

    def _build_inputs(self, parent: tk.Widget) -> None:
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0, width=560)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        holder = ttk.Frame(canvas)
        holder.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        window = canvas.create_window((0, 0), window=holder, anchor="nw")
        # Keep the inner frame as wide as the canvas so the sections fill the
        # panel instead of hugging their natural width.
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._bind_mousewheel(canvas, scroll)

        # --- Property -------------------------------------------------
        prop = self._section(holder, "Property (edit anything the parser got wrong)")
        self.f_address = LabeledEntry(prop, 0, 0, "Address", width=46, wide=True)
        self.f_price = LabeledEntry(prop, 1, 0, "Purchase price", suffix="$",
                                    tooltip="Underwrite your OFFER here, not the asking price.")
        self.f_beds = LabeledEntry(prop, 1, 1, "Beds")
        self.f_baths = LabeledEntry(prop, 2, 0, "Baths")
        self.f_sqft = LabeledEntry(prop, 2, 1, "Sq ft")
        self.f_year = LabeledEntry(prop, 3, 0, "Year built")
        self.f_county = LabeledEntry(prop, 3, 1, "County")

        # --- Tax ------------------------------------------------------
        # One box. There is no estimator chain anymore: every path through it
        # had somewhere to be quietly wrong, and the state's own estimator is
        # a two-minute lookup that is simply correct.
        tax = self._section(holder, "Property tax")
        self.f_tax = LabeledEntry(
            tax, 0, 0, "Annual property tax (required)", width=14, suffix="$",
            tooltip="What a RENTAL buyer will pay, not the seller's capped, usually "
                    "homestead-exempt bill. Run the parcel through the state estimator "
                    "(button below) and type the figure here. Used verbatim. Cleared "
                    "on every new listing so the last property's tax cannot follow you.")
        button_row = ttk.Frame(tax)
        button_row.grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 2))
        ttk.Button(button_row, text="Open MI tax estimator",
                   command=lambda: webbrowser.open(TAX_ESTIMATOR_URL)).pack(side="left", padx=4)
        ttk.Label(button_row, text="Seller's bill on the listing is reference only.",
                  foreground="#666").pack(side="left", padx=8)

        # --- Income ---------------------------------------------------
        income = self._section(holder, "Income")
        self.f_rent = LabeledEntry(
            income, 0, 0, "Monthly rent (required, from comps)", width=14, suffix="$",
            tooltip="From actual local rent comps. Nothing auto-fills this: deriving "
                    "rent from the asking price makes the model agree with whatever "
                    "the seller asks, which is the opposite of useful. Cleared on "
                    "every new listing.")
        self.f_vacancy = LabeledEntry(income, 1, 0, "Vacancy", suffix="%",
                                      tooltip="8.33% = one vacant month per year, steady state.")
        self.f_rent_growth = LabeledEntry(income, 1, 1, "Rent growth", suffix="%/yr")
        self.f_lease_up = LabeledEntry(
            income, 2, 0, "Lease-up", suffix="months",
            tooltip="YEAR 1 ONLY: months between closing and a paying tenant, on top "
                    "of the steady-state vacancy rate. Years 2+ are unaffected. "
                    "0-11, and total year-1 vacancy must stay under 12 months.")

        # --- Financing ------------------------------------------------
        fin = self._section(holder, "Financing")
        self.f_down = LabeledEntry(fin, 0, 0, "Down payment", suffix="%")
        self.f_rate = LabeledEntry(fin, 0, 1, "Interest rate", suffix="%")
        self.f_term = LabeledEntry(fin, 1, 0, "Loan term", suffix="yrs")
        self.f_closing = LabeledEntry(fin, 1, 1, "Closing costs", suffix="%")
        self.f_capex0 = LabeledEntry(
            fin, 2, 0, "Initial make-ready / rehab", width=14, suffix="$",
            tooltip="CAPITAL, not an expense: one-time dollars spent BEFORE the first "
                    "tenant -- locks, paint, cleaning, the one appliance that died. It "
                    "comes out of your pocket at closing, so it lands in total cash "
                    "invested and in the cap-rate basis; it never touches NOI. "
                    "Defaults to $2,500 or 1% of price, whichever is HIGHER; set it to "
                    "0 only for a genuinely turn-key unit. Not the same box as 'Yr-1 "
                    "extra repairs' under Operating expenses, which is the running "
                    "repair load AFTER move-in.")
        self.f_hold = LabeledEntry(fin, 2, 1, "Projection", suffix="yrs",
                                   tooltip="A forever hold is modeled over the full loan term.")
        self.f_exit_cap = LabeledEntry(
            fin, 3, 0, "Exit cap rate", suffix="%",
            tooltip="Used for the cap-rate terminal value: year N+1 NOI divided by this "
                    "rate. Blank defaults to the year-1 cap rate + 0.50%, since the "
                    "building is older at exit than it is today.")

        # --- Operating expenses ---------------------------------------
        ops = self._section(holder, "Operating expenses")
        self.f_insurance = LabeledEntry(ops, 0, 0, "Insurance", suffix="$/yr")
        self.f_hoa = LabeledEntry(ops, 0, 1, "HOA", suffix="$/mo")
        self.f_mgmt = LabeledEntry(ops, 1, 0, "Management", suffix="% EGI")
        self.f_maint = LabeledEntry(ops, 1, 1, "Maintenance", suffix="% EGI")
        self.f_capex = LabeledEntry(ops, 2, 0, "Capex reserve", suffix="% EGI")
        self.f_repair_bump = LabeledEntry(
            ops, 3, 0, "Yr-1 extra repairs", suffix="% EGI",
            tooltip="OPERATING EXPENSE, year 1 only: maintenance runs hotter than the "
                    "steady-state % above while a new owner works through the punch "
                    "list the tenant finds after move-in. Added on top of Maintenance "
                    "for year 1, then drops away. 0 by default -- leave it at 0 unless "
                    "you want a year-1 repair penalty ON TOP of the one-time make-ready "
                    "under Financing, which covers the pre-tenant work.")
        self.f_other = LabeledEntry(
            ops, 2, 1, "Other fixed", suffix="$/yr",
            tooltip="Landlord-paid utilities, lawn/snow, rental certification. $0 by "
                    "default because on a single-family rental these are normally "
                    "tenant-paid or nominal -- put a figure here when a particular "
                    "property needs one.")
        self.f_exp_growth = LabeledEntry(ops, 3, 0, "Expense growth", suffix="%/yr")
        self.f_appreciation = LabeledEntry(ops, 3, 1, "Appreciation", suffix="%/yr")

        # --- Income tax -----------------------------------------------
        # Reported, never mixed into the pre-tax numbers: after-tax return is
        # a fact about you, not about the building.
        inc_tax = self._section(holder, "Income tax (reported, not screened)")
        self.f_building_share = LabeledEntry(
            inc_tax, 0, 0, "Building share", suffix="%",
            tooltip="Land is not depreciable. 80% is a common default; the county "
                    "assessor's land/improvement split is better.")
        self.f_fed_rate = LabeledEntry(inc_tax, 0, 1, "Federal marginal", suffix="%")
        self.f_state_rate = LabeledEntry(inc_tax, 1, 0, "State marginal", suffix="%",
                                         tooltip="Michigan is 4.25%.")
        self.f_city_rate = LabeledEntry(
            inc_tax, 1, 1, "City income tax", suffix="%",
            tooltip="Add it where one applies. Grand Rapids levies a city income tax, "
                    "and it reaches a non-resident's rental income from property in "
                    "the city.")
        self.f_ltcg_rate = LabeledEntry(inc_tax, 2, 0, "Capital gains", suffix="%")
        self.f_recapture_rate = LabeledEntry(
            inc_tax, 2, 1, "Depr. recapture", suffix="%",
            tooltip="Unrecaptured section 1250 gain, 25% federal maximum.")
        self.f_magi = LabeledEntry(
            inc_tax, 3, 0, "MAGI", suffix="$",
            tooltip="Used only to phase out the $25,000 active-participation "
                    "allowance: it drops $0.50 per $1 over $100,000 and is gone at "
                    "$150,000. Blank means no phase-out.")
        self.v_niit = tk.BooleanVar(value=False)
        self.v_passive_usable = tk.BooleanVar(value=True)
        niit_box = ttk.Checkbutton(inc_tax, text="NIIT (3.8%)", variable=self.v_niit)
        niit_box.grid(row=3, column=3, columnspan=3, sticky="w", padx=6)
        Tooltip(niit_box, "Net investment income tax on the gain at sale.")
        passive_box = ttk.Checkbutton(inc_tax, text="Passive losses usable now",
                                      variable=self.v_passive_usable)
        passive_box.grid(row=4, column=0, columnspan=6, sticky="w", padx=6, pady=(2, 4))
        Tooltip(passive_box,
                "On: the $25,000 active-participation allowance applies, so a paper "
                "loss offsets other income this year. Off: losses suspend and carry "
                "forward, offsetting future passive income first and releasing in "
                "full at sale.")

        # --- Thresholds -----------------------------------------------
        # Every bar has a tick box. Ticked = enforced; unticked = the metric is
        # still computed and reported, it just cannot fail the deal. Only the
        # two cash tests start ticked (see report.Thresholds).
        thr = self._section(holder, "Screening thresholds (tick the ones to enforce)")
        self.f_min_dscr = LabeledEntry(
            thr, 0, 0, "Min DSCR", toggle=False,
            tooltip="NOI / debt service in year 1. Off by default: it is the lender's "
                    "test, and how much it matters depends on your financing.")
        self.f_min_cap = LabeledEntry(
            thr, 0, 1, "Min cap rate", suffix="%", toggle=False,
            tooltip="Off by default: a cap rate compares this deal to the market, it "
                    "does not tell you whether YOU can carry it.")
        self.f_min_coc = LabeledEntry(
            thr, 1, 0, "Min cash-on-cash", suffix="%", toggle=True,
            tooltip="On by default. Year-1 cash flow over cash invested -- what your "
                    "money actually earns in year one.")
        self.f_min_irr = LabeledEntry(
            thr, 1, 1, "Min IRR", suffix="%", toggle=False,
            tooltip="Off by default: IRR leans on an exit you have not made yet, at "
                    "assumptions (appreciation, exit cap) you typed in.")
        self.f_min_cf = LabeledEntry(
            thr, 2, 0, "Min monthly CF", suffix="$", toggle=True,
            tooltip="On by default. Year-1 cash flow after debt service, lease-up "
                    "included. 0 means the deal may not cost you money every month. "
                    "Set it negative to allow a deal you are willing to feed through "
                    "its first year.")
        self.f_min_at_irr = LabeledEntry(
            thr, 2, 1, "Min after-tax IRR", suffix="%", toggle=False,
            tooltip="Off by default. After-tax return depends on your bracket and "
                    "your other passive income, so it is a personal number, not a "
                    "property number. Tick it to screen on it anyway.")
        self.f_max_be_occ = LabeledEntry(
            thr, 3, 0, "Max breakeven occ", suffix="%", toggle=False,
            tooltip="Off by default, and not an independent test: breakeven occupancy "
                    "<= assumed occupancy is the same condition as cash flow >= 0, so "
                    "ticking it double-counts one failure. The honest use is as a "
                    "STRESS test -- 85% with a 1-month vacancy assumption means 'must "
                    "still cash flow if vacancy doubles'.")
        self.f_cf_wiggle = LabeledEntry(
            thr, 3, 1, "CF wiggle room", suffix="%",
            tooltip="Wiggle room on the monthly cash-flow test only. A deal that "
                    "misses the cash-flow bar by less than this still fails, but the "
                    "report says CLOSE and lists the rent, price and expense moves "
                    "that would close the gap. Measured against the bar; when the bar "
                    "is $0 it falls back to this share of gross rent. 0 turns it off.")

        self.reset_fields(keep_listing=False)

    # --- Output panel ---------------------------------------------------

    def _build_output(self, parent: tk.Widget) -> None:
        banner = ttk.Frame(parent, padding=(4, 2))
        banner.pack(fill="x")
        self.verdict_label = tk.Label(banner, text="No deal underwritten yet",
                                      font=("TkDefaultFont", 13, "bold"), anchor="w",
                                      justify="left", wraplength=780)
        self.verdict_label.pack(fill="x")
        def _rewrap(event) -> None:
            width = max(400, event.width - 20)
            self.verdict_label.configure(wraplength=width)
            self.metrics_label.configure(wraplength=width)

        banner.bind("<Configure>", _rewrap)
        self.metrics_label = tk.Label(banner, text="", font=MONO, anchor="w", justify="left",
                                      wraplength=780)
        self.metrics_label.pack(fill="x", pady=(2, 4))

        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(fill="both", expand=True)
        self.txt_report = self._text_tab("Report")
        self.txt_projection = self._text_tab("Projection")
        self.txt_sensitivity = self._text_tab("Sensitivity")
        self.txt_after_tax = self._text_tab("After tax")
        self._build_comparison_tab()

    def _text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text=title)
        text = tk.Text(frame, wrap="none", font=MONO, borderwidth=0, padx=8, pady=6)
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set, state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="we")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text.tag_configure("pass", foreground=OK_COLOR)
        text.tag_configure("fail", foreground=BAD_COLOR)
        return text

    def _build_comparison_tab(self) -> None:
        frame = ttk.Frame(self.tabs)
        self.tabs.add(frame, text="Comparison")
        self.tree = ttk.Treeview(frame, columns=BATCH_HEADERS, show="headings", height=18)
        for header in BATCH_HEADERS:
            self.tree.heading(header, text=header)
            width = 210 if header == "Property" else 72
            self.tree.column(header, width=width, minwidth=54, stretch=(header == "Property"),
                             anchor="w" if header == "Property" else "e")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="we")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.tree.tag_configure("pass", foreground=OK_COLOR)
        self.tree.tag_configure("fail", foreground=BAD_COLOR)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Button(buttons, text="Remove selected", command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(buttons, text="Clear all", command=self.clear_comparison).pack(side="left", padx=4)
        ttk.Button(buttons, text="Export table…", command=self.export_comparison).pack(side="left", padx=4)
        ttk.Label(buttons, text="Underwrite a listing, then 'Add to comparison' to stack them up.",
                  foreground="#666").pack(side="left", padx=12)

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        # Colour the PASS/FAIL markers so the eye lands on them first.
        for marker, tag in (("PASS", "pass"), ("FAIL", "fail")):
            start = "1.0"
            while True:
                pos = widget.search(marker, start, stopindex="end")
                if not pos:
                    break
                end = f"{pos}+{len(marker)}c"
                widget.tag_add(tag, pos, end)
                start = end
        widget.configure(state="disabled")

    def _warn_missing_parser(self) -> None:
        from extraction import MISSING_BS4_MESSAGE
        messagebox.showwarning("HTML parser missing from this build", MISSING_BS4_MESSAGE)

    # --- Loading listings -----------------------------------------------

    def open_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Open a saved listing page",
            filetypes=[("Listing page", "*.html *.htm *.txt"), ("All files", "*.*")],
        )
        if path:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                self.load_blob(fh.read(), source=os.path.basename(path))

    def load_sample(self) -> None:
        self.load_blob(SAMPLE_LISTING_HTML, source="built-in sample")

    def paste_dialog(self) -> None:
        window = tk.Toplevel(self.master)
        window.title("Paste listing HTML or text")
        window.geometry("820x560")
        ttk.Label(window, padding=6, text=(
            "Open the listing in your browser, View Page Source (or Inspect → copy outer HTML), "
            "paste it here, then Parse. Plain copied listing text works too.")).pack(fill="x")
        text = tk.Text(window, wrap="word", font=MONO)
        text.pack(fill="both", expand=True, padx=6, pady=4)
        text.focus_set()

        def do_parse() -> None:
            blob = text.get("1.0", "end").strip()
            window.destroy()
            if blob:
                self.load_blob(blob, source="pasted text")

        row = ttk.Frame(window)
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="Parse", command=do_parse).pack(side="right", padx=6)
        ttk.Button(row, text="Cancel", command=window.destroy).pack(side="right")

    def load_blob(self, blob: str, source: str = "") -> None:
        try:
            listing = parse_listing(blob)
        except ParserUnavailableError as exc:
            messagebox.showerror("HTML parser missing from this build", str(exc))
            self.status.set("HTML parsing unavailable -- paste plain listing text, "
                            "or type the numbers in by hand.")
            return
        except Exception as exc:
            messagebox.showerror("Could not parse that page", str(exc))
            return
        if not listing.price:
            messagebox.showwarning(
                "No price found",
                "Parsed the page but found no price. Enter the fields by hand, "
                "or try a different listing page.",
            )
        self.listing = listing
        self.populate_from_listing(listing)
        found = sum(1 for v in (listing.price, listing.beds, listing.sqft,
                                listing.year_built, listing.property_tax_annual) if v)
        self.status.set(f"Loaded {source or 'listing'}: {listing.full_address or 'unknown address'} "
                        f"({found}/5 key fields found). Review the boxes, then Underwrite.")
        self.underwrite()

    def populate_from_listing(self, listing: ListingData) -> None:
        self.f_address.set(listing.full_address or listing.address)
        self.f_price.set(listing.price or "", decimals=0)
        self.f_beds.set(listing.beds if listing.beds is not None else "")
        self.f_baths.set(listing.baths if listing.baths is not None else "")
        self.f_sqft.set(listing.sqft if listing.sqft is not None else "", decimals=0)
        self.f_year.set(str(listing.year_built) if listing.year_built else "")
        self.f_county.set(listing.county or "")
        self.f_hoa.set(listing.hoa_monthly if listing.hoa_monthly is not None else 0)

        # Rent and tax are per-property figures the user supplies. Clearing
        # them on every load is the whole point: a stale rent or tax carried
        # over from the last listing is an underwrite of a property that does
        # not exist, and it looks exactly like a real one.
        self.f_rent.var.set("")
        self.f_tax.var.set("")

        if listing.price:
            # Insurance is still estimated, and this is now the only place it
            # is auto-filled. It used to live inside reestimate_tax(), so
            # removing the estimator would have silently stopped it.
            estimate = estimate_insurance(listing, EnrichmentAssumptions())
            typed = self.f_insurance.get()
            if typed in (None, 0.0) or (self._auto_insurance is not None
                                        and typed == self._auto_insurance):
                self._auto_insurance = estimate
                self.f_insurance.set(estimate, decimals=0)
            # Make-ready scales with price, so reseed it unless the user has
            # moved it off the default for the previous price.
            self.f_capex0.set(default_make_ready(listing.price), decimals=0)

    # --- Reading the form back ------------------------------------------

    def listing_from_fields(self) -> ListingData:
        """Rebuild a ListingData from the boxes, so user edits drive the model."""
        base = self.listing
        typed = self.f_address.get_text()
        # The address box shows the full address, so only carry the parsed
        # city/state/zip when the box still matches what the parser produced.
        keep_locality = typed in ("", base.address, base.full_address)
        # Unchanged box holds the FULL address; store just the street part so
        # full_address does not append the city/state/zip a second time.
        street = base.address if keep_locality else typed
        return ListingData(
            address=street or typed or base.address,
            city=base.city if keep_locality else None,
            state=base.state if keep_locality else None,
            postal_code=base.postal_code if keep_locality else None,
            price=self.f_price.get(0.0) or 0.0,
            beds=self.f_beds.get(), baths=self.f_baths.get(), sqft=self.f_sqft.get(),
            year_built=int(self.f_year.get(0) or 0) or None,
            # The seller's bill, taxable value, SEV and homestead status are
            # carried through from the parse for the report's reference line
            # only -- nothing computes with them anymore.
            property_tax_annual=base.property_tax_annual,
            hoa_monthly=self.f_hoa.get(0.0),
            days_on_market=base.days_on_market,
            mls_id=base.mls_id, mls_status=base.mls_status,
            county=self.f_county.get_text() or base.county,
            municipality=base.municipality, lot_acres=base.lot_acres,
            taxable_value=base.taxable_value, sev=base.sev,
            homestead_pct=base.homestead_pct, tax_year=base.tax_year,
            hoa_yn=base.hoa_yn, zoning=base.zoning,
            property_sub_type=base.property_sub_type, school_district=base.school_district,
            garage_spaces=base.garage_spaces, basement=base.basement, stories=base.stories,
            public_remarks=base.public_remarks,
        )

    def inputs_from_fields(self, listing: ListingData) -> PropertyInputs:
        expenses = OperatingExpenses(
            property_tax_annual=self.f_tax.get(0.0) or 0.0,
            insurance_annual=self.f_insurance.get(0.0) or 0.0,
            hoa_annual=(self.f_hoa.get(0.0) or 0.0) * 12,
            other_fixed_annual=self.f_other.get(0.0) or 0.0,
            management_pct=(self.f_mgmt.get(0.0) or 0.0) / 100,
            maintenance_pct=(self.f_maint.get(8.0) or 0.0) / 100,
            capex_reserve_pct=(self.f_capex.get(8.0) or 0.0) / 100,
        )
        return PropertyInputs(
            purchase_price=listing.price,
            # No fallback: underwrite() refuses to run without a rent, so by
            # the time we get here the box is known to hold one.
            monthly_rent=self.f_rent.get(),
            down_payment_pct=(self.f_down.get(20.0) or 0.0) / 100,
            interest_rate=(self.f_rate.get(7.0) or 0.0) / 100,
            loan_term_years=int(self.f_term.get(30) or 30),
            closing_cost_pct=(self.f_closing.get(3.0) or 0.0) / 100,
            initial_capex=self.f_capex0.get(0.0) or 0.0,
            vacancy_rate=(self.f_vacancy.get(8.33) or 0.0) / 100,
            hold_years=int(self.f_hold.get(30) or 30),
            lease_up_months=self.f_lease_up.get(1.0) or 0.0,
            year1_repair_bump_pct=(self.f_repair_bump.get(0.0) or 0.0) / 100,
            exit_cap_rate=((self.f_exit_cap.get() / 100)
                           if self.f_exit_cap.get() else None),
            expenses=expenses,
            rent_growth=(self.f_rent_growth.get(3.0) or 0.0) / 100,
            expense_growth=(self.f_exp_growth.get(1.25) or 0.0) / 100,
            appreciation=(self.f_appreciation.get(3.0) or 0.0) / 100,
            label=self.f_address.get_text(),
        )

    def thresholds_from_fields(self) -> Thresholds:
        # The number in a box is always read, ticked or not, so a bar you
        # switch off and back on comes back as you left it. The tick is what
        # decides whether screen() enforces it.
        return Thresholds(
            min_dscr=self.f_min_dscr.get(1.25) or 0.0,
            min_cap_rate=(self.f_min_cap.get(5.0) or 0.0) / 100,
            min_cash_on_cash=(self.f_min_coc.get(8.0) or 0.0) / 100,
            min_irr=(self.f_min_irr.get(10.0) or 0.0) / 100,
            min_monthly_cash_flow=self.f_min_cf.get(0.0) or 0.0,
            screen_dscr=self.f_min_dscr.is_on(),
            screen_cap_rate=self.f_min_cap.is_on(),
            screen_cash_on_cash=self.f_min_coc.is_on(),
            screen_irr=self.f_min_irr.is_on(),
            screen_monthly_cash_flow=self.f_min_cf.is_on(),
            cash_flow_wiggle_pct=(self.f_cf_wiggle.get(10.0) or 0.0) / 100,
            # These two are screened only when ticked AND filled in: for them
            # the value carries the switch downstream (see report.Thresholds),
            # so an unticked box has to arrive as None.
            min_after_tax_irr=((self.f_min_at_irr.get() / 100)
                               if (self.f_min_at_irr.is_on()
                                   and self.f_min_at_irr.get() is not None) else None),
            max_breakeven_occupancy=((self.f_max_be_occ.get() / 100)
                                     if (self.f_max_be_occ.is_on()
                                         and self.f_max_be_occ.get() is not None) else None),
        )

    # --- Actions --------------------------------------------------------

    def tax_assumptions_from_fields(self) -> TaxAssumptions:
        magi = self.f_magi.get()
        return TaxAssumptions(
            building_share=(self.f_building_share.get(80.0) or 80.0) / 100,
            federal_ordinary_rate=(self.f_fed_rate.get(22.0) or 0.0) / 100,
            state_ordinary_rate=(self.f_state_rate.get(4.25) or 0.0) / 100,
            city_ordinary_rate=(self.f_city_rate.get(0.0) or 0.0) / 100,
            capital_gains_rate=(self.f_ltcg_rate.get(15.0) or 0.0) / 100,
            depreciation_recapture_rate=(self.f_recapture_rate.get(25.0) or 0.0) / 100,
            niit=bool(self.v_niit.get()),
            passive_losses_usable=bool(self.v_passive_usable.get()),
            magi=magi if magi else None,
        )

    def underwrite(self) -> None:
        listing = self.listing_from_fields()
        if not listing.price:
            self.status.set("Enter a purchase price to underwrite.")
            return

        # Rent and tax are validated HERE, before anything downstream runs,
        # so nothing ever sees a missing figure and quietly substitutes one.
        rent = self.f_rent.get()
        if not rent or rent <= 0:
            self.status.set("Enter a monthly rent (from comps) to underwrite. "
                            "Nothing fills this in for you.")
            return
        tax = self.f_tax.get()
        if not tax or tax <= 0:
            self.status.set("Enter an annual property tax to underwrite. Use the MI tax "
                            "estimator button -- the seller's bill is not your bill.")
            return

        try:
            # Every number the form owns goes IN to enrich(); it computes no
            # figure the form would then have to overwrite. Its job here is
            # notes, warnings and the tax sanity check.
            enriched = enrich(
                listing,
                property_tax_annual=tax,
                monthly_rent=rent,
                insurance_annual=self.f_insurance.get(),
            )
            inputs = self.inputs_from_fields(listing)
            result = underwrite(inputs)
            after_tax = compute_after_tax(result, self.tax_assumptions_from_fields())
        except Exception as exc:
            self.status.set(f"Could not underwrite: {exc}")
            messagebox.showerror("Underwriting failed", str(exc))
            return

        self.listing, self.enriched, self.result = listing, enriched, result
        self.after_tax = after_tax
        thresholds = self.thresholds_from_fields()

        self._set_text(self.txt_report,
                       format_report(result, enriched, thresholds, projection_years=0))
        self._set_text(self.txt_projection, self._projection_text(result))
        self._set_text(self.txt_sensitivity, self._sensitivity_text(inputs))
        self._set_text(self.txt_after_tax, format_after_tax(after_tax, result))
        self._update_banner(result, thresholds)
        self.status.set(
            f"Underwrote {listing.full_address or listing.address or 'deal'} at "
            f"{money(listing.price)}  |  rent {money(rent)}/mo, tax {money(tax)}/yr "
            "(both your figures)"
        )

    def _update_banner(self, result: UnderwritingResult, thresholds: Thresholds) -> None:
        checks = screen(result, thresholds, self.after_tax)
        text = verdict(checks)
        # A cash-flow miss inside the wiggle room says so on the headline: the
        # difference between "short $40/mo" and "short $400/mo" is the whole
        # question of whether the deal is worth reworking.
        gap = cash_flow_gap(result, thresholds)
        if gap is not None and gap.close:
            text += (f"  CLOSE on cash flow: {money(gap.short_by)}/mo short of "
                     f"{money(gap.threshold)}/mo -- see the report for what closes it.")
        color = OK_COLOR if text.startswith("PASS") else (
            WARN_COLOR if text.startswith(("MARGINAL", "NOT SCREENED")) else BAD_COLOR)
        self.verdict_label.configure(text=text, foreground=color)
        chips = "Year 1 (incl. lease-up):   " + "   ".join([
            f"Cap {result.cap_rate * 100:5.2f}%",
            f"CoC {result.cash_on_cash * 100:6.2f}%",
            f"DSCR {result.dscr:4.2f}",
            f"IRR {((result.irr_screened or 0) * 100):5.2f}%",
            f"Cash flow {money(result.monthly_cash_flow)}/mo",
            f"Breakeven occ {result.breakeven_occupancy * 100:5.1f}%",
            f"Cash in {money(result.total_cash_invested)}",
        ])
        if result.multiple_irr_possible:
            chips += "   (multiple IRRs possible)"
        if result.stabilized_dscr is not None:
            chips += ("\nStabilized (year 2):      "
                      f"DSCR {result.stabilized_dscr:4.2f}   "
                      f"CoC {result.stabilized_cash_on_cash * 100:6.2f}%   "
                      f"Cash flow {money(result.stabilized_monthly_cash_flow)}/mo")
        if self.after_tax is not None:
            chips += ("\nAfter tax:                "
                      f"IRR {((self.after_tax.irr_screened or 0) * 100):5.2f}%   "
                      f"Year-1 CF {money(self.after_tax.year1_after_tax_cash_flow / 12)}/mo")
        self.metrics_label.configure(text=chips)

    def _projection_text(self, result: UnderwritingResult) -> str:
        rows = []
        for y in result.years:
            rows.append([
                str(y.year), money(y.gross_rent), money(y.operating_expenses), money(y.noi),
                money(y.debt_service), money(y.cash_flow), money(y.cumulative_cash_flow),
                money(y.loan_balance), money(y.property_value), money(y.equity),
            ])
        return render_table(
            ["Yr", "Gross rent", "Opex", "NOI", "Debt svc", "Cash flow", "Cum CF",
             "Loan bal", "Value", "Equity"],
            rows,
            title=f"Full {result.horizon}-year projection",
        )

    def _sensitivity_text(self, inputs: PropertyInputs) -> str:
        parts = [
            render_grid(rent_growth_vs_vacancy(inputs, metric="IRR")),
            render_grid(rent_growth_vs_vacancy(inputs, metric="DSCR")),
            render_grid(interest_rate_grid(inputs)),
            render_grid(rent_level_grid(inputs)),
        ]
        be = breakeven_rent(inputs)
        gap = (be - inputs.monthly_rent) / inputs.monthly_rent if inputs.monthly_rent else 0.0
        parts.append(f"Breakeven rent (year-1 cash flow = $0, lease-up included): {money(be)}/mo "
                     f"vs your {money(inputs.monthly_rent)}/mo ({gap:+.1%}).")
        return "\n\n".join(parts)

    # --- Comparison -----------------------------------------------------

    def add_to_comparison(self) -> None:
        if not self.result:
            self.status.set("Underwrite a deal first, then add it to the comparison.")
            return
        label = self.f_address.get_text() or self.result.inputs.label or "Unnamed deal"
        row = one_line_summary(self.result, self.thresholds_from_fields(), label)
        # IRR in this row is the SCREENED IRR (the lower of the two exits),
        # matching the screening table -- see report.one_line_summary.
        tag = "pass" if row[-1] == "PASS" else "fail"
        self.tree.insert("", "end", values=row, tags=(tag,))
        self.comparison.append((label, row))
        self.tabs.select(4)
        self.status.set(f"Added {label} to the comparison ({len(self.comparison)} deal(s)).")

    def remove_selected(self) -> None:
        for item in self.tree.selection():
            index = self.tree.index(item)
            self.tree.delete(item)
            if 0 <= index < len(self.comparison):
                self.comparison.pop(index)

    def clear_comparison(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.comparison.clear()

    # --- Export ---------------------------------------------------------

    def _ask_save(self, default: str, kinds) -> Optional[str]:
        return filedialog.asksaveasfilename(defaultextension=os.path.splitext(default)[1],
                                            initialfile=default, filetypes=kinds) or None

    def _slug(self) -> str:
        raw = (self.f_address.get_text() or "deal").split(",")[0]
        return "".join(c if c.isalnum() else "_" for c in raw).strip("_") or "deal"

    def export_report(self) -> None:
        if not self.result:
            self.status.set("Nothing to export yet.")
            return
        path = self._ask_save(f"{self._slug()}_underwriting.txt", [("Text file", "*.txt")])
        if not path:
            return
        content = "\n\n".join([
            format_report(self.result, self.enriched, self.thresholds_from_fields(),
                          after_tax=self.after_tax),
            self._sensitivity_text(self.result.inputs),
            self._projection_text(self.result),
        ])
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.status.set(f"Saved report to {path}")

    def export_json(self) -> None:
        if not self.result:
            self.status.set("Nothing to export yet.")
            return
        path = self._ask_save(f"{self._slug()}_underwriting.json", [("JSON file", "*.json")])
        if not path:
            return
        result, inputs = self.result, self.result.inputs
        payload = {
            "listing": self.listing.to_dict(),
            "assumptions": {
                # Rent and tax are always the user's figures now, so there is
                # no source to record.
                "monthly_rent": inputs.monthly_rent,
                "property_tax_annual": inputs.expenses.property_tax_annual,
                "insurance_annual": inputs.expenses.insurance_annual,
                "initial_make_ready": inputs.initial_capex,
                "lease_up_months": inputs.lease_up_months,
                "year1_repair_bump_pct": inputs.year1_repair_bump_pct,
                "exit_cap_rate": result.exit_cap_rate_used,
                "down_payment_pct": inputs.down_payment_pct,
                "interest_rate": inputs.interest_rate,
                "loan_term_years": inputs.loan_term_years,
                "closing_cost_pct": inputs.closing_cost_pct,
                "vacancy_rate": inputs.vacancy_rate,
                "hold_years": inputs.hold_years,
            },
            "metrics": {
                "cap_rate": result.cap_rate,
                "cash_on_cash": result.cash_on_cash,
                "dscr": result.dscr,
                "irr_appreciation_exit": result.irr_appreciation_exit,
                "irr_cap_rate_exit": result.irr_cap_rate_exit,
                "irr_screened": result.irr_screened,
                "multiple_irr_possible": result.multiple_irr_possible,
                "monthly_cash_flow": result.monthly_cash_flow,
                "year1_noi": result.year1_noi,
                "breakeven_occupancy": result.breakeven_occupancy,
                "stabilized_dscr": result.stabilized_dscr,
                "stabilized_cash_on_cash": result.stabilized_cash_on_cash,
                "stabilized_monthly_cash_flow": result.stabilized_monthly_cash_flow,
                "terminal_value_cap_rate": result.terminal_value_cap_rate,
                "net_sale_appreciation": result.net_sale_appreciation,
                "net_sale_cap_rate": result.net_sale_cap_rate,
                "exit_values_disagree": result.exit_values_disagree,
            },
            "notes": self.enriched.notes if self.enriched else [],
            "warnings": self.enriched.warnings if self.enriched else [],
        }
        if self.after_tax is not None:
            payload["after_tax"] = {
                "year1_after_tax_cash_flow": self.after_tax.year1_after_tax_cash_flow,
                "irr_appreciation_exit": self.after_tax.irr_appreciation_exit,
                "irr_cap_rate_exit": self.after_tax.irr_cap_rate_exit,
                "irr_screened": self.after_tax.irr_screened,
                "depreciable_basis": self.after_tax.depreciable_basis,
                "accumulated_depreciation": self.after_tax.accumulated_depreciation,
                "suspended_balance_at_sale": self.after_tax.suspended_balance_at_sale,
                "total_tax_on_operations": self.after_tax.total_tax_on_operations,
                "exit_tax_appreciation": self.after_tax.sale_appreciation.total_tax,
                "exit_tax_cap_rate": self.after_tax.sale_cap_rate.total_tax,
            }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)
        self.status.set(f"Saved JSON to {path}")

    def export_comparison(self) -> None:
        if not self.comparison:
            self.status.set("Comparison table is empty.")
            return
        path = self._ask_save("comparison.csv", [("CSV file", "*.csv")])
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(BATCH_HEADERS)
            writer.writerows(row for _label, row in self.comparison)
        self.status.set(f"Saved comparison to {path}")

    # --- Defaults -------------------------------------------------------

    def reset_fields(self, keep_listing: bool = True) -> None:
        defaults: Dict[LabeledEntry, str] = {
            self.f_down: "20", self.f_rate: "7.0", self.f_term: "30", self.f_closing: "3.0",
            self.f_capex0: "2,500", self.f_hold: "30", self.f_vacancy: "8.33",
            self.f_rent_growth: "3.0", self.f_exp_growth: "1.25", self.f_appreciation: "3.0",
            self.f_lease_up: "1", self.f_repair_bump: "0", self.f_exit_cap: "",
            self.f_mgmt: "0", self.f_maint: "8", self.f_capex: "8", self.f_other: "0",
            self.f_building_share: "80", self.f_fed_rate: "22", self.f_state_rate: "4.25",
            self.f_city_rate: "0", self.f_ltcg_rate: "15", self.f_recapture_rate: "25",
            self.f_magi: "",
            self.f_min_dscr: "1.25", self.f_min_cap: "5.0", self.f_min_coc: "8.0",
            self.f_min_irr: "10.0", self.f_min_cf: "0", self.f_min_at_irr: "6.0",
            self.f_max_be_occ: "85", self.f_cf_wiggle: "10",
        }
        for widget, value in defaults.items():
            widget.var.set(value)
        # Which bars are enforced on a fresh form: the two cash tests only.
        for widget, on in ((self.f_min_dscr, False), (self.f_min_cap, False),
                           (self.f_min_coc, True), (self.f_min_irr, False),
                           (self.f_min_cf, True), (self.f_min_at_irr, False),
                           (self.f_max_be_occ, False)):
            widget.set_on(on)
        self.v_niit.set(False)
        self.v_passive_usable.set(True)
        if not keep_listing:
            return
        self.listing = ListingData()
        for widget in (self.f_address, self.f_price, self.f_beds, self.f_baths, self.f_sqft,
                       self.f_year, self.f_county, self.f_tax, self.f_rent,
                       self.f_insurance, self.f_hoa):
            widget.var.set("")
        self._auto_insurance = None
        self.after_tax = None
        self.status.set("Cleared. Load a listing or type a deal in by hand "
                        "(price, rent and tax are all required).")


def main() -> int:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    RentalAnalyzerGUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
