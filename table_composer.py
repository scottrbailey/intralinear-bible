"""
table_composer.py

Parses bsb_tables.tsv into a normalized SQLite `tokens` table, and
TableComposer reads that table back out as (osis_ref, [AlignedToken],
header, xrefs) — the same shape AlignmentComposer produces from a live
source/alignment/target join, so writers and formatters can't tell which
Composer produced the stream.

See docs/DEVELOPMENT.md for the column-mapping rationale (why positional
indexing instead of csv.DictReader, the gloss_type states, etc.) — this
schema was worked out against the real file, not guessed.
"""

import csv
import re
import sqlite3
from pathlib import Path

from bible_books import FULL_NAME_TO_OSIS
from composer import Composer
from models import AlignedToken, MappingDirection, SourceToken, SourceWord

# ------------------------------------------------------------- column layout
#
# bsb_tables.tsv has two columns both literally named "Parsing" (short code
# and full description), and one header with embedded spaces (" BSB version
# "). csv.DictReader collapses duplicate header names to the last one seen —
# confirmed empirically to silently drop the short-code column — so this
# module indexes columns positionally instead of trusting header text.

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
_STRONGS_RE   = re.compile(r'^0*(\d+)[a-z]*$')

_INSERT_COLUMNS = [
    'bsb_sort', 'book', 'chapter', 'verse', 'source_sort', 'language',
    'source_text', 'translit', 'strongs', 'parsing_short', 'parsing_full',
    'gloss_type', 'english', 'parent_id',
    'beg_quote', 'end_quote', 'punctuation', 'space',
    'heading', 'prefix_html', 'suffix_html', 'par_class', 'footnote',
]

DDL = f"""
CREATE TABLE tokens (
    bsb_sort      INTEGER PRIMARY KEY,
    book          TEXT NOT NULL,
    chapter       INTEGER NOT NULL,
    verse         INTEGER NOT NULL,
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
    heading       TEXT,
    prefix_html   TEXT,
    suffix_html   TEXT,
    par_class     TEXT,
    footnote      TEXT
);
CREATE INDEX tokens_book       ON tokens (book, bsb_sort);
CREATE INDEX tokens_source_sort ON tokens (source_sort);
"""

_INSERT_SQL = (
    "INSERT INTO tokens (" + ", ".join(_INSERT_COLUMNS) + ") VALUES ("
    + ", ".join("?" for _ in _INSERT_COLUMNS) + ")"
)


