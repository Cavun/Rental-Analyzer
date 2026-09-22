# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec -- builds a single double-clickable desktop app.

    pip install pyinstaller
    pyinstaller rental_analyzer.spec

Output lands in dist/: RentalAnalyzer.exe on Windows, RentalAnalyzer.app on
macOS, a standalone RentalAnalyzer binary on Linux. No Python install needed
on the target machine.
"""

block_cipher = None

a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    # Imported dynamically through the pipeline, so name them explicitly.
    hiddenimports=[
        'extraction', 'enrichment', 'financial_engine',
        'report', 'sensitivity', 'sample_listing',
        'bs4', 'soupsieve',
    ],
    hookspath=[],
    runtime_hooks=[],
    # Nothing in this app talks to the network or plots anything.
    excludes=['numpy', 'pandas', 'matplotlib', 'PIL', 'pytest', 'setuptools'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='RentalAnalyzer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,          # no terminal window behind the GUI
)

app = BUNDLE(
    exe,
    name='RentalAnalyzer.app',
    icon=None,
    bundle_identifier='com.rentalanalyzer.underwriting',
    info_plist={'NSHighResolutionCapable': True},
)
