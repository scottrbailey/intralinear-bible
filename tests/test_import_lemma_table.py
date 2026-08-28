"""
Tests for utils/import_lemma_table.py.
Run with: pytest tests/test_import_lemma_table.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "utils"))

from import_lemma_table import _parse_hebrew_lexicon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HEBREW_LEXICON = ROOT / "data" / "HebrewStrong.xml"


class TestCombiningGraphemeJoinerStripped:
    """H3389 Jerusalem's headword (יְרוּשָׁלִַ͏ם) carries a U+034F COMBINING
    GRAPHEME JOINER between two vowel points on the same consonant --
    purely typographic (pins their order for renderers that would
    otherwise reorder them), no phonetic content. Left in, it rendered as
    a literal glyph in strongs_lemma.transliteration and, being "uncased",
    tricked build_restored_names.py's str.title() into capitalizing the
    letter right after it mid-word ("Yerushalaim" -> "Yᵉrushalia͏M").
    Stripped in _parse_hebrew_lexicon() -- the one place this project
    pulls a headword out of this specific source file -- not in
    translit.py, which has no reason to know this XML file's own
    typographic conventions."""

    def test_no_cgj_in_parsed_headwords(self):
        lemmas = _parse_hebrew_lexicon(HEBREW_LEXICON)
        offenders = {strongs: word for strongs, word in lemmas.items() if '͏' in word}
        assert not offenders, f"CGJ leaked into parsed headword(s): {offenders}"

    def test_jerusalem_headword_clean(self):
        # Not a hand-typed Hebrew literal -- combining-mark byte order is
        # easy to get subtly wrong by hand (bit twice already this session:
        # a stray dagesh/qamats reorder broke an exception-set match, and
        # NFC normalization reorders them again at transliterate time
        # anyway) -- so derive "expected" from the raw file itself instead.
        raw = HEBREW_LEXICON.read_text(encoding='utf-8')
        assert '͏' in raw, "fixture assumption broken: source no longer has a CGJ at all"
        lemmas = _parse_hebrew_lexicon(HEBREW_LEXICON)
        assert '͏' not in lemmas.get('3389', '')
