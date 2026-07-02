# Changelog

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

