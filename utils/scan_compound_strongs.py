"""
utils/scan_compound_strongs.py

Diagnostic (not part of the build pipeline): investigates 1 Sam 1:1's
"Ramathaim-zophim" rendering two separate, identically-labeled lemma
blocks in BTB-L2 (see docs/BSB_TABLES_SOURCE_ERRORS.md item 5).

This investigation went through several wrong turns worth keeping on
record, since each one ruled out a plausible-looking fix:

  1. parent_id groups (scan 1) -- the mechanism import_bsb_table.py
     already builds from the 'vvv'/". . ." continuation markers: a token
     whose English gloss is covered by a neighbor gets that neighbor's
     bsb_sort as parent_id. A group of 2+ members sharing one Strong's
     number looked at first like the signature of the bug -- but it
     drastically over-counts: it also matches Hebrew's
     infinitive-absolute-for-emphasis construction (e.g. מוֹת תָּמוּת,
     "dying you shall die" -> "you will surely die") and other
     same-word-repeated idioms, where both source tokens are genuinely
     independent occurrences each deserving their own annotation.

  2. Hyphenated glosses with a matching-Strong's neighbor (scan 2) -- an
     attempt to narrow scan 1 using English surface form. Confirmed
     unreliable: real compounds are inconsistently hyphenated in English
     (Ben-hadad vs. Mephibosheth, Chedorlaomer -- both genuine two-root
     compounds, neither hyphenated), so this both missed real cases and
     added noise of its own.

  3. parent_id groups restricted to members ALL tagged proper noun (scan
     3) -- ruled out by direct comparison against the real BIB+ app:
     Genesis 36:8's "Esau ... Esau" (same word, same Strong's, two
     tokens, both proper-noun tagged) renders as ONE combined block in
     BIB+, but Numbers 33:9's "Elim ... Elim" (also same Strong's, also
     both proper-noun tagged, but two genuinely different inflected
     forms) renders as TWO separate blocks. Proper-noun tagging doesn't
     track the real distinction; "distinct source_text" doesn't either
     (cantillation differs by syntactic position regardless of whether a
     word is repeated or not, so it produced false structural
     "differences" for Esau/Esau).

     The deeper realization from Esau/Elim: a token's own inflected form
     differing from its lemma is *normal* for every inflected word in the
     Bible -- Elim's two tokens showing different transliterations is not
     a bug, it's just two real inflected forms of one name, and BIB+
     correctly keeps their two blocks. "How many blocks should this
     render as" turned out to be the wrong question -- it also depends on
     information (which part of a merged English gloss belongs to which
     source word) that bsb_tables.tsv doesn't preserve at all, so
     replicating BIB+'s finer per-word splits isn't achievable from this
     data source regardless.

  4. What's actually anomalous, and the one this file's scan settled on:
     not "two tokens share a Strong's number" (normal for any repeated or
     multi-form word) but "the Strong's number's own dictionary lemma
     (strongs_lemma.transliteration) is itself a multi-word/hyphenated
     compound that doesn't match *either* token's own individual form."
     That only happens when the dictionary headword genuinely covers two
     fused roots (Ramathaim-zophim's lemma is the whole compound name,
     matching neither "Ramathaim" nor "Zophim" alone) -- never for an
     ordinary single-root word like Elim or Esau, however it's inflected
     or however many times it's repeated. Confirmed narrower than scans
     1-3 on both counts they got wrong: it doesn't fire for Esau (lemma
     is a single word, matches both tokens) or Elim (lemma is a single
     word, not multi-word/hyphenated at all).

     Fix implication, once this scan's count is confirmed against the
     real data: rather than changing how many annotation blocks render
     (which scan 3's Esau/Elim counterexample ruled out), suppress
     `lemma_translit` specifically for tokens hit by this scan -- the
     same fallback-to-word's-own-form mechanism LEMMA_SUPPRESSED_STRONGS
     already uses in verse_formatter/intralinear.py, just populated from
     this analysis instead of hand-picked. Scoped to L1's primary line
     and L2's secondary line (the only two consumers of lemma_translit;
     L3 never reads it) -- matches the observation that this only ever
     surfaced as a visible problem once L2 started showing the
     transliteration and the lemma side by side.

Usage:
    python utils/scan_compound_strongs.py [path/to/bsb_tables.db]
"""

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "bsb_tables.db"


def scan_parent_groups(conn: sqlite3.Connection) -> list[tuple]:
    cur = conn.execute("""
        SELECT bsb_sort, verse_id, strongs, english,
               COALESCE(parent_id, bsb_sort) AS owner
        FROM tokens
        WHERE strongs IS NOT NULL
        ORDER BY bsb_sort
    """)
    groups: dict[int, list] = {}
    for row in cur:
        groups.setdefault(row['owner'], []).append(row)

    hits = []
    for owner, members in groups.items():
        if len(members) < 2:
            continue
        strongs_set = {m['strongs'] for m in members}
        if len(strongs_set) == 1:
            owner_row = next((m for m in members if m['bsb_sort'] == owner), members[-1])
            hits.append((owner_row['verse_id'], owner_row['english'],
                         strongs_set.pop(), len(members)))
    return hits


