# TableComposer / bsb_tables Pipeline — Status

Where things stand on the `bsb_tables.tsv` → `tokens`/`verses`/`books` database
→ `TableComposer` pipeline, as an alternative to `AlignmentComposer`'s live
source/alignment/target join. See `DEVELOPMENT.md` for the general
architecture; this doc tracks the newer, still-settling parts.

## Working

- `utils/import_bsb_table.py` parses the full 754K-row file into
  `data/bsb_tables.db` (`tokens`, `verses` tables) in ~10s.
- `utils/build_books_table.py` builds `data/books.db`, replacing the old
  `bible_books.py`.
- `TableComposer` (in `table_composer.py`) reads the token table and yields
  the same `(osis_ref, tokens, header, xrefs)` shape `AlignmentComposer`
  does — confirmed by feeding it directly into real `ESwordWriter` +
  `ESwordIntralinearFormatter`/`ESwordStackedFormatter` instances and
  producing actual `.bbli` files (2,601 verses, Genesis + Matthew).
- Full-Bible pass (all 31,085 verses with real content): zero exceptions,
  zero double-spaces, zero stray supplied-word brackets.
- `main.py --composer table` (or `composer: table` in config.yaml) selects
  `TableComposer` through the normal CLI entry point — verified against real
  Genesis output via both the CLI flag and the config key (1,533 verses,
  matching the known count); the `alignment` path is unchanged.
- `composer` is now auto-detected when not set explicitly (config key or
  `--composer` flag): `table_db` existing on disk means `table`, otherwise
  `alignment` — so a built database is picked up with zero config changes,
  and the `sources` block (macula-hebrew/macula-greek/Alignments paths) is
  never touched in that case either. An explicit `composer` still forces
  one path over the other regardless of what's on disk.

## Resolved this session

