"""
Post-build check: does the packaged app actually contain what it needs?

PyInstaller bundles only what the interpreter running it can import. When
something is missing it does not fail -- it writes a clean build log and
produces an .exe that dies on the user's first double-click with
"No module named 'tkinter'" or "No module named 'bs4'". The app is a GUI,
so CI cannot launch it to find out. This reads the bundle's own table of
contents instead, which catches the same thing without a display.

    python .github/scripts/check_bundle.py dist/RentalAnalyzer.exe
"""

import sys

from PyInstaller.archive.readers import CArchiveReader

# Substrings that must appear in the bundle's table of contents. tkinter is
# the GUI itself; bs4 (with soupsieve) is the HTML parser that reads listing
# pages -- without it the app opens but refuses every pasted listing.
REQUIRED = ('tkinter', 'bs4', 'soupsieve')


def main(path):
    names = list(CArchiveReader(path).toc)
    missing = [
        need for need in REQUIRED
        if not any(need in name for name in names)
    ]

    for need in REQUIRED:
        count = sum(need in name for name in names)
        print(f"  {'ok ' if count else 'MISSING'}  {need}: {count} entries")

    if missing:
        print(
            f"\n{path} is missing: {', '.join(missing)}.\n"
            "The build succeeded but the app would crash on launch. The usual\n"
            "cause is that the Python running PyInstaller could not import\n"
            "these -- install them into that same interpreter and rebuild.",
            file=sys.stderr,
        )
        return 1

    print(f"\n{path} looks complete.")
    return 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <path-to-built-app>")
    raise SystemExit(main(sys.argv[1]))
