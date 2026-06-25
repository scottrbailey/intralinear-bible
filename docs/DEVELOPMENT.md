# Intralinear Bible — Developer Guide

A pipeline for generating intralinear and reverse-interlinear Bible modules for
[MySword](https://www.mysword.info/) (Android), [e-Sword](https://www.e-sword.net/)
(Android / iOS / Windows), and OSIS XML — combining the
[Berean Standard Bible](https://bereanbible.com/) with inline Hebrew and Greek
transliteration linked to Strong's concordance.

---

## Output Targets

| Format   | Mode                | Abbreviation | File              |
|----------|---------------------|--------------|-------------------|
| e-Sword  | Intralinear         | `BSBi`       | `BSBi.bbli`       |
| e-Sword  | Reverse interlinear | `BSBri`      | `BSBri.bbli`      |
| MySword  | Intralinear         | `BSBi`       | `BSBi.bbl.mybible`  |
| MySword  | Intralinear stacked | `BSBis`      | `BSBis.bbl.mybible` |
| MySword  | Reverse interlinear | `BSBri`      | `BSBri.bbl.mybible` |
| OSIS XML | Intralinear         | `BSBi`       | `BSBi.osis.xml`   |

---

## Building From Source

### Prerequisites

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
```

Output files are written to the directory set in `config.yaml → output.dir`.

---

## Configuration

```yaml
# Module identity
version:     "1.0.1"
translation: "BSB"          # drives module abbreviations (BSBi, BSBis, BSBri)

# Source data (paths relative to data_root)
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
tsk:         "data/tsk_xrefs.json"

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
├── main.py              # entry point: CLI, config, writer factory
├── composer.py          # BibleComposer — loads sources, yields aligned tokens
├── models.py            # data classes: SourceToken, SourceWord, AlignedToken, …
├── verse_formatter.py   # VerseFormatter ABC + one concrete class per output target × style
│                        #   ESwordIntralinearFormatter
│                        #   ESwordReverseInterlinearFormatter
│                        #   MySwordIntralinearFormatter
│                        #   MySwordStackedFormatter      (subclass of Intralinear)
│                        #   MySwordReverseInterlinearFormatter
├── bible_writer.py      # BibleWriter ABC (open / add_verse / write)
├── sqlite_writer.py     # SQLiteBibleWriter — shared SQLite base for e-Sword + MySword
├── esword_writer.py     # ESwordWriter — Mods table, Bible view, Notes table
├── mysword_writer.py    # MySwordWriter — Details table with CSS + VerseRules
├── osis_writer.py       # OSISWriter — incremental OSIS XML tree
├── translit.py          # make_transliterator() — Hebrew + Greek → Latin script
├── config.yaml          # default pipeline configuration
└── data/
    ├── bsb_annotations.json   # section headers and translator footnotes
    └── tsk_xrefs.json         # Treasury of Scripture Knowledge cross-references
```

---

## Architecture

```
                          config.yaml
                               │
                        BibleComposer
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

**`BibleComposer`** reads source TSVs and alignment JSON once and streams
`(osis_ref, tokens, header, xrefs)` tuples. Multiple writers consume the same
stream, so `--format all` reads the source data exactly once regardless of how
many output targets are active.

**`VerseFormatter`** owns the complete rendering contract for one output target:
- `render_verse()` — produces the HTML/GBF string stored in the DB
- `css` — styles exactly the tags that `render_verse()` emits
- `verse_rules` — MySword regex transforms applied at display time (must match the tags above)

All three must stay in sync. The writer filters inputs (headers, notes, xrefs)
before calling `render_verse()` so disabled features never produce tags and their
CSS rules are never exercised.

**`BibleWriter`** subclasses (`ESwordWriter`, `MySwordWriter`, `OSISWriter`) are
format-only: they manage the output file, schema, and any format-specific side
tables (e-Sword's `Notes` table, MySword's `Details` table). They contain no
rendering logic.

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
- **Macula Hebrew** © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-hebrew](https://github.com/Clear-Bible/macula-hebrew) — CC BY 4.0
- **Macula Greek** © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-greek](https://github.com/Clear-Bible/macula-greek) — CC BY 4.0
- **Clear Bible Alignments** © Clear Bible — [github.com/Clear-Bible/Alignments](https://github.com/Clear-Bible/Alignments) — CC BY 4.0
