# Intralinear Bible — Developer Guide

A pipeline for generating intralinear and reverse-interlinear Bible modules for
[MySword](https://www.mysword.info/) (Android), [e-Sword](https://www.e-sword.net/)
(Android / iOS / Windows), and OSIS XML — combining the
[Berean Standard Bible](https://bereanbible.com/) with inline Hebrew and Greek
transliteration linked to Strong's concordance.

---

## Output Targets

| Format   | Mode                 | Abbreviation | File                 |
|----------|----------------------|--------------|----------------------|
| e-Sword  | Intralinear          | `BSTB`       | `BSTB.bbli`          |
| e-Sword  | Intralinear stacked  | `BSXB`       | `BSXB.bbli`          |
| e-Sword  | Reverse interlinear  | `BSBri`      | `BSBri.bbli`         |
| MySword  | Intralinear          | `BSTB`       | `BSTB.bbl.mybible`   |
| MySword  | Intralinear stacked  | `BSXB`       | `BSXB.bbl.mybible`   |
| MySword  | Reverse interlinear  | `BSBri`      | `BSBri.bbl.mybible`  |
| OSIS XML | Intralinear          | `BSBi`       | `BSBi.osis.xml`      |

---

## Building From Source

The pipeline can read verse/alignment data from either of two interchangeable
sources — a `Composer` (see Architecture below). Pick whichever prerequisite
path matches the data you have; `main.py` auto-detects which one to use (see
Configuration).

### Option A: `table_db` (recommended — no external repos needed)

Get `bsb_tables.tsv` (the full BSB interlinear export) from
[berean.bible/downloads.htm](https://berean.bible/downloads.htm), place it at
`data/bsb_tables.tsv`, then build the database once:

```bash
python utils/import_bsb_table.py     # writes data/bsb_tables.db (~10s)
```

That's it — no other repos to clone. `data/bsb_tables.tsv` and
`data/bsb_tables.db` are both gitignored (large/generated); re-run the import
script any time the source file changes.

### Option B: live source/alignment/target join

Clone the following repositories as siblings of this one:

```
parent/
├── intralinear-bible/      ← this repo
├── macula-hebrew/          ← https://github.com/Clear-Bible/macula-hebrew
├── macula-greek/           ← https://github.com/Clear-Bible/macula-greek
└── Alignments/             ← https://github.com/Clear-Bible/Alignments
```

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run

```bash
python main.py                              # e-Sword intralinear, config.yaml
python main.py --format mysword             # MySword intralinear + stacked
python main.py --format all                 # every output target in one pass
python main.py --format esword --mode inter # e-Sword reverse interlinear
python main.py my_config.yaml --format osis
python main.py --composer alignment         # force the live join even if table_db exists
```

Output files are written to the directory set in `config.yaml → output.dir`.

---

## Configuration

```yaml
# Module identity
version:     "1.0.1"
translation: "BSB"

# Composer: table_db is used automatically when that file exists on disk;
# otherwise falls back to the live source/alignment/target join below. Set
# composer explicitly (alignment|table) to force one path regardless of
# what's on disk — also settable via --composer, which wins over both.
table_db: "data/bsb_tables.db"
# composer: alignment

# Source data (paths relative to data_root) — only read when composer
# resolves to "alignment"
data_root: "../"
sources:
  ot:
    source:    "macula-hebrew/WLC/tsv/macula-hebrew.tsv"
    alignment: "Alignments/data/eng/alignments/BSB/WLCM-BSB-manual.json"
    target:    "Alignments/data/eng/targets/BSB/ot_BSB.tsv"
  nt:
    source:    "macula-greek/Nestle1904/tsv/macula-greek-Nestle1904.tsv"
    alignment: "Alignments/data/eng/alignments/BSB/SBLGNT-BSB-manual.json"
    target:    "Alignments/data/eng/targets/BSB/nt_BSB.tsv"

# Transliteration
transliteration:
  hebrew: "brill_simple"    # brill_simple | sbl_simple | sbl_academic | phonetic_dot
  greek:  "SIMPLE"

# Annotation sources
annotations: "data/bsb_annotations.json"
crossrefs:   "data/bsb_xrefs.json"

# Output options
output:
  dir:     "output"
  headers: 0    # section headers (0 = off; skips loading annotations if notes also 0)
  notes:   1    # translator footnotes
  xref:    0    # cross-references: 0 = none, 1 = start of verse, 2 = end of verse

# Book filter: null = full Bible, or a list of OSIS book IDs
books: null     # e.g. [Gen, Exod, Matt, John]
```

---

## Project Structure

```
intralinear-bible/
├── main.py              # entry point: CLI, config, composer auto-detect, writer factory
├── composer.py          # Composer ABC + AlignmentComposer — live source/alignment/target join
├── table_composer.py    # TableComposer — reads data/bsb_tables.db instead of joining live
├── models.py            # data classes: SourceToken, SourceWord, AlignedToken, …
├── verse_formatter.py   # VerseFormatter ABC + one concrete class per output target × style
│                        #   ESwordIntralinearFormatter
│                        #   ESwordStackedFormatter       (subclass of ESword Intralinear)
│                        #   ESwordReverseInterlinearFormatter
│                        #   MySwordIntralinearFormatter
│                        #   MySwordStackedFormatter      (subclass of MySword Intralinear)
│                        #   MySwordReverseInterlinearFormatter
│                        # also: Reference dataclass, parse_reference(), parse_headers() —
│                        # format-agnostic helpers both Composers' output flows through
├── bible_writer.py      # BibleWriter ABC (open / add_verse / write)
├── sqlite_writer.py     # SQLiteBibleWriter — shared SQLite base for e-Sword + MySword
├── esword_writer.py     # ESwordWriter — Mods table, Bible view, Notes table
├── mysword_writer.py    # MySwordWriter — Details table with CSS + VerseRules
├── osis_writer.py       # OSISWriter — incremental OSIS XML tree
├── translit.py          # make_transliterator() — Hebrew + Greek → Latin script
├── config.yaml          # default pipeline configuration
├── utils/
│   ├── import_bsb_table.py   # one-time: bsb_tables.tsv -> data/bsb_tables.db
│   ├── build_books_table.py  # one-time: biblelib -> data/books.db
│   └── extract_bsb_xrefs.py  # one-time: BSB USX -> data/bsb_xrefs.json (AlignmentComposer path)
└── data/
    ├── books.db                # book metadata (osis_id, display_abbrev, usfm_number, testament)
    ├── bsb_annotations.json    # section headers and translator footnotes (AlignmentComposer path)
    ├── bsb_xrefs.json          # BSB parallel-passage cross-references (AlignmentComposer path)
    ├── bsb_tables.tsv          # gitignored — full BSB interlinear export, see Building From Source
    └── bsb_tables.db           # gitignored — built from bsb_tables.tsv by import_bsb_table.py
```

---

## Architecture

```
                    config.yaml (composer: table | alignment, auto-detected)
                               │
                 ┌─────────────┴─────────────┐
          TableComposer              AlignmentComposer
       (reads bsb_tables.db)   (live source/alignment/target join)
                 └─────────────┬─────────────┘
                               │  yields (osis_ref, [AlignedToken], header, xrefs)
                 ┌─────────────┼─────────────┐
                 ▼             ▼             ▼
           ESwordWriter  MySwordWriter   OSISWriter
           (+ formatter) (+ formatter)
                 │             │
          VerseFormatter  VerseFormatter
          render_verse()  render_verse()
          css             css
          verse_rules     verse_rules
```

**`Composer`** is an ABC with one method, `iter_verses()`, yielding
`(osis_ref, [AlignedToken], header, xrefs)` tuples — writers and formatters
never know or care which implementation produced the stream:

- **`AlignmentComposer`** reads source TSVs and alignment JSON/ndjson once,
  live, and joins them per verse.
- **`TableComposer`** reads a single precomputed SQLite database
  (`data/bsb_tables.db`, built once by `utils/import_bsb_table.py` from
  `bsb_tables.tsv`) instead — no macula/Alignments repos needed, and it's
  the faster path, so `main.py` prefers it automatically whenever that
  database exists (see Configuration). Both composers store `header` and
  `xrefs` in different raw shapes (see below) — `VerseFormatter` is what
  normalizes that difference, not the composers themselves.

Multiple writers consume the same stream, so `--format all` reads the
verse data exactly once regardless of how many output targets are active.

**`VerseFormatter`** owns the complete rendering contract for one output target:
- `render_verse()` — produces the HTML/GBF string stored in the DB
- `css` — styles exactly the tags that `render_verse()` emits
- `verse_rules` — MySword regex transforms applied at display time (must match the tags above)
- `render_header()` / `parse_headers()` — turn a verse heading (either a
  plain string from `AlignmentComposer`'s `bsb_annotations.json`, or a raw
  `<p class=|hdg|>...` wrapper cell from `TableComposer`'s `bsb_tables.db`)
  into this format's heading markup; both shapes go through the same
  `parse_headers()` parsing. Default policy: skip the main `hdg`/`suphdg`
  segments entirely — MySword and e-Sword both have their own built-in
  pericope (section heading) display, on by default and not suppressible
  from module data, so rendering those here would double them up (and for
  e-Sword there's no way to make ours render above the verse the way native
  pericopes do anyway). Only render the classes native pericopes don't
  cover — `acrostic`, `ihdg`, `subhdg` — each wrapped in a same-named
  `<span>` (styled by `_INTRALINEAR_CSS`, so each class stays independently
  stylable instead of colliding with `bracket_replacement`'s own `<i>` use)
  followed by a literal `<br/>`. CSS-only "same line as the verse number,
  wrap only after" tricks (`display:block`; `float:left`/`width:100%`) were
  tried first and both broke against the real e-Sword/MySword rendering
  engines — a trailing `<br/>` is what actually works reliably.
- `transform_english()` / `bracket_replacement` / `brace_replacement` —
  strip, keep, or restyle the BSB text's two independent translator-supplied-
  word markers: `[brackets]` (broadly supplied words — pronouns, articles,
  referents) and `{braces}` (English auxiliary/modal verbs implied by the
  source verb's own tense/mood, e.g. `{will}`, `{do}`, `{let}`). Also takes
  an `AlignedToken.par_class` (`TableComposer` only — the `Par` column's
  paragraph class in effect for that token, e.g. `pshdg` for a Psalm
  superscription, `selah` for a liturgical refrain) and wraps the English
  in a same-named `<span>` for the classes in `_ITALIC_PAR_CLASSES`,
  leaving any adjacent source-word/transliteration markup untouched.
- `Reference` / `parse_reference()` / `transform_reference()` /
  `render_crossref()` — parse a verse's cross-reference text (same plain
  `"Joh 1:1-5; Heb 11:1-3"` shape from both composers) into individual
  references and render each in this format's own link/tag syntax; a
  reference with no single verse target (a whole-chapter range or book
  span) either anchors to that range's first chapter while still
  displaying the full range as its label (MySword's `<RX>` tag, which
  supports a separate label/target), or falls back to plain non-linked
  text (e-Sword's `<ref>` tag, which doesn't)

All of the above must stay in sync with each other and with `css`/`verse_rules`.
The writer filters inputs (headers, notes, xrefs) before calling `render_verse()`
so disabled features never produce tags and their CSS rules are never exercised.

**`BibleWriter`** subclasses (`ESwordWriter`, `MySwordWriter`, `OSISWriter`) are
format-only: they manage the output file, schema, and any format-specific side
tables (e-Sword's `Notes` table, MySword's `Details` table). They contain no
rendering logic of their own, though e-Sword's `Notes`-table cross-reference
text is built by calling the injected `VerseFormatter`'s `transform_reference()`
per reference, same as the inline marker path.

---

## Extending

### Add a new verse style

1. Add a new `VerseFormatter` subclass in `verse_formatter.py` with its own
   `render_verse()`, `css`, and (if MySword) `verse_rules`.
2. Add the writer + formatter pair to `build_writers()` in `main.py`.

### Add a new output format

1. Create a new writer module that subclasses `BibleWriter`.
2. Inject a `VerseFormatter` for rendering.
3. Register it in `build_writers()`.

### Change how a format handles headings, supplied words, or cross-references

These are per-`VerseFormatter` overrides, not composer-level changes — both
composers feed the same raw shapes through `parse_headers()`/`parse_reference()`,
so a new format only needs to override the render/transform side:

- Headings: override `render_header()` (default skips `hdg`/`suphdg` to avoid
  doubling up with MySword/e-Sword's own pericope display, renders
  `acrostic`/`ihdg`/`subhdg` inline in italics); key off each segment's class
  (see `parse_headers()`'s docstring, or `_INLINE_HEADER_CLASSES`) for
  different policy or styling.
- Supplied words: set `bracket_replacement`/`brace_replacement` class vars,
  or override `transform_english()` directly for more control.
- Cross-references: override `transform_reference()` for this format's link
  syntax, and `render_crossref()` if the placement (inline vs. note table)
  needs more than "one call to `transform_reference()` per reference."

See `docs/TABLE_COMPOSER_STATUS.md` for the `TableComposer` pipeline's
current limitations and open questions.

---

## Transliteration Schemes

| Scheme         | Hebrew example  |
|----------------|-----------------|
| `brill_simple` | be·re·shít      |
| `sbl_simple`   | bereʾshit       |
| `sbl_academic` | bərēʾšîṯ        |
| `phonetic_dot` | beh·reh·SHEET   |

---

## License

[CC BY-SA 4.0](http://creativecommons.org/licenses/by-sa/4.0/)

### Attribution

- **Berean Standard Bible** © 2022 Bible Hub — [bereanbible.com](https://bereanbible.com) — CC BY-SA 4.0
- **BSB Interlinear Tables** (`bsb_tables.tsv`, `TableComposer` data source — Option A above) © Bible Hub — [berean.bible/downloads.htm](https://berean.bible/downloads.htm) — CC BY-SA 4.0
- **Macula Hebrew** (`AlignmentComposer` data source — Option B above) © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-hebrew](https://github.com/Clear-Bible/macula-hebrew) — CC BY 4.0
- **Macula Greek** (`AlignmentComposer` data source — Option B above) © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-greek](https://github.com/Clear-Bible/macula-greek) — CC BY 4.0
- **Clear Bible Alignments** (`AlignmentComposer` data source — Option B above) © Clear Bible — [github.com/Clear-Bible/Alignments](https://github.com/Clear-Bible/Alignments) — CC BY 4.0
