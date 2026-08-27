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

_STRONGS_RE = re.compile(r'^0*(\d+)([a-z]*)$')

# Par column marks a new paragraph only on that paragraph's first row (same
# start-of-run convention as Hdg/Crossref) — e.g. Psalm 3:1's "A Psalm" row
# carries '<p class=|pshdg|>', but every row after it up through "Absalom"
# carries no par_class at all, until "O LORD" (the real, counted verse 1
# text) starts a new '<p class=|indent1stline|>' paragraph. So this is state
# to track forward across the whole row stream — a paragraph routinely spans
# a verse boundary (confirmed: Matthew 5:11-16's red-letter paragraphs), so
# iter_verses() threads it across successive _build_verse() calls rather
# than resetting it fresh for each verse.
_PAR_CLASS_RE = re.compile(r'class=\|(\w+)\|')


def _extract_par_class(raw: str | None) -> str | None:
    if not raw:
        return None
    m = _PAR_CLASS_RE.search(raw)
    return m.group(1) if m else None


# Red-letter state is orthogonal to par_class, not a value of it — it can
# ride along with any paragraph type ('<p class=|reg|><span class=|red|>'
# marks a normally-styled paragraph whose text is *also* red) and fuses
# into indent-level class names entirely ('indentred1', 'indentred2',
# 'indent1stlinered', 'tab1stlinered'). So it's tracked as its own forward
# boolean, flipped by whether the *raw* marker text mentions "red" at all
# (regardless of what other class(es) it names), not by the single
# extracted par_class above. Confirmed against Matthew 4:4: red turns on at
# '<span class=|red|>' ("It is written"), rides through the
# indentred1/indentred2 quoted poetry, and turns back off at the next
# marker that doesn't mention red ('<p class=|reg|>', "Then the devil...").
def _extract_is_red(raw: str | None) -> bool | None:
    if not raw:
        return None
    return 'red' in raw


def _prefixed_strongs(bare: str | None, language: str) -> str:
    """Match AlignmentComposer's Strong's number convention: letter prefix, no leading zeros.

    macula-hebrew/macula-greek use trailing letters ('0871a', '2050b') to tag
    grammatical morphemes (prepositions, the article, conjunctions) with a
    pseudo-Strong's slot borrowed from an unrelated real entry's number —
    confirmed against the classic Strong's dictionary: '0871a' (the bare
    preposition bet) strips to H871, which is really 'Atharim' (Num 21:1);
    '2050b' (the conjunction waw) strips to H2050, which is really 'imagine
    mischief' (Ps 62:3). Stripping the letter and linking anyway would point
    readers at the wrong, unrelated dictionary entry, so a lettered number is
    suppressed entirely rather than resolved.
    """
    if not bare:
        return ''
    m = _STRONGS_RE.match(bare)
    if not m or m.group(2):
        return ''
    prefix = 'H' if language in ('H', 'A') else 'G'
    return prefix + m.group(1)


_PASEQ = '׀'  # HEBREW PUNCTUATION PASEQ — looks like an ASCII '|' but is a
# real Masoretic cantillation mark, not markup (confirmed: 2,268 tokens carry
# it, always glued onto the end of a real word, never standalone). Kept in
# the stored source_text — it's authentic text, not an error — but stripped
# from what SourceWord actually displays: it renders 1.5-2x the surrounding
# Hebrew's size in both e-Sword and MySword, a target-font/glyph problem
# with no known fix, not something worth destroying the underlying data over.


