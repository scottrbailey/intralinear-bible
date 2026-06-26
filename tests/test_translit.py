"""
Tests for Hebrew and Greek transliteration.
Run with: pytest tests/test_translit.py
"""
import unicodedata
import pytest
import translit

def nfc(s: str) -> str:
    return unicodedata.normalize('NFC', s)


@pytest.fixture(scope="module")
def tl():
    fn = translit.make_transliterator("phonetic_dot")
    return lambda word: fn(word, "phonetic_dot")


@pytest.fixture(scope="module")
def gk():
    """Greek transliterator (phonetic_dot scheme, proper-noun casing)."""
    fn = translit.make_transliterator("phonetic_dot")
    return lambda word, proper=True: nfc(fn(word, "G", is_proper=proper))


# ── Qamats gadol vs qamats qatan ────────────────────────────────────────────

class TestQamatsGadol:
    """Monosyllabic words with qamats must be gadol (ā, rendered 'a'), never qatan (ō, 'o').
    A monosyllabic word is inherently accented, so qamats qatan is impossible.
    All examples carry cantillation or meteg to make the accent explicit."""

    CASES = [
        # (Hebrew, strongs, expected_substr, label)
        ("שָׁ֔ם",   "H8033", "shám",  "sham — there"),
        ("רָֽע",    "H7451", "rá",    "ra — evil"),
        ("נָ֥ע",    "H5303", "ná",    "na — wanderer"),
        ("נָ֖ד",    "H5110", "nád",   "nad — wanderer"),
        ("אָ֣ז",    "H0227", "áz",    "az — then"),
        ("חָ֥ם",    "H2526", "chám",  "Ham — proper name"),
        ("נָ֣א",    "H4994", "ná",    "na — please"),
        ("הָ֑ם",    "H1990", "hám",   "Ham — place name"),
        ("דָּֽן",   "H1835", "dán",   "Dan — proper name"),
        ("גָ֑ד",    "H1409", "gád",   "Gad — proper name"),
        ("גָ֑ל",    "H1530", "gál",   "gal — wave"),
        ("בָ֞ר",    "H1250", "vár",   "bar — grain"),
        ("עָֽז",    "H5794", "áz",    "az — strong"),
    ]

    @pytest.mark.parametrize("hebrew,strongs,substr,label", CASES)
    def test_monosyllabic_qamats_is_gadol(self, tl, hebrew, strongs, substr, label):
        result = tl(hebrew)
        assert substr in result, (
            f"{strongs} {label}: expected '{substr}' in output, got {result!r}"
        )


class TestQamatsQatanLegitimate:
    """Multi-syllable words where qamats qatan IS correct (closed, unaccented syllable).
    We should NOT regress these."""

    CASES = [
        # הָיְתָ֥ה — was the original bug trigger; first qamats is gadol
        ("הָיְתָ֥ה", "H1961", "táh", "hayetah — she was (accent on last syllable)"),
        # כָּל־ in construct with following word often appears; standalone test
        ("כָּל", "H3605", "kol", "kol — all/every"),
    ]

    @pytest.mark.parametrize("hebrew,strongs,substr,label", CASES)
    def test_qamats_qatan_preserved(self, tl, hebrew, strongs, substr, label):
        result = tl(hebrew)
        assert substr in result, (
            f"{strongs} {label}: expected '{substr}' in output, got {result!r}"
        )


# ── Pe / Samekh paragraph-marker detection ──────────────────────────────────

class TestPeSamekh:
    """Pe (פ) and Samekh (ס) are paragraph markers ONLY when they appear as
    standalone tokens (no other Hebrew consonant in the same token).  When they
    appear inside a real word they must be transliterated normally."""

    CASES = [
        # Final samekh — vowel carried by previous syllable or by meteg/cantillation
        ("חָמָֽס",   "H2555", "s",  "Hamas — violence (final samekh)"),
        ("תִירָֽס",  "H8494", "s",  "Tiras — proper name (final samekh+meteg)"),
        ("כּוֹס",    "H3563", "s",  "kos — cup (final samekh no vowel)"),
        ("אָפֵ֖ס",   "H0656", "s",  "afes — to cease (final samekh)"),
        ("מַס",      "H4522", "s",  "mas — tribute (final samekh bare)"),
        # Medial samekh with dagesh forte
        ("יְכֻסּ֗וּ", "H3680", "ss", "yekhusu — will cover (medial samekh dagesh forte)"),
        # Initial samekh
        ("סוּתֽ",    "H5497", "s",  "suth — garment (initial samekh)"),
        ("אֲסוּרִ֑ים", "H0631", "s", "asurim — prisoners (medial samekh)"),
        # Initial pe
        ("פ֥וּט",    "H6316", "f",  "Put — proper name (initial pe)"),
        ("פּ֥וֹטִיפַר", "H6318", "p", "Potiphar (initial pe+dagesh)"),
        # Medial pe with dagesh forte
        ("צִפּ֥וֹר",  "H6833", "pp", "tsippor — bird (medial pe dagesh forte)"),
    ]

    @pytest.mark.parametrize("hebrew,strongs,substr,label", CASES)
    def test_pe_samekh_in_word_not_skipped(self, tl, hebrew, strongs, substr, label):
        result = tl(hebrew)
        assert substr in result, (
            f"{strongs} {label}: expected '{substr}' in output, got {result!r}"
        )

    def test_standalone_pe_skipped(self, tl):
        """Standalone פ (Petuchah marker) with no vowel should produce empty output."""
        standalone_pe = "פ"  # bare pe, no vowel, no adjacent consonants
        result = tl(standalone_pe)
        assert result == "", f"Standalone pe should be empty, got {result!r}"

    def test_standalone_samekh_skipped(self, tl):
        """Standalone ס (Setumah marker) with no vowel should produce empty output."""
        standalone_samekh = "ס"
        result = tl(standalone_samekh)
        assert result == "", f"Standalone samekh should be empty, got {result!r}"