1. **Verse headings.** `verses.heading` stays raw (the `<p class=|hdg|>`
   wrapper, possibly several segments back to back) — parsing is now owned
   by `VerseFormatter`, not the import step. `parse_headers(raw)` in
   `verse_formatter.py` splits the cell into `(class, text)` segments,
   drops empty-text and `pshdg` segments (confirmed misfiled `Par` content —
   see `BSB_TABLES_SOURCE_ERRORS.md`), decodes HTML entities, and strips
   `<br>` with no replacement (left to the caller's own tag structure). A
   plain string with no wrapper (`AlignmentComposer`'s `bsb_annotations.json`
   headers) is treated as a single `hdg` segment, so both Composers' header
   shapes go through the same code path.

   `VerseFormatter.render_header()`'s shared default policy: skip `hdg`/
   `suphdg` (the main section headings) entirely — MySword and e-Sword both
   have their own built-in pericope display, on by default and not
   suppressible from module data, so rendering those here doubles them up,
   and e-Sword has no way to make ours render above the verse the way its
   native pericopes do. Only `acrostic`/`ihdg`/`subhdg` — classes native
   pericopes don't cover — render, each wrapped in a same-named `<span>`
   rather than a hardcoded tag, styled by `_INLINE_HEADER_CSS` (block +
   italic, `acrostic` also centered) so each stays independently stylable.

5. **Supplied-word bracket/brace stripping.** Moved off `TableComposer`
   (which now always preserves `[brackets]`/`{braces}` verbatim) and onto
   `VerseFormatter.transform_english()`, controlled by two independent class
   vars: `bracket_replacement` for `[...]`, `brace_replacement` for `{...}`
   — each `('', '')` strips (default), `None` leaves untouched, any other
   `(prefix, suffix)` pair wraps the word instead (e.g. `('<i>', '</i>')`).
   Called from every `render_verse()` wherever `token.english` is emitted.
   The two markers turned out to be genuinely different categories, not one
   — see "Resolved" item 7 below — hence separate controls rather than one
   shared `bracket_replacement`.

6. **Cross-references.** `verses.crossref` is now parsed into the same plain
   `"Joh 1:1-5; Heb 11:1-3"` shape `AlignmentComposer`'s `bsb_xrefs.json`
   already uses (converted from the raw `<span class=|cross|>...` HTML at
   import time — book full names to our abbreviation via `books.db`, en-dash
   to hyphen), and `TableComposer.iter_verses()` yields it as `{'1': text}`.
   New `VerseFormatter` methods: `Reference` dataclass +
   module-level `parse_reference()` (format-agnostic parsing, handles exact
   verse refs, cross-chapter ranges, whole-chapter ranges, and book spans),
   `transform_reference(ref)` (one reference -> this format's link/tag
   syntax), `render_crossref(xrefs)` (a verse's full xref data -> this
   format's inline/note placement). Whole-chapter-range and book-span
   references (verified: 1 cross-chapter range, 4 whole-chapter ranges, 1
   book span, out of 2,033 total `<a>` labels) have no single verse target —
   MySword's `<RX>` tag anchors these to the range's first chapter (verse 1)
   while still *displaying* the full range as the label, since RX supports a
   separate label/target; e-Sword's `<ref>` tag doesn't, so those render as
   plain non-linked text instead of risking a broken/absurd native-parsed
   link (e-Sword's own `esword_writer.py` note-table building was updated to
   use `transform_reference()` too, replacing its old blind `<ref>` wrap).

7. **Curly-brace supplied-word marker turned out to be a second, distinct
   category from `[brackets]`.** Surfaced during end-to-end testing of the
   work above: the BSB text uses *two* markers, not one. `[brackets]`
   (18,688 rows) skew toward substantive, broadly-supplied content —
   articles, conjunctions, pronouns, referents/proper nouns ("[Jesus]
   answered") — words with no source-language counterpart at all.
   `{braces}` (1,270 rows) skew overwhelmingly toward English auxiliary/
   modal/copula verbs ("do/does/did", "will/shall/would/should/may/can",
   "is/are/was/were/am/be", "let") plus phrasal-verb/idiom particles
   ("away", "down", "up", "back", "over", "again", "together", "out",
   "about", "with", "from") — words that read as grammatically implied by
   the source verb's own tense/mood/aspect marking rather than freely-added
   content. Confirmed independent of brackets (both can appear in the same
   cell, e.g. `'[and] it {will} become'`, 40 such rows), never nested in one
   another, and always balanced (0 unmatched `{`/`}` across all 1,270). Given
   two different categories, `transform_english()` got a second, independent
   `brace_replacement` control rather than reusing `bracket_replacement` —
   see item 5 above.

## Known issues (not yet fixed)

1. **Pre-owner punctuation in `vvv`-led groups** — exactly 10 verses, verified:
   1Chr.8.34, 1Kgs.12.8, 2Cor.13.2, Acts.26.25, Dan.6.20, Ezek.47.4,
   Gen.26.13, John.7.35, Matt.14.29, Matt.19.3. When a `vvv` (continuation-
   before) row also carries its own punctuation/end_quote, that mark has no
   correct place in `_assemble_group_text()` — it can only attach before or
   after the group's owner text, but conceptually belongs to whatever
   finished rendering *before* this group started (Gen 26:13's comma
   belongs at the end of "richer and richer," not attached to either edge
   of "until"). Real fix needs punctuation timing decoupled from
   word-alignment grouping; not attempted.

2. **~18 more `space_before_punct` cases, not individually diagnosed.** A
   crude regex scan (` [,.;:!?'"'")\]]`) currently flags 28 verses; only the
   10 above have been traced to a specific mechanism. The rest (`'...you' ?'`-
   style patterns in 2Chr.32.11, Ezek.13.12, Job.6.23, etc.) haven't been
   looked at — could be the same mechanism, could be something new.

3. **`SOURCE_TO_TARGET` (forward interlinear) not implemented** —
   `TableComposer.__init__` raises `NotImplementedError`, same gap as
   `AlignmentComposer`. Blocked on a real design question: groups are only
   guaranteed contiguous in `bsb_sort` order, not `source_sort` order (the
   Gen 5:23 "365" group scatters non-monotonically: Heb Sort
   3003,3004,3002,3001,3000), so the grouped-AlignedToken approach this
   direction currently uses won't directly reorder — forward interlinear
   likely needs a per-row (ungrouped) rendering strategy instead.

4. **The `Space` column's meaning is still unconfirmed** — blank in every
   row seen so far across all investigation in this session. Not used for
   anything; flagged in `TableComposer`'s docstring.

## Where to pick this back up

Item 1 (pre-owner punctuation) is the most concrete of the remaining issues
— a real architectural limitation, already scoped down to exactly 10 verses,
just needs punctuation timing decoupled from word-alignment grouping.
