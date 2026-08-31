"""
Tests for utils/build_restored_names.py.
Run with: pytest tests/test_build_restored_names.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "utils"))

from build_restored_names import (  # noqa: E402
    _query_hebrew_proper_from_tokens,
    apply_restorations,
)


def _make_conn(rows):
    """rows: list of (language, strongs, parsing_short, parsing_full)."""
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE tokens (
            language TEXT, strongs TEXT, parsing_short TEXT, parsing_full TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO tokens (language, strongs, parsing_short, parsing_full) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return conn


class TestTokenLevelProperNounDetection:
    """H3881 'Levite' is pos="a" (plain gentilic adjective) in
    HebrewStrong.xml's own headword tagging -- _parse_hebrew_proper_nouns
    skips it -- but its actual occurrences in the WLC text are parsed
    N-proper-ms, N-proper-mp, and (construct plural) N-mpc. This is a
    second, independent signal (Open Scriptures' token-level morphology,
    not the lexicon's per-lemma pos) for the same underlying question:
    should this Strong's number get a restored-names entry."""

    def test_picks_up_gentilic_tagged_proper_in_text(self):
        conn = _make_conn([
            ('H', '3881', 'N-proper-ms', None),
            ('H', '3881', 'N-proper-mp', None),
            ('H', '3881', 'N-mpc', None),
        ])
        assert _query_hebrew_proper_from_tokens(conn) == {'3881'}

    def test_one_proper_occurrence_is_enough(self):
        """Not every occurrence needs to be tagged proper -- construct
        forms often aren't -- one is enough signal."""
        conn = _make_conn([
            ('H', '3881', 'N-mpc', None),
            ('H', '3881', 'N-proper-mp', None),
        ])
        assert _query_hebrew_proper_from_tokens(conn) == {'3881'}

    def test_ignores_non_proper_strongs(self):
        conn = _make_conn([
            ('H', '1234', 'N-msc', None),
            ('H', '1234', 'V-Qal-Perf-3ms', None),
        ])
        assert _query_hebrew_proper_from_tokens(conn) == set()

    def test_checks_parsing_full_too(self):
        conn = _make_conn([
            ('H', '5555', None, 'Noun - proper - masculine plural'),
        ])
        assert _query_hebrew_proper_from_tokens(conn) == {'5555'}

    def test_includes_aramaic(self):
        conn = _make_conn([
            ('A', '9999', 'N-proper-ms', None),
        ])
        assert _query_hebrew_proper_from_tokens(conn) == {'9999'}

    def test_excludes_greek(self):
        conn = _make_conn([
            ('G', '2385', 'N-proper-ms', None),
        ])
        assert _query_hebrew_proper_from_tokens(conn) == set()


def _make_restorations_conn(lemma_rows, token_rows):
    """lemma_rows: (strongs, lang, find_text, replace_text).
    token_rows: (bsb_sort, language, strongs, english)."""
    conn = sqlite3.connect(':memory:')
    conn.execute("""
        CREATE TABLE strongs_lemma (
            strongs TEXT NOT NULL, lang TEXT NOT NULL,
            find_text TEXT, replace_text TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE tokens (
            bsb_sort INTEGER PRIMARY KEY, language TEXT, strongs TEXT,
            english TEXT, english_restored TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO strongs_lemma (strongs, lang, find_text, replace_text) VALUES (?, ?, ?, ?)",
        lemma_rows,
    )
    conn.executemany(
        "INSERT INTO tokens (bsb_sort, language, strongs, english) VALUES (?, ?, ?, ?)",
        token_rows,
    )
    conn.commit()
    return conn


class TestBracketedTranslatorSuppliedNames:
    """Mark 8:7's eulogesas (G2127, 'bless') is glossed '[Jesus] blessed'
    because the Greek aorist participle has no separate word for its
    subject -- there's no G2424 token at all here for the ordinary
    strongs-keyed pass to match against. The name only exists inside the
    bracket BSB's translators supplied, on a token whose own strongs is
    unrelated, so it needs its own pass keyed on wording, not strongs."""

    def test_bracket_name_restored_on_unrelated_strongs(self):
        conn = _make_restorations_conn(
            lemma_rows=[('2424', 'G', 'Jesus', 'Yeshua')],
            token_rows=[(1, 'G', '2127', '[Jesus] blessed')],
        )
        apply_restorations(conn)
        row = conn.execute("SELECT english_restored FROM tokens WHERE bsb_sort = 1").fetchone()
        assert row[0] == '[Yeshua] blessed'

    def test_bracket_untouched_when_no_curated_name_matches(self):
        conn = _make_restorations_conn(
            lemma_rows=[('2424', 'G', 'Jesus', 'Yeshua')],
            token_rows=[(1, 'G', '3004', 'and [He] said')],
        )
        apply_restorations(conn)
        row = conn.execute("SELECT english_restored FROM tokens WHERE bsb_sort = 1").fetchone()
        assert row[0] is None

    def test_own_strongs_and_bracket_both_restored(self):
        """A token can carry its own curated strongs match AND an unrelated
        bracketed name in the same gloss -- both should land."""
        conn = _make_restorations_conn(
            lemma_rows=[
                ('2424', 'G', 'Jesus', 'Yeshua'),
                ('4074', 'G', 'Peter', 'Kepha'),
            ],
            token_rows=[(1, 'G', '4074', '[Jesus] answered Peter')],
        )
        apply_restorations(conn)
        row = conn.execute("SELECT english_restored FROM tokens WHERE bsb_sort = 1").fetchone()
        assert row[0] == '[Yeshua] answered Kepha'

    def test_annotate_mode_inside_brackets(self):
        conn = _make_restorations_conn(
            lemma_rows=[('2424', 'G', 'Jesus', 'Yeshua')],
            token_rows=[(1, 'G', '2127', '[Jesus] blessed')],
        )
        apply_restorations(conn, annotate=True)
        row = conn.execute("SELECT english_restored FROM tokens WHERE bsb_sort = 1").fetchone()
        assert row[0] == '[Yeshua (Jesus)] blessed'

    def test_article_stripped_inside_brackets(self):
        conn = _make_restorations_conn(
            lemma_rows=[('3068', 'H', 'LORD', 'Yehovah')],
            token_rows=[(1, 'H', '1234', 'said [the LORD] to him')],
        )
        apply_restorations(conn)
        row = conn.execute("SELECT english_restored FROM tokens WHERE bsb_sort = 1").fetchone()
        assert row[0] == 'said [Yehovah] to him'
