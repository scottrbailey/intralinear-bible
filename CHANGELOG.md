# Changelog

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

