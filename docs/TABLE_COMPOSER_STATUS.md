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
- `TableComposer(direction=MappingDirection.SOURCE_TO_TARGET)` (forward
  interlinear) — a per-source-token, ungrouped build (`_build_verse_source_order()`),
  deliberately not a source-order reordering of the grouped `TARGET_TO_SOURCE`
  model: rows are walked in `(verse_id, source_sort)` order with no par_class/
  is_red (that state only makes sense in `bsb_sort`/document order, which this
  path doesn't walk) and no cross-row punctuation/quote reassembly (a quote
  mark can land on a different row than the phrase it wraps once a BSB group's
  members reorder between `bsb_sort` and `source_sort` — e.g. Gen 5:23's "365"
  scattering — so it can occasionally display out of place; accepted as a
  known limit rather than solved). Verified against a synthetic scattered-
  group case reproducing that scenario, plus multi-verse/books-filter
  iteration; not yet exercised end-to-end through a writer/formatter (no
  forward-interlinear `VerseFormatter` exists yet).

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
   `suphdg` (the main section headings) entirely — e-Sword has its own
   built-in pericope display, on by default and not suppressible from
   module data, so rendering those here would double them up, and there's
   no way to make ours render above the verse the way its native pericopes
   do anyway. Only `acrostic`/`ihdg`/`subhdg` — classes native pericopes
   don't cover — render, each wrapped in a same-named `<span>` (styled by
   `_INTRALINEAR_CSS` so each stays independently stylable) followed by a
   literal `<br/>` — CSS-only alternatives (`display:block`, then
   `float:left`/`width:100%`) were both tried and both broke against real
   e-Sword rendering; a trailing `<br/>` is what works. `_MySwordXrefMixin`
   overrides this base default, though: MySword renders every header class
   via its own `<TS>...<Ts>` title tag, undifferentiated by class — its own
   pericope handling didn't need the same doubling workaround e-Sword did.

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

