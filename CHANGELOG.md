# Changelog

## [1.1.0] - 2026-07-05

### Added
- **`TableComposer` pipeline** (`table_composer.py`, `utils/import_bsb_table.py`, `data/bsb_tables.db`): an alternative to `AlignmentComposer`'s live macula-hebrew/macula-greek/Alignments join — parses the full BSB interlinear export (`bsb_tables.tsv`, from berean.bible/downloads.htm) into a normalized SQLite `tokens`/`verses` database once, then reads it directly. No external repos to clone (85MB vs. the ~4.5GB `macula-hebrew` alone), and now the recommended starting point. `composer` in `config.yaml` (or `--composer`) selects it explicitly; when not set, it's auto-detected from whether `table_db` exists on disk, so a built database is picked up with zero config changes.
- **`data/books.db`** (`utils/build_books_table.py`): replaces `bible_books.py` with a real book-metadata table (osis_id, display_abbrev, usfm_number, testament, canon_order) derived from `biblelib`, fixing a latent fragility in `composer.py` that sliced biblelib's raw iteration order at magic-number positions to separate OT/NT.
- **`VerseFormatter.render_header()` / `parse_headers()`**: parses the raw `Hdg` column's `<p class=|hdg|>...` wrapper into `(class, text)` segments. e-Sword skips the main `hdg`/`suphdg` section headings — its own built-in pericope display already shows them, and rendering both would double them up — rendering only `acrostic`/`ihdg`/`subhdg` inline; MySword renders every class via its own `<TS>...<Ts>` title tag instead.
- **`VerseFormatter.transform_english()` / `bracket_replacement` / `brace_replacement`**: independently strip, keep, or restyle the BSB text's two distinct translator-supplied-word markers — `[brackets]` (broadly supplied words: pronouns, articles, referents) and `{braces}` (English auxiliary/modal verbs implied by the source verb's own tense/mood, e.g. `{will}`, `{do}`, `{let}` — a separate, narrower category from brackets).
- **`Par`-column paragraph styling** (`AlignedToken.par_class`): Psalm superscriptions (`pshdg`), quoted inscriptions (`inscrip`), and liturgical refrains (`selah`) now render their English in italics, without touching the adjacent source-word/transliteration markup. Tracked as forward state across the whole verse stream, since these paragraphs routinely span more than one verse.
- **Cross-references for `TableComposer`**: `verses.crossref`'s raw HTML is converted at import time into the same plain `"Joh 1:1-5; Heb 11:1-3"` shape `AlignmentComposer`'s `bsb_xrefs.json` already used. New `Reference` dataclass, `parse_reference()`, `transform_reference()`, `render_crossref()` replace the old ad hoc xref-marker helpers; whole-chapter-range and book-span references (no single verse target) anchor to the range's first chapter where the format supports a separate label/target (MySword's `RX` tag), or fall back to plain non-linked text (e-Sword's `ref` tag).
- **Opt-in red-letter (words of Christ)**: `output.red_letter` in `config.yaml` (off by default — the source data doesn't mark God's direct OT speech the same way, so red-letter here is NT-only by construction). Tracked on `AlignedToken.is_red`, independently of `par_class` since it can ride along with any paragraph type. Renders using each app's own native markup — e-Sword's `<red>...</red>`, MySword's `<FR>...<Fr>` — so the reader's own display-setting toggle controls visibility, not a fixed CSS color.
- **`sample_{format}_{abbrev}.html`** preview files: CSS + the first verse from each testament, written next to each build's output and reported once by path — replaces the old `xml.etree`-based console pretty-printer, which choked on most real output (our HTML isn't well-formed XML) and dumped a parse error plus raw markup to console for nearly every build.
- **`docs/TABLE_COMPOSER_STATUS.md`** and **`docs/BSB_TABLES_SOURCE_ERRORS.md`**: living status/known-issues tracker for the new pipeline, and a report of confirmed data-entry errors in `bsb_tables.tsv` itself worth flagging upstream.