# ── Doubled-consonant syllable separator ────────────────────────────────────

class TestDoubledConsonant:
    """A word-initial doubled consonant should NOT produce a spurious syllable dot."""

    def test_mayim_no_leading_dot(self, tl):
        """מַיִם (H4325, water) — begins with mem-dagesh: should be 'mmá·yim', not 'm·má·yim'."""
        result = tl("מַּ֫יִם")
        assert not result.startswith("m·"), (
            f"Spurious leading syllable dot: {result!r}"
        )


# ── Greek syllabification ────────────────────────────────────────────────────

class TestGreekSyllabification:
    """Greek transliteration should include syllable separators (ꞏ) placed
    before the onset consonant(s) of each syllable, and a stress marker (´)
    on the accented vowel, derived from the original Greek diacritics."""

    SEP = 'ꞏ'   # sinological dot U+A78F
    ACC = '́'  # combining acute

    CASES = [
        # (Greek, expected_NFC, label)
        # Basic syllabification
        ('ποταμός',      'poꞏtaꞏmós',       'potamos — river, oxytone'),
        ('ἄνθρωπος',     'ánꞏthroꞏpos',     'anthropos — νθρ cluster: θρ onset, ν coda'),
        ('εὐαγγέλιον',   'euꞏanꞏgéꞏliꞏon', 'euangelion — gospel, 5 syllables'),
        ('αἷμα',         'haíꞏma',          'haima — blood, diphthong nucleus'),
        ('Ἰησοῦς',       'Iꞏeꞏsoús',        'Iesous — Jesus, 3 syllables'),
        ('πνεῦμα',       'pneúꞏma',         'pneuma — spirit, initial cluster'),
        ('λόγος',        'lóꞏgos',          'logos — word, paroxytone'),
        ('θεός',         'theꞏós',          'theos — God, hiatus not merged'),
        ('ἀγάπη',        'aꞏgáꞏpe',         'agape — love, proparoxytone'),
        ('κύριος',       'kýꞏriꞏos',        'kurios — lord, 3 syllables'),
        ('Ἰσραήλ',       'Isꞏraꞏél',        'Israel — 3 syllables'),
        ('Μωϋσῆς',       'Moꞏyꞏsés',        'Moyses — Moses, dialytika'),
        # Digraph fix: φ/χ/θ as single consonant must not be split
        ('Ἄφες',         'Áꞏphes',          'Aphes — φ single, goes with next syllable'),
        ('διαφέρετε',    'diꞏaꞏphéꞏreꞏte',  'diapherete — φ between vowels, not split'),
        ('καταφρονήσει', 'kaꞏtaꞏphroꞏnéꞏsei','kataphronEsei — φρ onset cluster intact'),
        ('ἀμφίβληστρον', 'amꞏphíꞏbleꞏstron', 'amphiblestron — μφ: φ onset, μ coda'),
        # Iota subscript: bt renders as explicit i, must not create phantom syllable
        ('ᾠκοδόμησεν',   'oiꞏkoꞏdóꞏmeꞏsen', 'oikodomesen — ᾠ subscript iota counted'),
        ('χρῄζετε',      'chreíꞏzeꞏte',     'chreizete — ῄ subscript iota counted'),
        ('ὥρᾳ',          'hóꞏrai',          'horai — ᾳ subscript iota counted'),
    ]

    @pytest.mark.parametrize("greek,expected,label", CASES)
    def test_syllabification(self, gk, greek, expected, label):
        result = gk(greek)
        assert result == expected, (
            f"{label}: got {result!r}, expected {expected!r}"
        )

    def test_no_sep_monosyllable(self, gk):
        """Monosyllabic words should have no syllable separator."""
        result = gk('ἐν')   # 'en' — in/on
        assert self.SEP not in result, f"Unexpected sep in monosyllable: {result!r}"

    def test_lowercase_common_noun(self, gk):
        """Common nouns (is_proper=False) should start lowercase."""
        result = gk('λόγος', proper=False)
        assert result[0].islower(), f"Common noun not lowercased: {result!r}"
