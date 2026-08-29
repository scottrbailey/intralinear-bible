"""
Tests for utils/build_restored_names.py.
Run with: pytest tests/test_build_restored_names.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "utils"))

from build_restored_names import _query_hebrew_proper_from_tokens  # noqa: E402


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