### Changed
- `BibleComposer` renamed to `AlignmentComposer`; extracted a `Composer` ABC so `TableComposer` and `AlignmentComposer` are interchangeable from the writers'/formatters' point of view.
- MySword note ids remapped to small numbers that reset each chapter (matching e-Sword's existing convention) instead of leaking `TableComposer`'s raw, large `bsb_sort`-based ids straight into `<RF q=...>`.
- MySword now renders section headers by default (previously skipped, matching e-Sword's doubling workaround — which MySword doesn't actually need).

### Fixed
- Bare-ellipsis misdetection in `utils/import_bsb_table.py`: a normalization-ordering bug was collapsing the real `. . .` continuation marker's own internal spacing before the ellipsis-artifact check could see it, making 11 rows look like an unrelated bug.
- 116 rows had berean.bible's own inline verse-number-anchor HTML leaking into the `BegQ` column instead of a real quote mark (e.g. Psalm 121:1 rendering literal `<span class=|reftext|>...` right before "I lift up").
- `par_class`/`is_red` paragraph state was incorrectly reset every verse instead of persisting across verse boundaries — a paragraph (red-letter passages especially) routinely spans more than one verse. Red-letter token count across the full Bible went from 15,373 (buggy) to 28,999 (fixed) once verses stopped losing track of state they'd already entered.

## [1.0.2] - 2026-07-02

### Added
- **BSB parallel-passage cross-references** (`utils/extract_bsb_xrefs.py`, `data/bsb_xrefs.json`): replaces the ~500,000-entry TSK cross-reference set with a much smaller, more useful set extracted directly from BSB USX source files' `<para style="r">` section references. Each is attached to the next verse encountered after it in the file.
- **`bible_books.py`**: canonical USX-code / display-abbreviation / book-number (1-66) table shared by the extraction script and the verse formatters, so the abbreviation scheme is defined once and book numbers are derived from `biblelib` rather than hand-counted.
- **MySword cross-reference rendering**: cross-references now render as real, tappable links using MySword's `<RX b.c.v[-v]>` tag, with each verse's group of references nested inside one `<RF q=R{key}>...<Rf>` popup (plus the reference's own display text, since bare `RX` tags have no visible label of their own).
- **MySword lemma rendering reworked to match e-Sword**: both now emit `<span class="ilb"><ruby>...` markup directly (previously MySword emitted `<lemma sn="...">` tags transformed by a VerseRules regex at render time).

### Changed
- Config key `tsk` renamed to the generic `crossrefs`, now pointing at `data/bsb_xrefs.json` by default.
- `output.xref` defaults to `1` (cross-references shown at the start of the verse) instead of `0`.

### Fixed
- MySword builds were silently dropping cross-references entirely: `MySwordWriter` never forwarded `xref_placement` to the renderer, and none of the MySword formatters referenced their `xrefs` parameter at all.
- `textwrap.dedent()` was a no-op on the MySword CSS blocks because one line was tab-indented while its siblings used spaces, so the dedent call found no common prefix to strip.
- MySword cross-reference popups rendered blank on-device: the popup content was nothing but bare `<RX>` milestone tags with no accompanying visible text for the popup to display.

## [1.0.1] - 2026-06-25

### Added
- **Greek syllabification and stress markers**: `add_greek_syllable_markers()` function maps Greek nuclei (vowels, diphthongs, iota subscript) to the bt transliteration output and inserts `ꞏ` syllable separators and combining-acute stress markers derived from original Greek diacritics. Activated automatically for any Hebrew scheme that defines `syllable_sep`/`stress_marker` (e.g. `phonetic_dot`).
- **Regression test suite** (`tests/test_translit.py`): 50 tests covering qamats gadol/qatan, pe/samekh paragraph markers, doubled-consonant separator, and Greek syllabification.
- **e-Sword reverse interlinear** (`.bbli`): English on top, source script / transliteration / Strong's / morphology below each word, using custom `<qi>`, `<e>`, `<lem>` tags styled via the `Mods` CSS table
- **MySword interlinear stacked** variant: original script stacked under transliteration using `<ruby>/<rt>` tags with `inline-flex; flex-direction: column-reverse` — works for both Hebrew and Greek
- **Verse preview**: first verse of each testament is pretty-printed during processing; MySword also applies VerseRules transforms and shows both intralinear and stacked output
- **Transliteration Comparison** - (`utils\compare_translit.py`) transliterates all words in Genesis/Matthew in several schemes and outputs to tsv file for comparison.
- **Reorganized and refactored** - cleaned up code structure.

### Fixed
- Strong's suffixes (e.g. `H871a`) stripped before normalization so dictionary links resolve correctly
- Strong's corrected to for Aramaic words 
- Doubled tail text in verse preview (ET.tostring already includes tail)
- Monosyllabic qamats qatan over-firing: qamats in a monosyllabic word is always gadol (ā); added cantillation/meteg guard in `is_qamats_qatan` to prevent it from returning `True` in an inherently accented syllable.
- Pe/Samekh paragraph-marker false positives: ס and פ inside real words were incorrectly skipped as section markers. The skip now only fires when the token contains no other Hebrew consonants.
- Greek digraph splits: φ→ph, χ→ch, θ→th etc. were split across syllable boundaries (e.g. `taphꞏro` instead of `taꞏphro`). Fixed by using Greek consonant count + `_gk_onset_length()` + atomic digraph walk.
- Iota subscript phantom syllables: ᾳ/ῃ/ῳ were counted as single-vowel nuclei, producing an extra syllable. Fixed by detecting the combining ypogegrammeni (U+0345) and marking the nucleus as consuming 2 xlit vowels.

