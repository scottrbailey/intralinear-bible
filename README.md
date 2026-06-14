# Intralinear Bible

A pipeline for generating intralinear and interlinear Bible modules for
[MySword](https://www.mysword.info/) (Android) and e-Sword, combining the
[Berean Standard Bible](https://berean.bible/) with inline Hebrew and Greek
transliteration linked to Strong's concordance.

## What It Produces

Running the pipeline generates up to three MySword module variants:

| Abbreviation | Description |
|---|---|
| `BSBi`  | Intralinear — transliteration as colored superscript links after each English word |
| `BSBis` | Intralinear Stacked — transliteration above original script, stacked display |
| `BSBi+` | Interlinear — GBF format with Hebrew/Greek script, transliteration, and English |

## Download

Pre-built modules are available on the
[Releases](https://github.com/scottrees/intralinear-bible/releases/latest) page.

To install in MySword:
1. Download the `.bbl.mybible` file
2. Copy it to the `MySword/Bibles/` folder on your Android device
3. Restart MySword and select the module from the Bible list

## Building From Source

### Prerequisites

You will need to clone the following repositories alongside this one:

```
parent/
├── intralinear-bible/      ← this repo
├── macula-hebrew/          ← https://github.com/Clear-Bible/macula-hebrew
├── macula-greek/           ← https://github.com/Clear-Bible/macula-greek
└── Alignments/             ← https://github.com/Clear-Bible/Alignments
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Configure

Copy and edit the config file:

```bash
cp config.yaml my_config.yaml
```

Key settings in `config.yaml`:

```yaml
output:
  format: "mysword"      # mysword, esword, osis
  mode:   "intralinear"  # intralinear, interlinear
books: null              # null = full Bible, or e.g. [Gen, Exod, Matt]
```

### Run

```bash
python main.py                    # uses config.yaml
python main.py my_config.yaml     # uses custom config
```

Output files are written to the `output/` directory.

## Transliteration Schemes

Hebrew transliteration scheme is configurable in `config.yaml`:

| Scheme          | Example       |
|-----------------|---------------|
| `brill_simple`  | be·re·shít    |
| `sbl_simple`    | bereʾshit     |
| `sbl_academic`  | bərēʾšîṯ      |
| `phonetic_dot`  | beh·reh·SHEET |

## Project Structure

```
intralinear-bible/
├── main.py              # pipeline entry point
├── translit.py          # Hebrew transliteration + make_transliterator()
├── sqlite_writer.py     # base SQLite writer and verse renderers
├── mysword_writer.py    # MySword .bbl.mybible writer
├── osis_renderer.py     # OSIS XML writer
├── config.yaml          # pipeline configuration
├── data/
│   └── bsb_annotations.json   # section headers and footnotes
└── output/              # generated modules (gitignored)
```

## License

This work is licensed under
[CC BY-SA 4.0](http://creativecommons.org/licenses/by-sa/4.0/).

### Attribution

- **Berean Standard Bible** © 2022 Bible Hub — [berean.bible](https://berean.bible/) — CC BY-SA 4.0
- **Macula Hebrew** © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-hebrew](https://github.com/Clear-Bible/macula-hebrew) — CC BY 4.0
- **Macula Greek** © Clear Bible / unfoldingWord — [github.com/Clear-Bible/macula-greek](https://github.com/Clear-Bible/macula-greek) — CC BY 4.0
- **Clear Bible Alignments** © Clear Bible — [github.com/Clear-Bible/Alignments](https://github.com/Clear-Bible/Alignments) — CC BY 4.0