# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec -- builds a double-clickable desktop app.

    python -m pip install -r requirements.txt   # bs4 MUST be in this env
    python -m pip install pyinstaller
    python -m PyInstaller rental_analyzer.spec

Output lands in dist/: RentalAnalyzer.exe on Windows, a standalone
RentalAnalyzer binary on Linux, RentalAnalyzer.app on macOS.

IMPORTANT: PyInstaller bundles what the Python running it can import. If
beautifulsoup4 is installed in a different interpreter than the one invoking
PyInstaller, bs4 silently will not make it into the build and the app dies
at startup with "No module named 'bs4'". The preflight check below turns
that into a loud build failure instead.
"""

import sys

from PyInstaller.utils.hooks import collect_all

# --- Preflight: fail loudly at BUILD time, not at the user's first launch ---
try:
    import bs4  # noqa: F401
except ImportError:
    raise SystemExit(
        "\n"
        "=" * 70 + "\n"
        "BUILD STOPPED: beautifulsoup4 is not installed in this Python.\n"
        "=" * 70 + "\n\n"
        f"Interpreter running PyInstaller:\n    {sys.executable}\n\n"
        "PyInstaller can only bundle what THIS interpreter can import, so the\n"
        "app would have been built without the HTML parser and would crash on\n"
        "launch. Install the dependency into this same interpreter and retry:\n\n"
        f'    "{sys.executable}" -m pip install -r requirements.txt\n'
        f'    "{sys.executable}" -m PyInstaller rental_analyzer.spec\n'
    )

# --- Collect the parser and everything it drags along ----------------------
datas, binaries, hiddenimports = [], [], []
for package in ("bs4", "soupsieve"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:
        # soupsieve is an optional bs4 dependency; skip it if it isn't there.
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# The app's own modules. They are already reachable from gui.py, but naming
# them makes the build independent of how the import graph is walked.
hiddenimports += [
    'extraction', 'enrichment', 'financial_engine', 'tax_engine',
    'report', 'sensitivity', 'sample_listing',
]

block_cipher = None
IS_MACOS = sys.platform == 'darwin'

a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing in this app plots, crunches arrays, or talks to the network.
    excludes=['numpy', 'pandas', 'matplotlib', 'PIL', 'pytest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if IS_MACOS:
    # A .app bundle is a directory, so macOS uses onedir + BUNDLE.
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name='RentalAnalyzer',
        debug=False, strip=False, upx=True, console=False,
    )
    coll = COLLECT(
        exe, a.binaries, a.zipfiles, a.datas,
        strip=False, upx=True, name='RentalAnalyzer',
    )
    app = BUNDLE(
        coll,
        name='RentalAnalyzer.app',
        icon=None,
        bundle_identifier='com.rentalanalyzer.underwriting',
        info_plist={'NSHighResolutionCapable': True},
    )
else:
    # Windows and Linux get a single self-contained file.
    exe = EXE(
        pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
        name='RentalAnalyzer',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        runtime_tmpdir=None,
        console=False,          # no terminal window behind the GUI
    )
