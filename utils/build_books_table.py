"""
build_books_table.py

One-time build: writes data/books.db, a small shared reference table used
by composer.py, verse_formatter.py, and the utils/extract_* scripts in
place of the old bible_books.py.

biblelib is the canonical source for everything that's actually biblelib's
to know (osis_id, usfm_number) — this script just queries it once and
persists the two things biblelib *doesn't* have: our own display
abbreviation convention (e.g. "Joh", "1Co", "Sol" — shorter than biblelib's
osisID for compound names), and an explicit testament/canon_order so
nothing has to slice into biblelib's own (apocrypha-inclusive) iteration
order to figure out where the NT starts.

Usage:
    python utils/build_books_table.py [--output FILE]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from biblelib.book import Books

DEFAULT_OUTPUT = ROOT / "data" / "books.db"

DDL = """
CREATE TABLE books (
    usx_code       TEXT PRIMARY KEY,   -- 'GEN', 'MAT', ... (biblelib's own dict key)
    osis_id        TEXT UNIQUE NOT NULL,
    display_abbrev TEXT NOT NULL,      -- ours: 'Gen', 'Mat', '1Co', ... (TSK_ABBREV convention)
    usfm_number    INTEGER NOT NULL,
    testament      TEXT NOT NULL CHECK(testament IN ('OT','NT')),
    canon_order    INTEGER NOT NULL UNIQUE
);
"""

# USX code -> our display abbreviation, in canonical (Protestant) order.
# This one thing isn't derivable from biblelib and has to be seed data;
# everything else in the books table below is computed from biblelib.
_DISPLAY_ABBREV_IN_CANONICAL_ORDER = {
    'GEN': 'Gen', 'EXO': 'Exo', 'LEV': 'Lev', 'NUM': 'Num', 'DEU': 'Deu',
    'JOS': 'Jos', 'JDG': 'Jdg', 'RUT': 'Rut', '1SA': '1Sa', '2SA': '2Sa',
    '1KI': '1Ki', '2KI': '2Ki', '1CH': '1Ch', '2CH': '2Ch', 'EZR': 'Ezr',
    'NEH': 'Neh', 'EST': 'Est', 'JOB': 'Job', 'PSA': 'Psa', 'PRO': 'Pro',
    'ECC': 'Ecc', 'SNG': 'Sol', 'ISA': 'Isa', 'JER': 'Jer', 'LAM': 'Lam',
    'EZK': 'Eze', 'DAN': 'Dan', 'HOS': 'Hos', 'JOL': 'Joe', 'AMO': 'Amo',
    'OBA': 'Oba', 'JON': 'Jon', 'MIC': 'Mic', 'NAM': 'Nah', 'HAB': 'Hab',
    'ZEP': 'Zep', 'HAG': 'Hag', 'ZEC': 'Zec', 'MAL': 'Mal', 'MAT': 'Mat',
    'MRK': 'Mar', 'LUK': 'Luk', 'JHN': 'Joh', 'ACT': 'Act', 'ROM': 'Rom',
    '1CO': '1Co', '2CO': '2Co', 'GAL': 'Gal', 'EPH': 'Eph', 'PHP': 'Php',
    'COL': 'Col', '1TH': '1Th', '2TH': '2Th', '1TI': '1Ti', '2TI': '2Ti',
    'TIT': 'Tit', 'PHM': 'Phm', 'HEB': 'Heb', 'JAS': 'Jas', '1PE': '1Pe',
    '2PE': '2Pe', '1JN': '1Jo', '2JN': '2Jo', '3JN': '3Jo', 'JUD': 'Jude',
    'REV': 'Rev',
}
_OT_COUNT = 39  # Genesis..Malachi; everything after that in this list is NT


def build_books_table(db_path: Path) -> None:
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)

    biblelib_books = Books()
    rows = []
    for canon_order, (usx_code, display_abbrev) in enumerate(
            _DISPLAY_ABBREV_IN_CANONICAL_ORDER.items(), start=1):
        book = biblelib_books[usx_code]
        testament = 'OT' if canon_order <= _OT_COUNT else 'NT'
        rows.append((usx_code, book.osisID, display_abbrev,
                      int(book.usfmnumber), testament, canon_order))

    conn.executemany(
        "INSERT INTO books (usx_code, osis_id, display_abbrev, usfm_number, "
        "testament, canon_order) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    print(f"Wrote {len(rows)} books to {db_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                         help=f"Output SQLite path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    build_books_table(args.output)
