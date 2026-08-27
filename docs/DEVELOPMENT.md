# Intralinear Bible — Developer Guide

A pipeline for generating intralinear and reverse-interlinear Bible modules for
[MySword](https://www.mysword.info/) (Android), [e-Sword](https://www.e-sword.net/)
(Android / iOS / Windows), and OSIS XML — combining the
[Berean Standard Bible](https://bereanbible.com/) with inline Hebrew and Greek
transliteration linked to Strong's concordance.

---

## Output Targets

| Format   | Mode                     | Abbreviation | File                    |
|----------|--------------------------|--------------|-------------------------|
| e-Sword  | Intralinear, level 1     | `BTB-L1`     | `BTB-L1.bbli`           |
| e-Sword  | Intralinear, level 2     | `BTB-L2`     | `BTB-L2.bbli`           |
| e-Sword  | Intralinear, level 3     | `BTB-L3`     | `BTB-L3.bbli`           |
| e-Sword  | Reverse interlinear      | `BSRB`       | `BSRB.bbli`             |
| MySword  | Intralinear, level 1     | `BTB-L1`     | `BTB-L1.bbl.mybible`    |
| MySword  | Intralinear, level 2     | `BTB-L2`     | `BTB-L2.bbl.mybible`    |
| MySword  | Intralinear, level 3     | `BTB-L3`     | `BTB-L3.bbl.mybible`    |
| MySword  | Reverse interlinear      | `BSRB`       | `BSRB.bbl.mybible`      |
| OSIS XML | Intralinear              | `BSBi`       | `BSBi.osis.xml`         |

### The BTB-L1/L2/L3 tiers

"Berean Transliterated Bible" ships as three separate modules per platform
rather than one, each a step deeper into the source language for readers who
don't read Hebrew/Greek script but still want to recognize word stems and
look up Strong's entries.

Every tier shares one shape: a **primary** line (`ro` in the markup) that's
always populated, always the Strong's link, and higher contrast — the line
a reader actually tracks — and a **secondary** line (`rt`) below it, lower
contrast, sometimes omitted entirely when it has nothing to add. Only what
fills each role changes per tier:

- **L1** — primary: lemma transliteration only (e.g. `reshit`, not
  `H7225`); no secondary line at all. Meant to get you to the lexicon
  entry as directly as possible, not to teach pronunciation.
- **L2** — primary: the word's own full transliteration, always shown, so
  the line a continuous reader is actually following is never in
  question. The lemma becomes the secondary line, shown only when it
  differs from the primary.

  (An earlier version had this backwards — lemma primary/always-shown,
  word-transliteration secondary/suppressed-when-matching — confirmed on
  real devices to break reading rhythm: hiding the line most readers were
  actually tracking, whenever it happened to match the lemma, reads as
  "did I lose my place?" rather than "nothing extra here," and invites
  "is this word missing from the source?" questions. Promoting the word's
  own transliteration to primary fixes both.)
- **L3** — primary: the original Hebrew/Greek script; secondary: the
  word's own transliteration, always shown (script and transliteration
  never coincide, so there's no "matches, omit it" case here). The
  heaviest tier, unchanged in substance from the retired `BSXB`.

Because tapping the primary line always opens the Strong's dictionary
entry, every tier's tap target matches what's actually being read — the
lemma at L1, the inflected word at L2, the real Hebrew/Greek word at L3 —
rather than a separate reference form sitting apart from the text. All
three tiers per platform share one `render_verse()`
(`_ESwordBTBFormatter` / `_MySwordBTBFormatter` in
`verse_formatter/intralinear.py`), with each tier supplying only its own
`_primary_content()`/`_secondary_content()`.

A handful of extremely common Greek function words with suppletive paradigms
— every inflected form collapsed under one Strong's number, e.g. `ho`/`he`/
`to` all under G3588's article entry — would otherwise show a jarring,
uninformative lemma mismatch (`ho` against `ton` on every occurrence).
`LEMMA_SUPPRESSED_STRONGS` in `verse_formatter/intralinear.py` hard-codes
the confirmed offenders (currently G3588 "the", G1473 "I", G4771 "you") to
fall back to the word's own transliteration wherever the lemma would
otherwise appear (L1's primary line, L2's secondary line).

`main.py --mode intralinear` builds all three tiers together; `--mode L1`/
`L2`/`L3` builds one alone (see Run, below).

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
python main.py                              # e-Sword BTB-L1/L2/L3, config.yaml
python main.py --format mysword             # MySword BTB-L1/L2/L3
python main.py --format mysword --mode L2   # MySword BTB-L2 only
python main.py --format all                 # every output target in one pass
python main.py --format esword --mode rev   # e-Sword reverse interlinear
python main.py my_config.yaml --format osis
python main.py --composer alignment         # force the live join even if table_db exists
```

Output files are written to the directory set in `config.yaml → output.dir`.

---

## Configuration

```yaml
# Module identity
version:     "1.1.5"
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
  dir:        "output"
  headers:    0    # section headers (0 = off; skips loading annotations if notes also 0)
  notes:      1    # translator footnotes
  xref:       0    # cross-references: 0 = none, 1 = start of verse, 2 = end of verse
  red_letter: 0    # words of Christ in red (table composer only); off by default

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
├── verse_formatter/     # VerseFormatter ABC + one concrete class per output target × style,
│                        # organized by mode rather than platform — see Architecture below
│   ├── base.py                 # VerseFormatter ABC; Reference dataclass, parse_reference(),
│   │                            #   parse_headers() (format-agnostic helpers both Composers'
│   │                            #   output flows through); e-Sword/MySword xref+red-letter mixins
│   ├── intralinear.py          # BTB-L1/L2/L3: ESwordLemmaFormatter, ESwordLemmaDetailFormatter,
│   │                            #   ESwordStackedFormatter, MySwordLemmaFormatter,
│   │                            #   MySwordLemmaDetailFormatter, MySwordStackedFormatter
│   ├── reverse_interlinear.py  # BSRB: ESwordReverseInterlinearFormatter,
│   │                            #   MySwordReverseInterlinearFormatter
│   └── __init__.py             # re-exports the package's public API
├── bible_writer.py      # BibleWriter ABC (open / add_verse / write)
├── sqlite_writer.py     # SQLiteBibleWriter — shared SQLite base for e-Sword + MySword
├── esword_writer.py     # ESwordWriter — Mods table, Bible view, Notes table
├── mysword_writer.py    # MySwordWriter — Details table with CSS + VerseRules
├── osis_writer.py       # OSISWriter — incremental OSIS XML tree
├── translit.py          # make_transliterator() — Hebrew + Greek → Latin script
├── config.yaml          # default pipeline configuration
├── utils/
│   ├── import_bsb_table.py   # one-time: bsb_tables.tsv -> data/bsb_tables.db
│   ├── import_lemma_table.py # one-time: HebrewStrong.xml/strongsgreek.xml (+ bsb_tables.db
│   │                          #   fallback for coverage holes) -> strongs_lemma table, for
│   │                          #   BTB-L1/L2's lemma transliteration
│   ├── build_books_table.py  # one-time: biblelib -> data/books.db
│   └── extract_bsb_xrefs.py  # one-time: BSB USX -> data/bsb_xrefs.json (AlignmentComposer path)
├── heb_devotional/       # separate pipeline — see Devotional Modules below
│   ├── reading_plan.py   # Hebcal fetch, day/date assignment, reference resolution (shared)
│   ├── esword.py         # .devi rendering + SQLite writing
│   └── mysword.py        # MySword Journal-format rendering + SQLite writing
└── data/
    ├── books.db                # book metadata (osis_id, display_abbrev, usfm_number, testament)
    ├── bsb_annotations.json    # section headers and translator footnotes (AlignmentComposer path)
    ├── bsb_xrefs.json          # BSB parallel-passage cross-references (AlignmentComposer path)
    ├── bsb_tables.tsv          # gitignored — full BSB interlinear export, see Building From Source
    ├── bsb_tables.db           # gitignored — built from bsb_tables.tsv by import_bsb_table.py;
    │                           #   also holds the strongs_lemma table (import_lemma_table.py)
    ├── HebrewStrong.xml        # Strong's Hebrew dictionary (openscriptures/HebrewLexicon) —
    │                           #   primary source for strongs_lemma
    ├── strongsgreek.xml        # Strong's Greek dictionary (morphgnt/strongs-dictionary-xml) —
    │                           #   primary source for strongs_lemma
    ├── parshat.json            # MJAA Hebrew-calendar reading plan (heb_devotional/ input)
    └── parashah_translations.json  # English translation of each parashah name
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
  database exists (see Configuration). Also the only composer that
  populates `SourceToken.lemma_translit` (from the `strongs_lemma` table,
  built by `utils/import_lemma_table.py`) — the BTB-L1/L2 lemma
  transliteration; `AlignmentComposer` leaves it at its default empty
  string, so those tiers built from that path fall back to the word's own
  transliteration everywhere. Both composers store `header` and `xrefs` in
  different raw shapes (see below) — `VerseFormatter` is what normalizes
  that difference, not the composers themselves.

Multiple writers consume the same stream, so `--format all` reads the
verse data exactly once regardless of how many output targets are active.

**`VerseFormatter`** owns the complete rendering contract for one output target:
- `render_verse()` — produces the HTML/GBF string stored in the DB
- `css` — styles exactly the tags that `render_verse()` emits
- `verse_rules` — MySword regex transforms applied at display time (must match the tags above)
- `_split_trailing_punct()` — `AlignedToken.english` glues trailing
  punctuation/quote marks directly onto the word (e.g. `"the earth."`),
  with no separate field for them. Both Intralinear formatters (e-Sword,
  MySword) split it off before rendering so the English word is followed
  by its transliteration/source-word `<span>` and *then* the punctuation,
  instead of the punctuation landing between the word and its annotation.
  Not used by the Reverse Interlinear formatters, where English and the
  source-word block stack in separate rows rather than running inline.
- `render_header()` / `parse_headers()` — turn a verse heading (either a
  plain string from `AlignmentComposer`'s `bsb_annotations.json`, or a raw
  `<p class=|hdg|>...` wrapper cell from `TableComposer`'s `bsb_tables.db`)
  into this format's heading markup; both shapes go through the same
  `parse_headers()` parsing. Default policy (e-Sword): skip the main
  `hdg`/`suphdg` segments entirely — e-Sword has its own built-in pericope
  (section heading) display, on by default and not suppressible from
  module data, so rendering those here would double them up, and there's
  no way to make ours render above the verse the way native pericopes do
  anyway. Only render the classes native pericopes don't cover —
  `acrostic`, `ihdg`, `subhdg` — each wrapped in a same-named `<span>`
  (styled by `intralinear.py`'s `_INTRALINEAR_CSS`, so each class stays independently
  stylable instead of colliding with `bracket_replacement`'s own `<i>` use)
  followed by a literal `<br/>`. CSS-only "same line as the verse number,
  wrap only after" tricks (`display:block`; `float:left`/`width:100%`) were
  tried first and both broke against the real e-Sword rendering engine —
  a trailing `<br/>` is what actually works reliably. MySword overrides
  this base default (via `_MySwordXrefMixin`) to render every header class
  via its own `<TS>...<Ts>` title tag instead, undifferentiated by class.
- `transform_english()` / `bracket_replacement` / `brace_replacement` —
  strip, keep, or restyle the BSB text's two independent translator-supplied-
  word markers: `[brackets]` (broadly supplied words — pronouns, articles,
  referents) and `{braces}` (English auxiliary/modal verbs implied by the
  source verb's own tense/mood, e.g. `{will}`, `{do}`, `{let}`). Also takes
  an `AlignedToken.par_class` (`TableComposer` only — the `Par` column's
  paragraph class in effect for that token, e.g. `pshdg` for a Psalm
  superscription, `selah` for a liturgical refrain) and wraps the English
  in a same-named `<span>` for the classes in `_ITALIC_PAR_CLASSES`, plus
  an `AlignedToken.is_red` (words of Christ, opt-in via `output.red_letter`
  in config.yaml) wrapped in `red_letter_tags` — each app's own native
  red-letter markup (e-Sword `<red>...</red>`, MySword `<FR>...<Fr>`) so
  the reader's own display toggle controls visibility, not a fixed CSS
  color — leaving any adjacent source-word/transliteration markup untouched.
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

1. Add a new `VerseFormatter` subclass with its own `render_verse()`, `css`,
   and (if MySword) `verse_rules` — in `verse_formatter/intralinear.py` or
   `verse_formatter/reverse_interlinear.py`, whichever mode it belongs to,
   or a new sibling module for an entirely new mode (either way, re-export
   it from `verse_formatter/__init__.py`).
2. Add the writer + formatter pair to `build_writers()` in `main.py`.

### Add a new output format

1. Create a new writer module that subclasses `BibleWriter`.
2. Inject a `VerseFormatter` for rendering.
3. Register it in `build_writers()`.

### Change how a format handles headings, supplied words, or cross-references

These are per-`VerseFormatter` overrides, not composer-level changes — both
composers feed the same raw shapes through `parse_headers()`/`parse_reference()`,
so a new format only needs to override the render/transform side:

- Headings: override `render_header()` (base default, used by e-Sword, skips
  `hdg`/`suphdg` to avoid doubling up with its own pericope display and
  renders `acrostic`/`ihdg`/`subhdg` inline in italics; MySword overrides it
  to render every class via its own `<TS>...<Ts>` tag instead); key off
  each segment's class
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

## Devotional Modules

A separate pipeline, `heb_devotional/`, generates a Hebrew-calendar
reading-plan devotional module — e-Sword Daily Devotional (`.devi`), MySword
Journal-format reference book (`.bok.mybible`) — from `data/parshat.json`
(the MJAA "Bible in a Year" reading plan: weekly Torah/Haftarah portions plus
daily OT/NT readings, keyed to Simchat Torah through Simchat Torah) and a
live [Hebcal](https://www.hebcal.com/) fetch, which supplies each week's real
Shabbat date, each holiday's specific date, the real Hebrew date for every
single day, and annotations for fasts, Rosh Chodesh, special Shabbatot, and
Yom Tov status.

This is unrelated to the main Bible-translation pipeline above — different
input data, different output cadence (once per Hebrew year, not per code
change) — so it isn't wired into `main.py`.

### Run

```bash
python -m heb_devotional.esword [hebrew_year]    # e-Sword .devi
python -m heb_devotional.mysword [hebrew_year]   # MySword Journal .bok.mybible
```

`hebrew_year` is the Hebrew year the cycle's Bereshit falls in — optional,
defaults to 5786. e-Sword needs `data/bsb_tables.db` (fills in real verse
ranges: its `<ref>` tag, unlike MySword's own bible link, doesn't resolve a
bare "book chapter" reference — see `utils/import_bsb_table.py`); MySword
needs no such lookup.

### Package layout

- `heb_devotional/reading_plan.py` — everything format-agnostic: the Hebcal
  fetch, week/holiday date derivation, `build_day_entries()` (assigns every
  reading to its real calendar date), and reference resolution —
  `resolve_refs()` for e-Sword's chapter-by-chapter, verse-bounded `<ref>`
  tags (needs `bsb_tables.db`), `resolve_refs_simple()` for MySword's
  simpler links (no lookup needed, but a verse-less multi-chapter reference
  still splits one Reference per chapter — MySword misreads a bare
  `book.chapter-chapter` href as a verse range on the first chapter, not a
  second chapter).
- `heb_devotional/esword.py` — `.devi` rendering + SQLite writing.
  `Devotional` is keyed on Month/Day only (no year), so a leap Hebrew year's
  ~383-385 day cycle lands two different Gregorian dates on the same slot
  (e.g. Oct 15 2026 and Oct 15 2027) — those merge into one row, each
  section carrying its own date/weekday/hdate and a Gregorian-year `<h2>`
  when a slot actually merges more than one.
- `heb_devotional/mysword.py` — MySword Journal-format (`journal` table)
  rendering + SQLite writing. Every row id/title bakes in the Gregorian
  year, so the Month/Day collision above never happens here — Index →
  month page (a real calendar table, with prev/next links to adjacent
  months, and holiday/fast CSS classes on the relevant day cells) → day
  page (with prev/next-day links, skipping over any day the reading plan
  doesn't cover), navigated via MySword's own `#j <id>` journal links;
  bible references use `#b<book_num>.<chapter>.<verse>[&w=1]` anchors
  (`&w=1` is MySword's own documented "show the whole chapter in the
  popup" suffix, used for a bare chapter reference with no verse given).
  CSS lives once in `details.customcss` (e-Sword's `.devi` Details table
  has no such column, hence the per-row inline `<style>` there instead).

### Data

- `data/parshat.json` — the reading plan itself: `{week_no, week, type:
  D|W|H, refs: [...], label?}`.
- `data/parashah_translations.json` — English translation of each parashah
  name, shown under the Hebrew heading.

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
- **Strong's Hebrew Dictionary** (`data/HebrewStrong.xml`, BTB-L1/L2 lemma transliteration) — [openscriptures/HebrewLexicon](https://github.com/openscriptures/HebrewLexicon) — Public Domain
- **Strong's Greek Dictionary** (`data/strongsgreek.xml`, BTB-L1/L2 lemma transliteration) — [morphgnt/strongs-dictionary-xml](https://github.com/morphgnt/strongs-dictionary-xml) — Public Domain
- **Macula Hebrew** (`AlignmentComposer` data source — Option B above) © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-hebrew](https://github.com/Clear-Bible/macula-hebrew) — CC BY 4.0
- **Macula Greek** (`AlignmentComposer` data source — Option B above) © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-greek](https://github.com/Clear-Bible/macula-greek) — CC BY 4.0
- **Clear Bible Alignments** (`AlignmentComposer` data source — Option B above) © Clear Bible — [github.com/Clear-Bible/Alignments](https://github.com/Clear-Bible/Alignments) — CC BY 4.0
