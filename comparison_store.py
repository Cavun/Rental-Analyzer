"""
comparison_store.py -- keeps the GUI's comparison table between sessions.

Each saved deal is the row the table shows PLUS the form exactly as it was
typed when the deal was added: every box as text, every threshold switch,
the two tax checkboxes, and the parsed listing. Re-opening a row puts that
form back and re-runs the underwrite, so nothing is stored that the model
would have to trust instead of recompute.

JSON, not CSV: a deal is a nested record (form + listing + row), and CSV
would flatten it into something only this module could read back anyway.

The file is the user's own data. It lives beside the program when run from
source (and is git-ignored there), and in ~/.rental_analyzer/ in a packaged
build, where the program's own folder may be read-only or replaced on
upgrade. RENTAL_ANALYZER_DATA_DIR overrides both -- the tests use it.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

FILENAME = "saved_comparison.json"
VERSION = 1


def data_dir() -> str:
    override = os.environ.get("RENTAL_ANALYZER_DATA_DIR")
    if override:
        return override
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.expanduser("~"), ".rental_analyzer")
    return os.path.dirname(os.path.abspath(__file__))


def store_path() -> str:
    return os.path.join(data_dir(), FILENAME)


def load(path: str = None) -> List[Dict[str, Any]]:
    """
    Saved deals, oldest first. A missing file is an empty table. A file that
    will not parse is moved aside to <name>.corrupt rather than overwritten
    by the next save, and ValueError says where it went.
    """
    path = path or store_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        deals = payload["deals"]
        if not isinstance(deals, list):
            raise TypeError("'deals' is not a list")
        return [d for d in deals if isinstance(d, dict) and "row" in d]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        aside = path + ".corrupt"
        try:
            os.replace(path, aside)
        except OSError:
            aside = path
        raise ValueError(f"Could not read saved comparison ({exc}); "
                         f"moved it to {aside} and started an empty table.") from exc


def save(deals: List[Dict[str, Any]], path: str = None) -> str:
    """Write every deal atomically (temp file, then rename). Returns the path."""
    path = path or store_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"version": VERSION, "deals": deals}, fh, indent=2, default=str)
    os.replace(tmp, path)
    return path
