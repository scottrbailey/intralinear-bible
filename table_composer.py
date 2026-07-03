"""
table_composer.py

TableComposer reads a `tokens` table (built once via
utils/import_bsb_table.py) and yields (osis_ref, [AlignedToken], header,
xrefs) — the same shape AlignmentComposer produces from a live
source/alignment/target join, so writers and formatters can't tell which
Composer produced the stream.

See docs/DEVELOPMENT.md for the schema rationale.
"""

import re
import sqlite3
from pathlib import Path

from composer import Composer
from models import AlignedToken, MappingDirection, SourceToken, SourceWord

_STRONGS_RE = re.compile(r'^0*(\d+)[a-z]*$')


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
    text (only the owner has one), and trailing punctuation/quote. Some of
    these fields carry their own accidental padding (Exodus 18:4's beg_quote
    is " “", not "“" — confirmed against the raw data), so the whole
    assembled result is stripped rather than trusting any one field's edges.
    Punctuation/quotes still glue on with no inserted space between parts —
    confirmed against cases where a comma lands on a continuation row, not
    the owner (e.g. Genesis 40:1).
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
    return ''.join(parts).strip()


class TableComposer(Composer):
    """Reads a token table built by utils/import_bsb_table.py and yields the
    same (osis_ref, [AlignedToken], header, xrefs) shape as AlignmentComposer.

    Notes on fidelity to AlignmentComposer's contract:
      - xrefs: verses.crossref is already the plain "Joh 1:1-5; Heb 11:1-3"
        shape (converted from the file's raw Crossref HTML at import time,
        see utils/import_bsb_table.py) — same shape AlignmentComposer's own
        bsb_xrefs.json uses, wrapped here as {'1': text} to match the
        {key: text} dict both Composers yield (bsb_tables.tsv never has more
        than one cross-reference group per verse, confirmed on all 1,325).
      - Supplied-word brackets ("[Jesus] answered") are preserved as-is;
        VerseFormatter.transform_english() decides whether to strip, wrap, or
        keep them at render time (same as AlignmentComposer's target text,
        which happens to have none, so this is a no-op there).
      - suffix_html/par_class (trailing markup/content, poetry indent, etc.)
        are preserved in the tokens table but not yet surfaced on
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

        # verses is small (~31K rows) — load it whole rather than querying
        # per-verse, since heading/book/chapter/verse are facts about the
        # verse (looked up by verse_id), not about whichever token happens
        # to be first in a given sort order.
        cur.execute("SELECT verse_id, book, chapter, verse, heading, crossref FROM verses")
        verse_info = {r['verse_id']: r for r in cur}

        where, params = "", ()
        if self._books_filter:
            placeholders = ','.join('?' for _ in self._books_filter)
            where  = (f"WHERE verse_id IN (SELECT verse_id FROM verses "
                      f"WHERE book IN ({placeholders}))")
            params = tuple(self._books_filter)

        cur.execute(f"SELECT * FROM tokens {where} ORDER BY bsb_sort", params)

        current_verse_id, verse_rows = None, []
        for row in cur:
            if row['verse_id'] != current_verse_id:
                if verse_rows:
                    yield self._build_verse(verse_info[current_verse_id], verse_rows)
                current_verse_id, verse_rows = row['verse_id'], []
            verse_rows.append(row)
        if verse_rows:
            yield self._build_verse(verse_info[current_verse_id], verse_rows)

        conn.close()

    @staticmethod
    def _build_verse(verse_info, rows):
        osis_ref = f"{verse_info['book']}.{verse_info['chapter']}.{verse_info['verse']}"
        header   = verse_info['heading']
        xrefs    = {'1': verse_info['crossref']} if verse_info['crossref'] else {}

        groups, order = {}, []
        for row in rows:
            owner = row['bsb_sort'] if row['parent_id'] is None else row['parent_id']
            if owner not in groups:
                groups[owner] = []
                order.append(owner)
            groups[owner].append(row)

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
        # forward onto the next one. _assemble_group_text() strips every
        # group's result, so a non-empty combined_text is always whitespace-
        # clean at both ends and just needs one normal space after it.
        tokens = []
        pending_prefix, pending_words, pending_notes = '', [], []

        def needs_space_after(text: str) -> bool:
            return bool(text)

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

        return osis_ref, tokens, header, xrefs
