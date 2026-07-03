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

## Known issues (not yet fixed)

1. **Verse headings still have their raw wrapper tag.** `verses.heading`
   comes straight from the file's `Hdg` column without stripping its
   `<p class=|hdg|>` / `<p class=|subhdg|>` prefix — confirmed present on
   100% of the 3,148 non-null headings, zero exceptions. Surfaced during
   the end-to-end render test: e-Sword's headline rendered as literal
   `<p class=|hdg|>The Creation` instead of `The Creation`. Same pollution
   pattern already fixed for `Crossref`; just never checked on `Hdg`. Fix:
   strip the `<p class=|...|>` prefix when populating `verses.heading` in
   `import_bsb_table.py`.

2. **Pre-owner punctuation in `vvv`-led groups** — exactly 10 verses, verified:
   1Chr.8.34, 1Kgs.12.8, 2Cor.13.2, Acts.26.25, Dan.6.20, Ezek.47.4,
   Gen.26.13, John.7.35, Matt.14.29, Matt.19.3. When a `vvv` (continuation-
   before) row also carries its own punctuation/end_quote, that mark has no
   correct place in `_assemble_group_text()` — it can only attach before or
   after the group's owner text, but conceptually belongs to whatever
   finished rendering *before* this group started (Gen 26:13's comma
   belongs at the end of "richer and richer," not attached to either edge
   of "until"). Real fix needs punctuation timing decoupled from
   word-alignment grouping; not attempted.

3. **~18 more `space_before_punct` cases, not individually diagnosed.** A
   crude regex scan (` [,.;:!?'"'")\]]`) currently flags 28 verses; only the
   10 above have been traced to a specific mechanism. The rest (`'...you' ?'`-
   style patterns in 2Chr.32.11, Ezek.13.12, Job.6.23, etc.) haven't been
   looked at — could be the same mechanism, could be something new.

4. **`SOURCE_TO_TARGET` (forward interlinear) not implemented** —
   `TableComposer.__init__` raises `NotImplementedError`, same gap as
   `AlignmentComposer`. Blocked on a real design question: groups are only
   guaranteed contiguous in `bsb_sort` order, not `source_sort` order (the
   Gen 5:23 "365" group scatters non-monotonically: Heb Sort
   3003,3004,3002,3001,3000), so the grouped-AlignedToken approach this
   direction currently uses won't directly reorder — forward interlinear
   likely needs a per-row (ungrouped) rendering strategy instead.

5. **Supplied-word bracket stripping (`[Jesus]` → `Jesus`) happens at
   `TableComposer` render time, gated on direction**, not at import — doing
   it at import would destroy the bracket info forward interlinear will
   need once #4 is built. Discussed trade-off: leaving it at render time
   costs nothing extra within a single `main.py` run (composer is shared
   across all writers in one pass), only re-runs if you invoke `main.py`
   again later. Alternative considered and deferred: precompute both a
   bracketed and stripped column at import time.

6. **`verses.crossref` is captured but not wired into the `xrefs` output.**
   It holds raw HTML (`<br /><span class=|cross|>(<a href=...>John
   1:1-5</a>...)`), not the plain `"Joh 1:1-5; Heb 11:1-3"` shape
   `AlignmentComposer`'s xref pipeline (and the MySword/e-Sword formatters'
   `_mysword_rx_tags` etc.) expect. `TableComposer.iter_verses()` always
   yields `{}` for xrefs. Turning one into the other is unbuilt.

7. **`main.py` isn't wired to select `TableComposer`.** It's hardcoded to
   `AlignmentComposer(config)`. The end-to-end render test above used a
   standalone script instantiating `TableComposer` + writers directly,
   bypassing `main.py`'s CLI/config entirely. Needs a config key or CLI flag
   to pick a composer before this is usable through the normal `python
   main.py` entry point.

8. **The `Space` column's meaning is still unconfirmed** — blank in every
   row seen so far across all investigation in this session. Not used for
   anything; flagged in `TableComposer`'s docstring.

## Where to pick this back up

Item 1 (heading prefix) is the smallest, most clearly-scoped fix and
probably the next thing worth doing — it's a one-line addition to
`import_bsb_table.py` alongside the existing `Crossref` misfiled-prefix
handling, same pattern, already-verified 100%-consistent data.