8. **Stray website markup leaking into rendered text via BegQ** (e.g. Psalm
   121:1 rendering literal `<span class=|reftext|>...` right before "I lift
   up"). `_assemble_group_text()` glues `beg_quote` onto the token's text
   with no cleaning, and 116 rows have berean.bible's own inline verse-
   number-anchor HTML there instead of a real quote mark — see
   `BSB_TABLES_SOURCE_ERRORS.md` item 3. Fixed at import time:
   `_strip_reftext_marker()` in `import_bsb_table.py` strips the
   `<span class=|reftext|>...</span>` pattern from BegQ before it's stored,
   confirmed zero leaks across the full Bible.

9. **`pshdg`/`inscrip`/`selah` Par-column classes now style the English
   text they apply to** (Psalm superscriptions, quoted inscriptions like
   Exodus 28:36's "HOLY TO THE LORD", liturgical refrains). Unlike header
   classes, Par classes apply to a *run* of tokens, not a standalone label,
   and — like `Hdg`/`Crossref` — the raw column only marks a paragraph's
   *first* row; every row after it carries no `par_class` at all until the
   next paragraph starts (confirmed: Psalm 3:1's "A Psalm" row alone carries
   `<p class=|pshdg|>`, with "of David"/"when he fled"/etc. all `None` until
   "O LORD" starts a new `indent1stline` paragraph). `TableComposer` now
   tracks that as forward state across the whole row stream (not reset per
   verse — see item 11) and stamps the bare class name on
   `AlignedToken.par_class` (new field, always `None` from
   `AlignmentComposer`); `VerseFormatter.transform_english()` wraps
   just the English — never the adjacent source-word/transliteration
   markup — in a same-named `<span>` when `par_class` is one of
   `_ITALIC_PAR_CLASSES`, styled by one shared rule in `_INTRALINEAR_CSS`.
   `pshdg`/`inscrip`/`selah` are collapsed to one italic treatment rather
   than styled individually, deliberately not reopening full Par-column
   styling (indent levels, lists, tabs) as its own project.

10. **Red-letter (words of Christ), wired as an opt-in `output.red_letter`
    config option, off by default.** Tracked independently of `par_class`
    on a new `AlignedToken.is_red` field, since it's orthogonal to
    paragraph type — it can ride along with any paragraph (`<p class=|reg|>
    <span class=|red|>` marks a normally-styled paragraph whose text is
    *also* red) and fuses into indent-level class names entirely
    (`indentred1`, `indentred2`, `indent1stlinered`, `tab1stlinered`).
    `TableComposer._extract_is_red()` flips a forward boolean whenever a
    row's raw marker text mentions "red" at all; confirmed against Matthew
    4:4 that it turns on at `<span class=|red|>` ("It is written"), rides
    through the `indentred1`/`indentred2` quoted poetry, and turns back off
    at the next marker that doesn't mention red (`<p class=|reg|>`, "Then
    the devil..."). `SQLiteBibleWriter` strips `is_red` back to `False` on
    every token when `red_letter` is off (same filter-at-the-writer pattern
    as `notes`). Default off given the OT/NT red-letter completeness
    question raised earlier in this session (the source data doesn't mark
    God's direct OT speech the same way, so red-letter here is NT-only by
    construction) — left as the module builder's choice, not resolved by
    the pipeline itself.

    Renders using each app's own native red-letter markup rather than a
    fixed CSS color, so the *reader's* own display-setting toggle controls
    visibility instead of it being baked into the module: e-Sword gets
    `<red>...</red>`, MySword gets `<FR>...<Fr>` (matching its `<RF>...<Rf>`
    footnote-tag convention). `VerseFormatter.red_letter_tags` — a
    `(prefix, suffix)` class var, same shape as `bracket_replacement` —
    controls this; `_ESwordXrefMixin`/`_MySwordXrefMixin` set it for their
    respective formatters (shared home for both the Intralinear and Reverse
    Interlinear variant of each platform, even though it isn't xref-
    specific), and the base class default falls back to a CSS `<span>` for
    any future format with no native equivalent. The `.red` CSS rule from
    the first pass was removed since it's now dead weight — both platforms'
    native tags need no CSS at all.

11. **`par_class`/`is_red` state was incorrectly reset every verse instead
    of persisting across verse boundaries — found by spot-checking real
    output.** A paragraph routinely spans more than one verse (Matthew
    5:11's "Blessed are you..." opens a `<p class=|red|>` paragraph that
    keeps going through 5:12's "Rejoice and be glad..." with no marker of
    its own at all, through 5:14's next `<p class=|red|>`), but
    `TableComposer._build_verse()` was a `@staticmethod` re-initializing
    `current_par_class`/`current_is_red` fresh on every call, so verses
    with no marker of their own — meaning "still in the previous
    paragraph" — were silently treated as plain `reg`/non-red instead.
    Fixed by threading both as running state through `iter_verses()`
    across successive `_build_verse()` calls rather than resetting them
    per verse; confirmed safe to run continuously across book boundaries
    too, since every book's first token carries its own explicit marker
    (checked Genesis/Matthew/Mark/Psalms). Red-letter token count across
    the full Bible went from 15,373 (buggy) to 28,999 (fixed) — roughly
    half of all red-letter text had been getting dropped by this bug.

12. **Trailing punctuation moved after the transliteration** in the
    Intralinear formatters' inline layout — see the "word order" section
    of `verse_formatter.py` and `_split_trailing_punct()`. (Also fixed a
    regression this introduced: the trailing-punctuation character class
    briefly included `]`, splitting supplied-word brackets like `"[Jesus]"`
    into an unbalanced `"[Jesus"` + `"]"` before `transform_english()` ever
    saw them, so `bracket_replacement` silently stopped matching. Fixed by
    removing `]` from that class — it's a bracket's own closer, not
    punctuation to relocate.)

13. **Hebrew *paseq* (U+05C0, `׀`) filtered from displayed source text.**
    Looks like an ASCII `|` but is a real Masoretic cantillation mark
    (2,268 tokens, always glued onto the end of a real word, never
    standalone) — confirmed genuine text, not a data error. Rendered at
    1.5-2x the surrounding Hebrew's size in both e-Sword and MySword (a
    target-font/glyph issue with no known fix), so `TableComposer._to_source_word()`
    strips it from what `SourceWord`/`SourceToken` actually display; the
    stored `tokens.source_text` column keeps the authentic character
    untouched.

14. **Letter-suffixed Strong's numbers (`0871a`, `2050b`) no longer resolve
    to a lexicon link.** macula-hebrew/macula-greek use a trailing letter to
    tag grammatical morphemes (prepositions, the article, conjunctions) with
    a pseudo-Strong's slot borrowed from an unrelated real entry's number,
    not a genuine sense-disambiguated Strong's number — confirmed against
    the classic Strong's dictionary: the bare preposition *bet* is tagged
    `0871a`, which strips to H871, but H871 is really "Atharim" (Num 21:1,
    its only occurrence); the conjunction *waw* is tagged `2050b`/etc.,
    which strips to H2050, but H2050 is really "imagine mischief" (Ps 62:3,
    also its only occurrence) — same collision pattern across three
    different grammatical categories, not a one-off. `composer.py`'s
    `_load_source_index()` (`AlignmentComposer`'s live source-file path) and
    `table_composer.py`'s `_prefixed_strongs()` previously stripped the
    letter and linked to the digits anyway, silently pointing readers at the
    wrong dictionary entry; both now suppress the number entirely when a
    letter suffix is present rather than resolving it. `bsb_tables.db` never
    carried a letter-suffixed number to begin with (confirmed: 0 of 437,587
    non-null `strongs` values), so this is a no-op for `TableComposer`'s
    current output — it fixes `AlignmentComposer`'s live path and guards
    against a future `bsb_tables.tsv` rebuild pulling raw macula fields
    through unchanged.

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

3. **`SOURCE_TO_TARGET` (forward interlinear) still only implemented for
   `TableComposer`** — `AlignmentComposer._join_verse` still raises
   `NotImplementedError` for this direction. Likely an easier join there,
   not a port of `TableComposer`'s: macula's per-word `gloss` column gives
   each source token its own literal gloss independent of BSB's grouped
   translation text, so there's no group-scattering concern to work around
   in the first place — just no writer/formatter exists yet to consume
   either path's forward-interlinear output.

4. **The `Space` column's meaning is still unconfirmed** — blank in every
   row seen so far across all investigation in this session. Not used for
   anything; flagged in `TableComposer`'s docstring.

## Where to pick this back up

Item 1 (pre-owner punctuation) is the most concrete of the remaining issues
— a real architectural limitation, already scoped down to exactly 10 verses,
just needs punctuation timing decoupled from word-alignment grouping.
