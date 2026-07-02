"""
bible_books.py

Canonical 66-book ordering shared by the xref extraction script and the
MySword RX-tag formatter: USX/USFM 3-letter codes (matching the `code`
attribute in BSB USX source files) mapped to our own display abbreviation
convention (e.g. "Joh", "1Co", "Sol" — used throughout xref data) and to
biblelib's numeric book index (1-66).
"""

from biblelib.book import Books

# USX/USFM code -> our display abbreviation, in canonical (Protestant) order.
# The display abbreviation matches the TSK_ABBREV convention (utils/extract_tsk.py)
# rather than biblelib's osisID, which uses longer forms (Exod, Judg, 1Sam, ...).
USX_TO_ABBREV = {
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

BOOK_ORDER = list(USX_TO_ABBREV)

# USX code -> biblelib usfmnumber string ("01".."66", zero-padded).
BOOK_NUMBER = {book.usfmname: book.usfmnumber
               for book in Books().values() if book.usfmname in USX_TO_ABBREV}

# Our display abbreviation -> book number as int (1-66), derived from biblelib
# rather than hand-numbered, so MySword's <RX b.c.v> tags stay correct even if
# biblelib's own numbering ever changes.
ABBREV_TO_BOOK_NUM = {abbrev: int(BOOK_NUMBER[usx])
                      for usx, abbrev in USX_TO_ABBREV.items()}