# ================================================================= import

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
    book = chapter = verse = None
    prev_verse_col = None
    discarded_at_boundary = 0

    with open(tsv_path, encoding='utf-8', newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        next(reader)  # header

        for cols in reader:
            src_text = cols[COL_SOURCE_TEXT].strip()
            if not src_text:
                discarded_at_boundary += len(pending_vvv)
                pending_vvv = []
                current_owner_bsb_sort = None
                continue

            verse_col = cols[COL_VERSE_NUM].strip()
            if verse_col != prev_verse_col:
                prev_verse_col = verse_col
                vid = cols[COL_VERSE_ID].strip()
                if vid:
                    m = _VERSE_ID_RE.match(vid)
                    book_name, chap_s, verse_s = m.groups()
                    book, chapter, verse = FULL_NAME_TO_OSIS[book_name], int(chap_s), int(verse_s)
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
            if bsb_version == '-':
                gloss_type, english = 'untranslated', None
            elif bsb_version == '. . .':
                gloss_type, english = 'continuation_after', None
            elif bsb_version == 'vvv':
                gloss_type, english = 'continuation_before', None
            else:
                gloss_type, english = 'text', bsb_version

            params = dict(
                bsb_sort=bsb_sort, book=book, chapter=chapter, verse=verse,
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
                punctuation=cols[COL_PNC] or None,
                space=cols[COL_SPACE] or None,
                heading=cols[COL_HDG] or None,
                prefix_html=cols[COL_CROSSREF] or None,
                suffix_html=cols[COL_END_TEXT] or None,
                par_class=cols[COL_PAR] or None,
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

            if len(batch) >= batch_size:
                flush()

        discarded_at_boundary += len(pending_vvv)  # trailing, at EOF

    flush()
    conn.commit()
    conn.close()

    if discarded_at_boundary:
        print(f"Warning: {discarded_at_boundary} 'vvv' row(s) discarded unresolved "
              f"at a verse/blank-row boundary — unexpected, worth investigating.")


# ================================================================ Composer

def _prefixed_strongs(bare: str | None, language: str) -> str:
    """Match AlignmentComposer's Strong's number convention: letter prefix, no leading zeros."""
    if not bare:
        return ''
    bare = _STRONGS_RE.sub(r'\1', bare)
    prefix = 'H' if language in ('H', 'A') else 'G'
    return prefix + bare


def _to_source_word(row: sqlite3.Row) -> SourceWord:
    parsing_full = row['parsing_full'] or ''
    is_proper    = 'proper' in parsing_full.lower()
    token = SourceToken(
        id=str(row['bsb_sort']),
        text=row['source_text'],
        strongs=_prefixed_strongs(row['strongs'], row['language']),
        gloss=row['english'] or '',
        token_class=row['parsing_short'] or '',
        pos='',
        noun_type='proper' if is_proper else '',
        morph=parsing_full,
        lang=row['language'],
        after=' ',
    )
    return SourceWord(
        tokens=[token], stem=token, text=row['source_text'],
        lang=row['language'], is_proper=is_proper,
    )


def _assemble_group_text(members: list, owner_row) -> str:
    """Build one group's display phrase from its member rows, in bsb_sort order.

    Each row can contribute a leading quote (beg_quote), the owner's English
    text (only the owner has one), and trailing punctuation/quote. Source
    values pad `english` with spaces for word separation, so that padding is
    stripped here and punctuation/quotes are glued on directly with no
    inserted space — confirmed against cases where a comma lands on a
    continuation row, not the owner (e.g. Genesis 40:1).
    """
    parts = []
    for row in members:
        if row['beg_quote']:
            parts.append(row['beg_quote'])
        if row is owner_row and row['english']:
            parts.append(row['english'].strip())
        if row['punctuation']:
            parts.append(row['punctuation'])
        if row['end_quote']:
            parts.append(row['end_quote'])
    return ''.join(parts)


class TableComposer(Composer):
    """Reads a token table built by import_bsb_table() and yields the same
    (osis_ref, [AlignedToken], header, xrefs) shape as AlignmentComposer.

    Notes on fidelity to AlignmentComposer's contract:
      - xrefs: bsb_tables.tsv carries no cross-reference data; always {}.
      - prefix_html/suffix_html/par_class (red-letter spans, poetry indent,
        etc.) are preserved in the tokens table but not yet surfaced on
        AlignedToken — no formatter consumes them yet, so they aren't wired
        through until one does.
      - The 'space' column's meaning is still unconfirmed (blank in every
        row seen so far); spacing between tokens uses AlignedToken's normal
        default (space after) rather than reading it.
    """

    def __init__(self, db_path: Path, config: dict = None,
                 direction: MappingDirection = MappingDirection.TARGET_TO_SOURCE):
        if direction == MappingDirection.SOURCE_TO_TARGET:
            # Groups are only guaranteed contiguous in bsb_sort order — the
            # Genesis 5:23 "365" group scatters non-monotonically in
            # source_sort order (Heb Sort 3003,3004,3002,3001,3000). Forward
            # interlinear needs a per-row (ungrouped) rendering strategy,
            # not this grouped one; deferred, same as AlignmentComposer.
            raise NotImplementedError(
                "Source-primary ordering not yet implemented for TableComposer "
                "(see class docstring / comment above)."
            )
        self.db_path        = Path(db_path)
        self.config         = config or {}
        self.direction       = direction
        self._books_filter  = self.config.get('books')

    def iter_verses(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        where, params = "", ()
        if self._books_filter:
            placeholders = ','.join('?' for _ in self._books_filter)
            where  = f"WHERE book IN ({placeholders})"
            params = tuple(self._books_filter)

        cur.execute(f"SELECT * FROM tokens {where} ORDER BY bsb_sort", params)

        current_key, verse_rows = None, []
        for row in cur:
            key = (row['book'], row['chapter'], row['verse'])
            if key != current_key:
                if verse_rows:
                    yield self._build_verse(current_key, verse_rows)
                current_key, verse_rows = key, []
            verse_rows.append(row)
        if verse_rows:
            yield self._build_verse(current_key, verse_rows)

        conn.close()

    @staticmethod
    def _build_verse(key, rows):
        book, chapter, verse = key
        osis_ref = f"{book}.{chapter}.{verse}"

        groups, order = {}, []
        for row in rows:
            owner = row['bsb_sort'] if row['parent_id'] is None else row['parent_id']
            if owner not in groups:
                groups[owner] = []
                order.append(owner)
            groups[owner].append(row)

        header = next((r['heading'] for r in rows if r['heading']), None)

        # An 'untranslated' word can still carry its own leading quote or
        # trailing punctuation (e.g. a comma-bearing pronoun BSB doesn't
        # translate), and consecutive untranslated words can appear with
        # nothing attached to any but the last of them (Gen 1:11's closing
        # "." and "”" land on the fourth of four straight untranslated
        # words). Left as their own AlignedTokens, these read as "he said ,"
        # or worse. So any group with no translated (alphanumeric) content —
        # empty or punctuation-only — is folded into a neighbor instead of
        # becoming its own token: trailing marks glue onto the previous real
        # token, everything else (leading marks, or nothing at all) carries
        # forward onto the next one. skip_space_after is always recomputed
        # from the token's actual trailing character rather than assumed,
        # since some punctuation values embed their own spacing (the file's
        # em-dash is stored as " — ", not "—").
        tokens = []
        pending_prefix, pending_words, pending_notes = '', [], []

        def needs_space_after(text: str) -> bool:
            return bool(text) and not text[-1].isspace()

        for owner in order:
            members   = groups[owner]
            owner_row = next(r for r in members if r['bsb_sort'] == owner)
            english   = _assemble_group_text(members, owner_row)
            notes     = [{'noteId': f"F{r['bsb_sort']}", 'text': r['footnote']}
                         for r in members if r['footnote']]
            source_words = [_to_source_word(r) for r in members]

            has_word  = any(c.isalnum() for c in english)
            has_trail = any(r['end_quote'] or r['punctuation'] for r in members)

            if not has_word and not (has_trail and tokens):
                pending_prefix += english
                pending_words.extend(source_words)
                pending_notes.extend(notes)
                continue

            combined_text  = pending_prefix + english
            combined_words = pending_words + source_words
            combined_notes = pending_notes + notes
            pending_prefix, pending_words, pending_notes = '', [], []

            if not has_word:
                prev = tokens[-1]
                prev.english += combined_text
                prev.source_words.extend(combined_words)
                prev.notes.extend(combined_notes)
                prev.skip_space_after = not needs_space_after(prev.english)
                continue

            tokens.append(AlignedToken(
                english=combined_text,
                skip_space_after=not needs_space_after(combined_text),
                source_words=combined_words,
                notes=combined_notes,
            ))

        if pending_prefix or pending_words or pending_notes:
            # leftover forward-pending content with nothing after it in the
            # verse (rare) — attach to the last token, or stand alone if the
            # whole verse turned out to be untranslated.
            if tokens:
                tokens[-1].english += pending_prefix
                tokens[-1].source_words.extend(pending_words)
                tokens[-1].notes.extend(pending_notes)
                tokens[-1].skip_space_after = not needs_space_after(tokens[-1].english)
            else:
                tokens.append(AlignedToken(english=pending_prefix, skip_space_after=True,
                                            source_words=pending_words, notes=pending_notes))

        return osis_ref, tokens, header, {}
