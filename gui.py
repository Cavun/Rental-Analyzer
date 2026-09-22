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
    3. Adjust assumptions, hit Underwrite (or just press Enter in any box).
    4. Add the deal to the comparison tab and move on to the next listing.
"""

from __future__ import annotations

import json
import os
import sys
import webbrowser
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from enrichment import EnrichmentAssumptions, enrich
from extraction import BS4_AVAILABLE, ListingData, ParserUnavailableError, parse_listing
from financial_engine import OperatingExpenses, PropertyInputs, UnderwritingResult, underwrite
from report import (
    BATCH_HEADERS,
    Thresholds,
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
                 width: int = 12, suffix: str = "", tooltip: str = "", wide: bool = False):
        self.var = tk.StringVar()
        label_widget = ttk.Label(parent, text=label)
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
        self._auto_tax: Optional[float] = None       # last auto-filled tax value
        self._auto_insurance: Optional[float] = None
        self.status = tk.StringVar(value="Load a listing, or just type a price and hit Underwrite.")

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
        ttk.Button(bar, text="Load sample", command=self.load_sample).pack(side="left", padx=2)
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

    def _build_inputs(self, parent: tk.Widget) -> None:
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0, width=560)
        scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        holder = ttk.Frame(canvas)
        holder.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=holder, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

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
        tax = self._section(holder, "Property tax")
        self.f_seller_tax = LabeledEntry(
            tax, 0, 0, "Seller's annual tax", suffix="$",
            tooltip="What the SELLER pays today. Not what you will pay: it is capped "
                    "and usually homestead-exempt. Shown for reference only.")
        self.f_taxable = LabeledEntry(tax, 0, 1, "Taxable value", suffix="$")
        self.f_sev = LabeledEntry(tax, 1, 0, "SEV", suffix="$")
        self.f_homestead = LabeledEntry(tax, 1, 1, "Homestead %", suffix="%")
        self.f_tax = LabeledEntry(
            tax, 2, 0, "ANNUAL TAX USED", suffix="$",
            tooltip="The figure the underwrite actually uses. Auto-filled with an estimate "
                    "when you parse a listing; type over it with a verified number from the "
                    "state estimator and it is used verbatim.")
        self.tax_source_label = ttk.Label(tax, text="", foreground=WARN_COLOR)
        self.tax_source_label.grid(row=2, column=3, columnspan=3, sticky="w", padx=4)
        button_row = ttk.Frame(tax)
        button_row.grid(row=3, column=0, columnspan=6, sticky="w", pady=(4, 2))
        ttk.Button(button_row, text="Open MI tax estimator",
                   command=lambda: webbrowser.open(TAX_ESTIMATOR_URL)).pack(side="left", padx=4)
        ttk.Button(button_row, text="Re-estimate from listing",
                   command=self.reestimate_tax).pack(side="left", padx=4)

        # --- Income ---------------------------------------------------
        income = self._section(holder, "Income")
        self.f_rent = LabeledEntry(income, 0, 0, "Monthly rent", suffix="$",
                                   tooltip="Auto-filled at 1% of price. Replace it with a real "
                                           "rent comp as soon as you have one -- this is the "
                                           "single most load-bearing number in the model.")
        self.f_rent_pct_btn = ttk.Button(income, text="= 1% of price", width=13, command=self.apply_one_percent)
        self.f_rent_pct_btn.grid(row=0, column=3, columnspan=2, sticky="w", padx=4)
        self.f_vacancy = LabeledEntry(income, 1, 0, "Vacancy", suffix="%",
                                      tooltip="8.33% = one vacant month per year.")
        self.f_rent_growth = LabeledEntry(income, 1, 1, "Rent growth", suffix="%/yr")

        # --- Financing ------------------------------------------------
        fin = self._section(holder, "Financing")
        self.f_down = LabeledEntry(fin, 0, 0, "Down payment", suffix="%")
        self.f_rate = LabeledEntry(fin, 0, 1, "Interest rate", suffix="%")
        self.f_term = LabeledEntry(fin, 1, 0, "Loan term", suffix="yrs")
        self.f_closing = LabeledEntry(fin, 1, 1, "Closing costs", suffix="%")
        self.f_capex0 = LabeledEntry(fin, 2, 0, "Initial capex", suffix="$",
                                     tooltip="Rehab budget. 0 for a turn-key property.")
        self.f_hold = LabeledEntry(fin, 2, 1, "Projection", suffix="yrs",
                                   tooltip="A forever hold is modeled over the full loan term.")

        # --- Operating expenses ---------------------------------------
        ops = self._section(holder, "Operating expenses")
        self.f_insurance = LabeledEntry(ops, 0, 0, "Insurance", suffix="$/yr")
        self.f_hoa = LabeledEntry(ops, 0, 1, "HOA", suffix="$/mo")
        self.f_mgmt = LabeledEntry(ops, 1, 0, "Management", suffix="% EGI")
        self.f_maint = LabeledEntry(ops, 1, 1, "Maintenance", suffix="% EGI")
        self.f_capex = LabeledEntry(ops, 2, 0, "Capex reserve", suffix="% EGI")
        self.f_other = LabeledEntry(ops, 2, 1, "Other fixed", suffix="$/yr")
        self.f_exp_growth = LabeledEntry(ops, 3, 0, "Expense growth", suffix="%/yr")
        self.f_appreciation = LabeledEntry(ops, 3, 1, "Appreciation", suffix="%/yr")

        # --- Thresholds -----------------------------------------------
        thr = self._section(holder, "Screening thresholds")
        self.f_min_dscr = LabeledEntry(thr, 0, 0, "Min DSCR")
        self.f_min_cap = LabeledEntry(thr, 0, 1, "Min cap rate", suffix="%")
        self.f_min_coc = LabeledEntry(thr, 1, 0, "Min cash-on-cash", suffix="%")
        self.f_min_irr = LabeledEntry(thr, 1, 1, "Min IRR", suffix="%")

        self.reset_fields(keep_listing=False)

    # --- Output panel ---------------------------------------------------

    def _build_output(self, parent: tk.Widget) -> None:
        banner = ttk.Frame(parent, padding=(4, 2))
        banner.pack(fill="x")
        self.verdict_label = tk.Label(banner, text="No deal underwritten yet",
                                      font=("TkDefaultFont", 13, "bold"), anchor="w",
                                      justify="left", wraplength=780)
        self.verdict_label.pack(fill="x")
        banner.bind("<Configure>",
                    lambda e: self.verdict_label.configure(wraplength=max(400, e.width - 20)))
        self.metrics_label = tk.Label(banner, text="", font=MONO, anchor="w", justify="left")
        self.metrics_label.pack(fill="x", pady=(2, 4))

        self.tabs = ttk.Notebook(parent)
        self.tabs.pack(fill="both", expand=True)
        self.txt_report = self._text_tab("Report")
        self.txt_projection = self._text_tab("Projection")
        self.txt_sensitivity = self._text_tab("Sensitivity")
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
        text.tag_configure("flag", foreground=BAD_COLOR)
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
        self.tree.tag_configure("flag", foreground=BAD_COLOR)

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
        # Colour the PASS/FLAG markers so the eye lands on them first.
        for marker, tag in (("PASS", "pass"), ("FLAG", "flag")):
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
        self.f_seller_tax.set(listing.property_tax_annual or "", decimals=0)
        self.f_taxable.set(listing.taxable_value or "", decimals=0)
        self.f_sev.set(listing.sev or "", decimals=0)
        self.f_homestead.set(listing.homestead_pct if listing.homestead_pct is not None else "")
        self.f_hoa.set(listing.hoa_monthly if listing.hoa_monthly is not None else 0)

        if listing.price:
            self.apply_one_percent()
            self.reestimate_tax()

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
            property_tax_annual=self.f_seller_tax.get(),
            hoa_monthly=self.f_hoa.get(0.0),
            days_on_market=base.days_on_market,
            mls_id=base.mls_id, mls_status=base.mls_status,
            county=self.f_county.get_text() or base.county,
            municipality=base.municipality, lot_acres=base.lot_acres,
            taxable_value=self.f_taxable.get(), sev=self.f_sev.get(),
            homestead_pct=self.f_homestead.get(), tax_year=base.tax_year,
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
            management_pct=(self.f_mgmt.get(8.0) or 0.0) / 100,
            maintenance_pct=(self.f_maint.get(8.0) or 0.0) / 100,
            capex_reserve_pct=(self.f_capex.get(8.0) or 0.0) / 100,
        )
        return PropertyInputs(
            purchase_price=listing.price,
            monthly_rent=self.f_rent.get(listing.price * 0.01),
            down_payment_pct=(self.f_down.get(20.0) or 0.0) / 100,
            interest_rate=(self.f_rate.get(7.0) or 0.0) / 100,
            loan_term_years=int(self.f_term.get(30) or 30),
            closing_cost_pct=(self.f_closing.get(3.0) or 0.0) / 100,
            initial_capex=self.f_capex0.get(0.0) or 0.0,
            vacancy_rate=(self.f_vacancy.get(8.33) or 0.0) / 100,
            hold_years=int(self.f_hold.get(30) or 30),
            expenses=expenses,
            rent_growth=(self.f_rent_growth.get(3.0) or 0.0) / 100,
            expense_growth=(self.f_exp_growth.get(2.5) or 0.0) / 100,
            appreciation=(self.f_appreciation.get(3.0) or 0.0) / 100,
            label=self.f_address.get_text(),
        )

    def thresholds_from_fields(self) -> Thresholds:
        return Thresholds(
            min_dscr=self.f_min_dscr.get(1.25) or 0.0,
            min_cap_rate=(self.f_min_cap.get(5.0) or 0.0) / 100,
            min_cash_on_cash=(self.f_min_coc.get(8.0) or 0.0) / 100,
            min_irr=(self.f_min_irr.get(10.0) or 0.0) / 100,
        )

    # --- Actions --------------------------------------------------------

    def apply_one_percent(self) -> None:
        price = self.f_price.get(0.0) or 0.0
        if price:
            self.f_rent.set(price * 0.01, decimals=0)

    def reestimate_tax(self) -> None:
        """Fill the tax box with the listing-derived estimate."""
        listing = self.listing_from_fields()
        if not listing.price:
            return
        try:
            enriched = enrich(listing)
        except ValueError:
            return
        self._auto_tax = enriched.property_tax_annual
        self.f_tax.set(enriched.property_tax_annual, decimals=0)
        if self._auto_insurance is None or self.f_insurance.get(0.0) in (None, 0.0, self._auto_insurance):
            self._auto_insurance = enriched.insurance_annual
            self.f_insurance.set(enriched.insurance_annual, decimals=0)
        self._refresh_tax_source_label()

    def _tax_is_provided(self) -> bool:
        """True when the user typed over the auto-filled estimate."""
        current = self.f_tax.get()
        if current is None:
            return False
        if self._auto_tax is None:
            return True
        return abs(current - self._auto_tax) > 0.51

    def _refresh_tax_source_label(self) -> None:
        if self._tax_is_provided():
            self.tax_source_label.configure(text="VERIFIED (your figure)", foreground=OK_COLOR)
        else:
            self.tax_source_label.configure(text="estimated from listing", foreground=WARN_COLOR)

    def underwrite(self) -> None:
        listing = self.listing_from_fields()
        if not listing.price:
            self.status.set("Enter a purchase price to underwrite.")
            return

        self._refresh_tax_source_label()
        provided_tax = self.f_tax.get() if self._tax_is_provided() else None
        try:
            enriched = enrich(listing, property_tax_annual=provided_tax)
            # The form is the source of truth for the numbers it owns.
            enriched.monthly_rent = self.f_rent.get(enriched.monthly_rent) or enriched.monthly_rent
            enriched.insurance_annual = self.f_insurance.get(enriched.insurance_annual)
            enriched.property_tax_annual = self.f_tax.get(enriched.property_tax_annual)
            inputs = self.inputs_from_fields(listing)
            result = underwrite(inputs)
        except Exception as exc:
            self.status.set(f"Could not underwrite: {exc}")
            messagebox.showerror("Underwriting failed", str(exc))
            return

        self.listing, self.enriched, self.result = listing, enriched, result
        thresholds = self.thresholds_from_fields()

        self._set_text(self.txt_report, format_report(result, enriched, thresholds, projection_years=0))
        self._set_text(self.txt_projection, self._projection_text(result))
        self._set_text(self.txt_sensitivity, self._sensitivity_text(inputs))
        self._update_banner(result, thresholds)
        self.status.set(
            f"Underwrote {listing.full_address or listing.address or 'deal'} at {money(listing.price)}"
            + ("  |  tax: your verified figure" if provided_tax is not None else "  |  tax: estimated")
        )

    def _update_banner(self, result: UnderwritingResult, thresholds: Thresholds) -> None:
        checks = screen(result, thresholds)
        text = verdict(checks)
        color = OK_COLOR if text.startswith("INVESTIGATE") else (
            WARN_COLOR if text.startswith("MARGINAL") else BAD_COLOR)
        self.verdict_label.configure(text=text, foreground=color)
        chips = "   ".join([
            f"Cap {result.cap_rate * 100:5.2f}%",
            f"CoC {result.cash_on_cash * 100:6.2f}%",
            f"DSCR {result.dscr:4.2f}",
            f"IRR {((result.irr_with_equity or 0) * 100):5.2f}%",
            f"Cash flow {money(result.monthly_cash_flow)}/mo",
            f"Cash in {money(result.total_cash_invested)}",
        ])
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
        parts.append(f"Breakeven rent (year-1 cash flow = $0): {money(be)}/mo "
                     f"vs assumed {money(inputs.monthly_rent)}/mo ({gap:+.1%}).")
        return "\n\n".join(parts)

    # --- Comparison -----------------------------------------------------

    def add_to_comparison(self) -> None:
        if not self.result:
            self.status.set("Underwrite a deal first, then add it to the comparison.")
            return
        label = self.f_address.get_text() or self.result.inputs.label or "Unnamed deal"
        row = one_line_summary(self.result, self.thresholds_from_fields(), label)
        tag = "pass" if row[-1] == "PASS" else "flag"
        self.tree.insert("", "end", values=row, tags=(tag,))
        self.comparison.append((label, row))
        self.tabs.select(3)
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
            format_report(self.result, self.enriched, self.thresholds_from_fields()),
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
                "monthly_rent": inputs.monthly_rent,
                "property_tax_annual": inputs.expenses.property_tax_annual,
                "property_tax_source": "provided" if self._tax_is_provided() else "estimated",
                "insurance_annual": inputs.expenses.insurance_annual,
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
                "irr_with_equity": result.irr_with_equity,
                "irr_cash_flow_only": result.irr_cash_flow_only,
                "monthly_cash_flow": result.monthly_cash_flow,
                "year1_noi": result.year1_noi,
                "breakeven_occupancy": result.breakeven_occupancy,
            },
            "notes": self.enriched.notes if self.enriched else [],
            "warnings": self.enriched.warnings if self.enriched else [],
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
            self.f_capex0: "0", self.f_hold: "30", self.f_vacancy: "8.33",
            self.f_rent_growth: "3.0", self.f_exp_growth: "2.5", self.f_appreciation: "3.0",
            self.f_mgmt: "8", self.f_maint: "8", self.f_capex: "8", self.f_other: "0",
            self.f_min_dscr: "1.25", self.f_min_cap: "5.0", self.f_min_coc: "8.0",
            self.f_min_irr: "10.0",
        }
        for widget, value in defaults.items():
            widget.var.set(value)
        if not keep_listing:
            return
        self.listing = ListingData()
        for widget in (self.f_address, self.f_price, self.f_beds, self.f_baths, self.f_sqft,
                       self.f_year, self.f_county, self.f_seller_tax, self.f_taxable,
                       self.f_sev, self.f_homestead, self.f_tax, self.f_rent,
                       self.f_insurance, self.f_hoa):
            widget.var.set("")
        self._auto_tax = self._auto_insurance = None
        self.status.set("Cleared. Load a listing or type a deal in by hand.")


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