def _load_lemma_lookup(conn: sqlite3.Connection) -> dict:
    """{'H7225': 'reshit', 'G26': 'agape', ...} from strongs_lemma (see
    utils/import_lemma_table.py) -- keyed to match _prefixed_strongs()'s own
    output exactly (prefix + bare digits, no leading zeros, Aramaic already
    folded to 'H'), so a token's own .strongs value is a direct lookup key
    with no reformatting needed. Empty dict (not an error) if strongs_lemma
    doesn't exist yet -- an older bsb_tables.db built before that table was
    added, or one nobody's run utils/import_lemma_table.py against -- so
    every token's lemma_translit just stays '' rather than the whole
    pipeline failing over one optional table.

    Compound-headword Strong's numbers (see _find_compound_strongs()) are
    dropped from the returned dict entirely, so every consumer's existing
    `sw.stem.lemma_translit or word_xlit` fallback naturally shows each
    token's own real form instead -- no changes needed anywhere else.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strongs_lemma'"
    ).fetchone():
        return {}
    lookup = {f"{lang}{strongs}": xlit
              for strongs, lang, xlit in conn.execute(
                  "SELECT strongs, lang, transliteration FROM strongs_lemma")}
    for key in _find_compound_strongs(conn, lookup):
        lookup.pop(key, None)
    return lookup


def _find_compound_strongs(conn: sqlite3.Connection, lemma_lookup: dict) -> set:
    """Strong's numbers (in _load_lemma_lookup's own 'H7436' key format)
    whose strongs_lemma dictionary transliteration is itself a
    multi-word/hyphenated compound headword that matches neither member's
    own transliteration in any real occurrence -- the pattern behind 1 Sam
    1:1's "Ramathaim-zophim" showing the same full compound name as the
    lemma line on both of its source tokens (see
    docs/BSB_TABLES_SOURCE_ERRORS.md item 5, and
    utils/scan_compound_strongs.py, which investigated and validated this
    exact signature offline against 588 real occurrences).

    Confirmed against the real BIB+ app that ordinary inflected-vs-lemma
    divergence -- the same word repeated (Gen 36:8's "Esau ... Esau"), or
    one name in two different grammatical forms (Num 33:9's "Elim ...
    Elim") -- is NOT this pattern and must not be suppressed:
    both of those have single-word lemmas, so the "multi-word/hyphenated"
    condition below already excludes them. Only a dictionary headword
    that's structurally a fused two-root compound (Bethel, Beersheba,
    Ben-hadad, Kiriath-jearim, Melchizedek, ...) matches.

    Groups are found the same way import_bsb_table.py's own 'vvv'/'. . .'
    continuation markers link them: COALESCE(parent_id, bsb_sort) as the
    owning group key. Computed once per build (one pass over `tokens`,
    same order of cost as loading `verses`) rather than hand-maintained --
    real data turned up on the order of 150-200 distinct Strong's numbers
    behind those 588 occurrences, far too many to keep as a literal list
    the way LEMMA_SUPPRESSED_STRONGS does for its 3 hand-picked entries
    (a different, unrelated phenomenon -- a lemma that's a real single
    word but unhelpful for a few extremely common Greek function words,
    not a compound headword).
    """
    cur = conn.execute("""
        SELECT bsb_sort, strongs, translit, language,
               COALESCE(parent_id, bsb_sort) AS owner
        FROM tokens
        WHERE strongs IS NOT NULL
        ORDER BY bsb_sort
    """)
    groups: dict = {}
    for row in cur:
        groups.setdefault(row['owner'], []).append(row)

    suppressed = set()
    for members in groups.values():
        if len(members) < 2:
            continue
        keys = {_prefixed_strongs(m['strongs'], m['language']) for m in members}
        if len(keys) != 1:
            continue
        key = next(iter(keys))
        if not key:
            continue
        lemma = lemma_lookup.get(key)
        if not lemma or (' ' not in lemma and '-' not in lemma):
            continue
        member_translits = {m['translit'] for m in members if m['translit']}
        if lemma in member_translits:
            continue
        suppressed.add(key)
    return suppressed


def _to_source_word(row: sqlite3.Row, lemma_lookup: dict) -> SourceWord:
    parsing_full = row['parsing_full'] or ''
    is_proper    = 'proper' in parsing_full.lower()
    source_text  = row['source_text'].replace(_PASEQ, '')
    strongs      = _prefixed_strongs(row['strongs'], row['language'])
    token = SourceToken(
        id=str(row['bsb_sort']),
        text=source_text,
        strongs=strongs,
        gloss=row['english'] or '',
        token_class=row['parsing_short'] or '',
        pos='',
        noun_type='proper' if is_proper else '',
        morph=row['morph'] or '',
        lang=row['language'],
        after=' ',
        translit=row['translit'] or '',
        lemma_translit=lemma_lookup.get(strongs, ''),
    )
    return SourceWord(
        tokens=[token], stem=token, text=source_text,
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

    direction=SOURCE_TO_TARGET (forward interlinear) is a genuinely
    different build, not a reordering of the above: see
    _build_verse_source_order() for what it deliberately drops (par_class,
    is_red) and accepts as a known limit (occasional quote/punctuation
    misplacement) to get a per-source-token, ungrouped display.
    """

    def __init__(self, db_path: Path, config: dict = None,
                 direction: MappingDirection = MappingDirection.TARGET_TO_SOURCE):
        self.db_path          = Path(db_path)
        self.config           = config or {}
        self.direction        = direction
        self._books_filter    = self.config.get('books')
        self._chapters_filter = self.config.get('chapters')  # optional {book: chapter}

    def iter_verses(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        lemma_lookup = _load_lemma_lookup(conn)

        # verses is small (~31K rows) — load it whole rather than querying
        # per-verse, since heading/book/chapter/verse are facts about the
        # verse (looked up by verse_id), not about whichever token happens
        # to be first in a given sort order.
        cur.execute("SELECT verse_id, book, chapter, verse, heading, crossref FROM verses")
        verse_info = {r['verse_id']: r for r in cur}

        conditions, params = [], []
        if self._books_filter:
            if self._chapters_filter:
                # Per-book chapter cap (e.g. {'Gen': 1, 'Matt': 5}) -- chapters
                # 1 through N inclusive, not just chapter N, since e-Sword's
                # own chapter picker won't let you navigate into a book at
                # all if chapter 1 is missing (confirmed on-device). A book
                # with no entry here shows all of its chapters.
                book_conds = []
                for book in self._books_filter:
                    chapter = self._chapters_filter.get(book)
                    if chapter:
                        book_conds.append("(book = ? AND chapter <= ?)")
                        params.extend([book, chapter])
                    else:
                        book_conds.append("(book = ?)")
                        params.append(book)
                conditions.append(f"({' OR '.join(book_conds)})")
            else:
                placeholders = ','.join('?' for _ in self._books_filter)
                conditions.append(f"book IN ({placeholders})")
                params.extend(self._books_filter)

        where = ""
        if conditions:
            where = (f"WHERE verse_id IN (SELECT verse_id FROM verses "
                     f"WHERE {' AND '.join(conditions)})")
        params = tuple(params)

        if self.direction == MappingDirection.SOURCE_TO_TARGET:
            # verse_id is an explicit sort key here (unlike the bsb_sort path
            # below) because source_sort only orders tokens *within* one
            # verse — it's the source language's own word position in that
            # verse, not a whole-Bible ordinal the way bsb_sort is, so
            # nothing else keeps same-verse rows contiguous.
            cur.execute(f"SELECT * FROM tokens {where} ORDER BY verse_id, source_sort", params)
            current_verse_id, verse_rows = None, []
            for row in cur:
                if row['verse_id'] != current_verse_id:
                    if verse_rows:
                        yield self._build_verse_source_order(
                            verse_info[current_verse_id], verse_rows, lemma_lookup)
                    current_verse_id, verse_rows = row['verse_id'], []
                verse_rows.append(row)
            if verse_rows:
                yield self._build_verse_source_order(
                    verse_info[current_verse_id], verse_rows, lemma_lookup)
            conn.close()
            return

        cur.execute(f"SELECT * FROM tokens {where} ORDER BY bsb_sort", params)

        # par_class/is_red only mark a paragraph's first row (see
        # _build_verse's docstring) — a paragraph routinely spans a verse
        # boundary (confirmed: Matthew 5:11's "Blessed are you..." opens
        # '<p class=|red|>' and keeps going through 5:12's "Rejoice..." with
        # no marker of its own at all, through 5:14's next '<p class=|red|>'
        # paragraph), so this state has to carry across _build_verse() calls,
        # not reset per verse. Safe to run continuously across book
        # boundaries too — every book's first token carries its own explicit
        # marker (confirmed for Genesis/Matthew/Mark/Psalms), so there's
        # never a real gap for stale state to leak through.
        current_par_class, current_is_red = None, False

        current_verse_id, verse_rows = None, []
        for row in cur:
            if row['verse_id'] != current_verse_id:
                if verse_rows:
                    verse, current_par_class, current_is_red = self._build_verse(
                        verse_info[current_verse_id], verse_rows,
                        current_par_class, current_is_red, lemma_lookup,
                    )
                    yield verse
                current_verse_id, verse_rows = row['verse_id'], []
            verse_rows.append(row)
        if verse_rows:
            verse, current_par_class, current_is_red = self._build_verse(
                verse_info[current_verse_id], verse_rows,
                current_par_class, current_is_red, lemma_lookup,
            )
            yield verse

        conn.close()

    @staticmethod
    def _build_verse(verse_info, rows, current_par_class, current_is_red, lemma_lookup):
        osis_ref = f"{verse_info['book']}.{verse_info['chapter']}.{verse_info['verse']}"
        header   = verse_info['heading']
        xrefs    = {'1': verse_info['crossref']} if verse_info['crossref'] else {}

        groups, order = {}, []
        par_class_at, is_red_at = {}, {}
        for row in rows:
            owner = row['bsb_sort'] if row['parent_id'] is None else row['parent_id']
            if owner not in groups:
                groups[owner] = []
                order.append(owner)
            groups[owner].append(row)

            extracted = _extract_par_class(row['par_class'])
            if extracted is not None:
                current_par_class = extracted
            par_class_at[row['bsb_sort']] = current_par_class

            extracted_red = _extract_is_red(row['par_class'])
            if extracted_red is not None:
                current_is_red = extracted_red
            is_red_at[row['bsb_sort']] = current_is_red

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
            source_words = [_to_source_word(r, lemma_lookup) for r in members]

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
                par_class=par_class_at[owner],
                is_red=is_red_at[owner],
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
                                            source_words=pending_words, notes=pending_notes,
                                            par_class=current_par_class, is_red=current_is_red))

        return (osis_ref, tokens, header, xrefs), current_par_class, current_is_red

    @staticmethod
    def _build_verse_source_order(verse_info, rows, lemma_lookup) -> tuple:
        """Forward-interlinear build: one AlignedToken per source token, in
        the source language's own reading order (source_sort) rather than
        grouped by English alignment (see _build_verse). Deliberately not a
        source-order reordering of that grouped model:

        - No par_class/is_red. Both are forward state tracked walking rows
          in bsb_sort (document) order — this path doesn't walk that order,
          and re-deriving them here isn't worth it for what's fundamentally
          a word-order/annotation display, not a natural-reading one.
        - beg_quote/punctuation/english/end_quote are rendered exactly as
          stored on each row, no cross-row reassembly. A multi-word BSB
          group's decorations don't all sit on the same row (e.g. a comma
          landing on a continuation row, not the owner — see
          _assemble_group_text()'s docstring), and that row's position in
          source_sort order isn't guaranteed to match its position in the
          group's bsb_sort order (the Gen 5:23 "365" scattering case), so a
          quote mark can occasionally land visually out of place relative
          to the phrase it belongs to. Accepted as a known limit of this
          direction rather than solved — the source word content itself,
          the point of this mode, is unaffected either way.
        """
        osis_ref = f"{verse_info['book']}.{verse_info['chapter']}.{verse_info['verse']}"
        header   = verse_info['heading']
        xrefs    = {'1': verse_info['crossref']} if verse_info['crossref'] else {}

        tokens = []
        for row in rows:
            parts = []
            if row['beg_quote']:
                parts.append(row['beg_quote'])
            if row['english']:
                parts.append(row['english'].strip())
            if row['punctuation']:
                parts.append(row['punctuation'])
            if row['end_quote']:
                parts.append(row['end_quote'])
            english = ''.join(parts).strip()

            notes = [{'noteId': f"F{row['bsb_sort']}", 'text': row['footnote']}] if row['footnote'] else []

            tokens.append(AlignedToken(
                english=english,
                skip_space_after=False,
                source_words=[_to_source_word(row, lemma_lookup)],
                notes=notes,
            ))

        return osis_ref, tokens, header, xrefs