def scan_parent_groups_proper_nouns(conn: sqlite3.Connection) -> list[tuple]:
    """Same as scan_parent_groups, restricted to all-proper-noun-tagged
    groups, plus a `distinct_script` flag: True means every member's own
    source_text differs from the others (Ramathaim =/= Zophim -- a genuine
    two-part compound), False means at least two members share the exact
    same source_text (the name is simply mentioned twice in the same
    stretch of text and merged into one English clause -- the same
    not-actually-a-bug pattern as the verb-emphasis idioms scan 1 catches,
    just landing on a repeated proper noun instead of a repeated verb).
    Read distinct_script=False hits as leads to verify by hand, not as
    confirmed instances of the compound-name bug.
    """
    cur = conn.execute("""
        SELECT bsb_sort, verse_id, strongs, english, source_text,
               parsing_short, parsing_full,
               COALESCE(parent_id, bsb_sort) AS owner
        FROM tokens
        WHERE strongs IS NOT NULL
        ORDER BY bsb_sort
    """)
    groups: dict[int, list] = {}
    for row in cur:
        groups.setdefault(row['owner'], []).append(row)

    def is_proper(row) -> bool:
        return 'proper' in (row['parsing_short'] or '').lower() or \
               'proper' in (row['parsing_full'] or '').lower()

    hits = []
    for owner, members in groups.items():
        if len(members) < 2:
            continue
        if not all(is_proper(m) for m in members):
            continue
        strongs_set = {m['strongs'] for m in members}
        if len(strongs_set) == 1:
            owner_row = next((m for m in members if m['bsb_sort'] == owner), members[-1])
            distinct_script = len({m['source_text'] for m in members}) == len(members)
            hits.append((owner_row['verse_id'], owner_row['english'],
                         strongs_set.pop(), len(members), distinct_script))
    return hits


def scan_hyphenated_neighbors(conn: sqlite3.Connection) -> list[tuple]:
    all_rows = conn.execute(
        "SELECT bsb_sort, verse_id, strongs, english FROM tokens ORDER BY bsb_sort"
    ).fetchall()
    pos_by_sort = {r['bsb_sort']: i for i, r in enumerate(all_rows)}

    hits = []
    for row in all_rows:
        if not row['english'] or '-' not in row['english'] or not row['strongs']:
            continue
        idx = pos_by_sort[row['bsb_sort']]
        for neighbor_idx in (idx - 1, idx + 1):
            if not (0 <= neighbor_idx < len(all_rows)):
                continue
            neighbor = all_rows[neighbor_idx]
            if neighbor['verse_id'] == row['verse_id'] and neighbor['strongs'] == row['strongs']:
                hits.append((row['verse_id'], row['english'], row['strongs']))
                break
    return hits


def scan_compound_lemma_mismatch(conn: sqlite3.Connection) -> list[tuple]:
    """The scan that matters -- see module docstring, item 4. Flags a
    parent_id group (2+ members, one shared Strong's number) only when
    strongs_lemma's own dictionary transliteration for that Strong's
    number is multi-word/hyphenated AND doesn't exactly equal any single
    member's own translit. Ordinary inflected-vs-lemma divergence (Elim,
    Esau, and every regularly inflected word in the Bible) never
    satisfies the "lemma itself is multi-word" half of that test, so it
    doesn't fire for them -- only for Strong's entries whose dictionary
    headword is itself a fused two-root compound.
    """
    cur = conn.execute("""
        SELECT bsb_sort, verse_id, strongs, english, translit, language,
               COALESCE(parent_id, bsb_sort) AS owner
        FROM tokens
        WHERE strongs IS NOT NULL
        ORDER BY bsb_sort
    """)
    groups: dict[int, list] = {}
    for row in cur:
        groups.setdefault(row['owner'], []).append(row)

    lemma_by_key = {
        (r['lang'], r['strongs']): r['transliteration']
        for r in conn.execute("SELECT strongs, lang, transliteration FROM strongs_lemma")
    }

    def lemma_for(row) -> str | None:
        lang = 'H' if row['language'] in ('H', 'A') else row['language']
        return lemma_by_key.get((lang, row['strongs']))

    hits = []
    for owner, members in groups.items():
        if len(members) < 2:
            continue
        strongs_set = {m['strongs'] for m in members}
        if len(strongs_set) != 1:
            continue
        lemma = lemma_for(members[0])
        if not lemma or (' ' not in lemma and '-' not in lemma):
            continue  # lemma isn't a multi-word/hyphenated compound -- ordinary divergence
        member_translits = {m['translit'] for m in members if m['translit']}
        if lemma in member_translits:
            continue  # lemma exactly matches one member's own form -- not the bug
        owner_row = next((m for m in members if m['bsb_sort'] == owner), members[-1])
        hits.append((owner_row['verse_id'], owner_row['english'],
                     strongs_set.pop(), lemma, len(members)))
    return hits


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('db', nargs='?', default=DEFAULT_DB, type=Path)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    print("=== [1] parent_id groups sharing one Strong's number (superseded -- see docstring item 1) ===")
    hits = scan_parent_groups(conn)
    print(f"  {len(hits)} total (list suppressed -- heavy false-positive rate, see item 1)\n")

    print("=== [2] hyphenated glosses with a matching-Strong's neighbor (superseded -- see docstring item 2) ===")
    hits2 = scan_hyphenated_neighbors(conn)
    print(f"  {len(hits2)} total (list suppressed -- heavy false-positive rate, see item 2)\n")

    print("=== [3] parent_id groups, all members proper-noun tagged (superseded -- see docstring item 3) ===")
    hits3 = scan_parent_groups_proper_nouns(conn)
    print(f"  {len(hits3)} total (list suppressed -- ruled out by the Esau/Elim BIB+ comparison, see item 3)\n")

    print("=== [4] compound-lemma mismatch against strongs_lemma (the scan that matters -- see docstring item 4) ===")
    hits4 = scan_compound_lemma_mismatch(conn)
    for verse_id, english, strongs, lemma, count in hits4:
        print(f"  {verse_id}  {english!r}  strongs={strongs}  lemma={lemma!r}  ({count} tokens)")
    print(f"  {len(hits4)} total")

    conn.close()


if __name__ == '__main__':
    main()
