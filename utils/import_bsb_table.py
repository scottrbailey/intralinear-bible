"""
import_bsb_table.py

One-time import: parse bsb_tables.tsv into a normalized SQLite `tokens`
table for TableComposer to read at pipeline run time.

bsb_tables.tsv has two columns both literally named "Parsing" (short code
and full description) and one header with embedded spaces (" BSB version
"). csv.DictReader collapses duplicate header names to the last one seen —
confirmed empirically to silently drop the short-code column — so this
module indexes columns positionally instead of trusting header text. That
also means it doesn't care what the file's header row actually says, so
there's no need to edit it.

See docs/DEVELOPMENT.md for the schema/gloss_type rationale — this was
worked out against the real file, not guessed.

Usage:
    python utils/import_bsb_table.py [--source FILE] [--output FILE]
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE = ROOT / "data" / "bsb_tables.tsv"
DEFAULT_OUTPUT = ROOT / "data" / "bsb_tables.db"
BOOKS_DB       = ROOT / "data" / "books.db"

# Full English book name, as cited in bsb_tables.tsv's VerseId column
# (e.g. "Genesis 1:1", "1 Samuel 3:2") -> OSIS book id, in canonical order.
# This is a BSB-citation-format quirk, not a general fact about books, so
# it stays local to this import script rather than living in data/books.db
# (which is shared pipeline-wide infrastructure). Can't be derived live from
# biblelib either: biblelib's own .name differs from the file in exactly two
# places ("Psalms" vs the file's "Psalm", "Song of Songs" vs "Song of
# Solomon") — verified against the actual file: exactly these 66 names, in
# exactly this order, appear as VerseId prefixes.
_FULL_NAMES_IN_CANONICAL_ORDER = [
    'Genesis', 'Exodus', 'Leviticus', 'Numbers', 'Deuteronomy',
    'Joshua', 'Judges', 'Ruth', '1 Samuel', '2 Samuel',
    '1 Kings', '2 Kings', '1 Chronicles', '2 Chronicles', 'Ezra',
    'Nehemiah', 'Esther', 'Job', 'Psalm', 'Proverbs',
    'Ecclesiastes', 'Song of Solomon', 'Isaiah', 'Jeremiah', 'Lamentations',
    'Ezekiel', 'Daniel', 'Hosea', 'Joel', 'Amos',
    'Obadiah', 'Jonah', 'Micah', 'Nahum', 'Habakkuk',
    'Zephaniah', 'Haggai', 'Zechariah', 'Malachi', 'Matthew',
    'Mark', 'Luke', 'John', 'Acts', 'Romans',
    '1 Corinthians', '2 Corinthians', 'Galatians', 'Ephesians', 'Philippians',
    'Colossians', '1 Thessalonians', '2 Thessalonians', '1 Timothy', '2 Timothy',
    'Titus', 'Philemon', 'Hebrews', 'James', '1 Peter',
    '2 Peter', '1 John', '2 John', '3 John', 'Jude',
    'Revelation',
]
with sqlite3.connect(BOOKS_DB) as _conn:
    _osis_in_canon_order = [r[0] for r in
        _conn.execute("SELECT osis_id FROM books ORDER BY canon_order")]
FULL_NAME_TO_OSIS = dict(zip(_FULL_NAMES_IN_CANONICAL_ORDER, _osis_in_canon_order))

# ------------------------------------------------------------- column layout

COL_HEB_SORT      = 0
COL_GRK_SORT      = 1
COL_BSB_SORT      = 2
COL_VERSE_NUM     = 3
COL_LANGUAGE      = 4
COL_SOURCE_TEXT   = 5
# COL 6 is the apparatus-annotated WLC/Nestle text ({TR} etc.) — deliberately
# not modeled; see DEVELOPMENT.md.
COL_TRANSLIT      = 7
COL_PARSING_SHORT = 8
COL_PARSING_FULL  = 9
COL_STR_HEB       = 10
COL_STR_GRK       = 11
COL_VERSE_ID      = 12
COL_HDG           = 13
COL_CROSSREF      = 14
COL_PAR           = 15
COL_SPACE         = 16
COL_BEGQ          = 17
COL_BSB_VERSION   = 18
COL_PNC           = 19
COL_ENDQ          = 20
COL_FOOTNOTES     = 21
COL_END_TEXT      = 22

_LANGUAGE_MAP = {'Hebrew': 'H', 'Aramaic': 'A', 'Greek': 'G'}
_VERSE_ID_RE  = re.compile(r'^(.+?)\s+(\d+):(\d+)$')

# The BSB version column has occasional typographical inconsistencies of its
# own — e.g. Exodus 12:42's "[is to be a vigil ]" (space before the closing
# bracket) and Leviticus 7:9's "Likewise , every" (space before the comma).
# Not a pipeline bug, just the source data; cleaned up on the way in.
_SPACE_BEFORE_PUNCT_RE = re.compile(r'\s+([,.;:!?)\]’”])')
_INTERNAL_WHITESPACE_RE = re.compile(r'\s{2,}')


def _normalize_english(text: str) -> str:
    text = _SPACE_BEFORE_PUNCT_RE.sub(r'\1', text)
    return _INTERNAL_WHITESPACE_RE.sub(' ', text)


# Eleven rows carry the properly-spaced ". . ." continuation marker glued to
# real content in the same cell, e.g. Numbers 26:18's raw BSB version is
# " . . . 40,500 " — not the exact-match ". . ." that gets classified as a
# continuation row, so it falls through to plain text with the marker still
# embedded. Confirmed against the actual published BSB text row by row: these
# eleven have no ellipsis in the real text (the marker is a leftover
# artifact, not content) and should just become their trailing content
# ("40,500"). Ephesians 3:14 looks similar at a glance but is NOT one of
# these — its raw value is " ... for this reason " (a bare, unspaced "...",
# a completely different string), and it genuinely does have an ellipsis in
# the published text (Paul's sentence resuming after the digression since
# 3:1), so it's deliberately excluded and left untouched.
#
# This has to run *before* _normalize_english(): that function's
# space-before-punctuation cleanup would otherwise match the spaces inside
# ". . ." too (each "space then period" looks identical to "word ." needing
# its space removed) and mangle the marker into "..." instead of stripping
# it — which is exactly how this was first miscategorized as a bare-ellipsis
# bug instead of a leftover-marker bug.
#
# Keyed by bsb_sort since that's this file's own stable identifier; if
# bsb_tables.tsv is ever refreshed, re-verify this list rather than trusting
# it blindly.
_ELLIPSIS_ARTIFACT_BSB_SORTS = {
    106223, 106302, 106356, 106397, 106531,  # Numbers 26 census verses
    106590, 106672, 106712, 106786, 106841,
    638979,  # John 21:7
}
_SPACED_ELLIPSIS_PREFIX_RE = re.compile(r'^\.\s*\.\s*\.\s*')


def _strip_ellipsis_artifact(bsb_sort: int, text: str) -> str:
    if bsb_sort in _ELLIPSIS_ARTIFACT_BSB_SORTS:
        return _SPACED_ELLIPSIS_PREFIX_RE.sub('', text)
    return text

# heading and crossref are verse-level facts, not token-level ones — moved
# to their own `verses` table rather than living on whichever token happens
# to be first in bsb_sort order (that "first" token isn't meaningful once
# source_sort ordering is used, e.g. forward interlinear). Verified against
# the real file: all 1328 non-null Crossref values sit on the verse's first
# bsb_sort row with zero exceptions once 3 known mis-column rows are
# corrected — those 3 (BSB Sort 392396, 457348, 560983) hold Par-shaped
# content ("<p class=|...|>") instead of a real cross-reference
# ("<br /><span class=|cross|>..."), and belong on that token's own
# par_class, not on the verse's crossref.
_CROSSREF_MISFILED_PREFIX = '<p class='

_INSERT_COLUMNS = [
    'bsb_sort', 'verse_id', 'source_sort', 'language',
    'source_text', 'translit', 'strongs', 'parsing_short', 'parsing_full',
    'gloss_type', 'english', 'parent_id',
    'beg_quote', 'end_quote', 'punctuation', 'space',
    'suffix_html', 'par_class', 'footnote',
]

DDL = """
CREATE TABLE verses (
    verse_id INTEGER PRIMARY KEY,  -- bsb_tables.tsv's own "Verse" column
    book     TEXT NOT NULL,
    chapter  INTEGER NOT NULL,
    verse    INTEGER NOT NULL,
    heading  TEXT,
    crossref TEXT
);
CREATE INDEX verses_book ON verses (book, verse_id);

CREATE TABLE tokens (
    bsb_sort      INTEGER PRIMARY KEY,
    verse_id      INTEGER NOT NULL REFERENCES verses(verse_id),
    source_sort   REAL NOT NULL,
    language      TEXT NOT NULL CHECK(language IN ('H','A','G')),
    source_text   TEXT NOT NULL,
    translit      TEXT,
    strongs       TEXT,
    parsing_short TEXT,
    parsing_full  TEXT,
    gloss_type    TEXT NOT NULL
                  CHECK(gloss_type IN ('text','untranslated',
                                       'continuation_after','continuation_before')),
    english       TEXT,
    parent_id     INTEGER REFERENCES tokens(bsb_sort),
    beg_quote     TEXT,
    end_quote     TEXT,
    punctuation   TEXT,
    space         TEXT,
    suffix_html   TEXT,
    par_class     TEXT,
    footnote      TEXT
);
CREATE INDEX tokens_verse      ON tokens (verse_id, bsb_sort);
CREATE INDEX tokens_source_sort ON tokens (source_sort);
"""

_INSERT_SQL = (
    "INSERT INTO tokens (" + ", ".join(_INSERT_COLUMNS) + ") VALUES ("
    + ", ".join("?" for _ in _INSERT_COLUMNS) + ")"
)
_INSERT_VERSE_SQL = "INSERT INTO verses (verse_id, book, chapter, verse, heading, crossref) VALUES (?, ?, ?, ?, ?, ?)"


def import_bsb_table(tsv_path: Path, db_path: Path, batch_size: int = 5000) -> None:
    """Parse bsb_tables.tsv into a fresh SQLite database at db_path.

    Single forward pass over the file (already in bsb_sort order). Rows with
    gloss_type 'continuation_before' ("vvv") are buffered until the next
    'text'/'untranslated' owner is found; rows with gloss_type
    'continuation_after' (". . .") resolve immediately against the current
    owner — except when a vvv buffer is already open, in which case the
    ". . ." is actually part of that same forward-looking group (confirmed
    against 7 real cases in the file where "vvv" is immediately followed by
    ". . ." with no owner between them).
    """
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)
    cur = conn.cursor()

    batch: list = []

    def flush():
        if batch:
            cur.executemany(_INSERT_SQL, batch)
            batch.clear()

    def row_tuple(params: dict) -> tuple:
        return tuple(params[c] for c in _INSERT_COLUMNS)

    pending_vvv: list = []
    current_owner_bsb_sort = None
    verse_id = None
    prev_verse_col = None
    discarded_at_boundary = 0
    misfiled_crossref_count = 0
    row_count = 0

    with open(tsv_path, encoding='utf-8', newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        next(reader)  # header — not inspected; see module docstring

        for cols in reader:
            src_text = cols[COL_SOURCE_TEXT].strip()
            if not src_text:
                discarded_at_boundary += len(pending_vvv)
                pending_vvv = []
                current_owner_bsb_sort = None
                continue

            verse_col   = cols[COL_VERSE_NUM].strip()
            par_correction = None
            if verse_col != prev_verse_col:
                prev_verse_col = verse_col
                verse_id = int(verse_col)
                vid = cols[COL_VERSE_ID].strip()
                if vid:
                    m = _VERSE_ID_RE.match(vid)
                    book_name, chap_s, verse_s = m.groups()
                    book, chapter, verse = FULL_NAME_TO_OSIS[book_name], int(chap_s), int(verse_s)

                    heading  = cols[COL_HDG] or None
                    crossref = cols[COL_CROSSREF] or None
                    if crossref and crossref.startswith(_CROSSREF_MISFILED_PREFIX):
                        # Par-shaped content sitting in the Crossref column —
                        # redirect it onto this row's own par_class instead.
                        par_correction = crossref
                        crossref = None
                        misfiled_crossref_count += 1
                    cur.execute(_INSERT_VERSE_SQL, (verse_id, book, chapter, verse, heading, crossref))
                discarded_at_boundary += len(pending_vvv)
                pending_vvv = []
                current_owner_bsb_sort = None

            language = _LANGUAGE_MAP.get(cols[COL_LANGUAGE])
            if language is None:
                continue  # stray/garbage Language value; only seen paired with blank source text

            bsb_sort    = int(cols[COL_BSB_SORT])
            source_sort = float(cols[COL_GRK_SORT]) if language == 'G' else float(cols[COL_HEB_SORT])
            strongs     = (cols[COL_STR_GRK] if language == 'G' else cols[COL_STR_HEB]).strip() or None

            bsb_version = cols[COL_BSB_VERSION].strip()
            extra_punctuation = None
            if bsb_version == '-':
                gloss_type, english = 'untranslated', None
            elif bsb_version == '. . .':
                gloss_type, english = 'continuation_after', None
            elif bsb_version == 'vvv':
                gloss_type, english = 'continuation_before', None
            else:
                bsb_version = _strip_ellipsis_artifact(bsb_sort, bsb_version)
                english = _normalize_english(bsb_version)
                if english and not any(c.isalnum() for c in english):
                    # John 21:7's stripped residue is bare ")" -- pure
                    # punctuation with no translated content. Left in
                    # `english`, TableComposer's merge routing (which only
                    # looks at the punctuation/end_quote *fields*, not raw
                    # text) wouldn't know to attach it to the previous word;
                    # moving it to `punctuation` lets that logic work as designed.
                    gloss_type, english, extra_punctuation = 'untranslated', None, english
                else:
                    gloss_type = 'text'

            params = dict(
                bsb_sort=bsb_sort, verse_id=verse_id,
                source_sort=source_sort, language=language,
                source_text=cols[COL_SOURCE_TEXT],
                translit=cols[COL_TRANSLIT] or None,
                strongs=strongs,
                parsing_short=cols[COL_PARSING_SHORT] or None,
                parsing_full=cols[COL_PARSING_FULL] or None,
                gloss_type=gloss_type,
                english=english,
                parent_id=None,
                beg_quote=cols[COL_BEGQ] or None,
                end_quote=cols[COL_ENDQ] or None,
                punctuation=extra_punctuation or cols[COL_PNC] or None,
                space=cols[COL_SPACE] or None,
                suffix_html=cols[COL_END_TEXT] or None,
                par_class=par_correction or cols[COL_PAR] or None,
                footnote=cols[COL_FOOTNOTES] or None,
            )

            if gloss_type in ('text', 'untranslated'):
                for pending in pending_vvv:
                    pending['parent_id'] = bsb_sort
                    batch.append(row_tuple(pending))
                pending_vvv = []
                current_owner_bsb_sort = bsb_sort
                batch.append(row_tuple(params))
            elif gloss_type == 'continuation_before':
                pending_vvv.append(params)
            elif gloss_type == 'continuation_after':
                if pending_vvv:
                    pending_vvv.append(params)
                else:
                    params['parent_id'] = current_owner_bsb_sort
                    batch.append(row_tuple(params))

            row_count += 1
            if len(batch) >= batch_size:
                flush()

        discarded_at_boundary += len(pending_vvv)  # trailing, at EOF

    flush()
    conn.commit()
    conn.close()

    print(f"Imported {row_count:,} rows from {tsv_path.name} to {db_path}")
    if misfiled_crossref_count:
        print(f"Redirected {misfiled_crossref_count} misfiled Crossref value(s) "
              f"(Par-shaped content) onto their own token's par_class.")
    if discarded_at_boundary:
        print(f"Warning: {discarded_at_boundary} 'vvv' row(s) discarded unresolved "
              f"at a verse/blank-row boundary — unexpected, worth investigating.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE,
                         help=f"Path to bsb_tables.tsv (default: {DEFAULT_SOURCE})")
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                         help=f"Output SQLite path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    import_bsb_table(args.source, args.output)
