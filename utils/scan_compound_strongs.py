"""
utils/scan_compound_strongs.py

Diagnostic (not part of the build pipeline): scans bsb_tables.db's tokens
table for cases where multiple source tokens are meant to display as one
compound English word but Strong's-number annotation still renders one
ruby/lemma block per token instead of one for the whole compound -- the
pattern behind 1 Sam 1:1's "Ramathaim-zophim" showing two separate blocks
(see docs/BSB_TABLES_SOURCE_ERRORS.md item 5).

Three scans, since they can catch different things:

  1. parent_id groups -- the exact mechanism import_bsb_table.py already
     uses for the 'vvv' ("continuation_before") marker: a token with no
     English gloss of its own defers to the next real-gloss token, and
     that owner's bsb_sort becomes every deferred token's parent_id.
     table_composer.py's _build_verse() already groups by this owner for
     the *English text* (so "Ramathaim-zophim" isn't duplicated) -- but it
     still calls _to_source_word() once per member row and appends all of
     them to the same AlignedToken.source_words, so the *annotation*
     (lemma/translit/Strong's link) still repeats once per member. A group
     of 2+ members that all share one Strong's number is the structural
     signature of that -- but NOT a confirmed hit on its own: it also
     matches Hebrew's infinitive-absolute-for-emphasis construction
     (e.g. מוֹת תָּמוּת, "dying you shall die" -> "you will surely die")
     and other same-word-repeated idioms ("between you and me", "years"
     for a distributive "year by year"), where the two source tokens are
     genuinely independent occurrences of the same word that each deserve
     their own annotation -- collapsing those would be wrong, not a fix.
     This raw scan over-counts heavily for that reason (confirmed: the
     large majority of a real run's hits were this kind of verb/idiom
     repetition, not compound names).

  2. Hyphenated glosses with a matching-Strong's neighbor -- a broader
     heuristic that doesn't depend on parent_id at all, since a compound
     name might reach a hyphenated English rendering by some other route
     than the vvv mechanism. Flags an owner row whose own English gloss
     contains a hyphen when the immediately adjacent row (previous or
     next bsb_sort, same verse) shares its Strong's number. Broader nets
     both true positives the first scan might miss and false positives
     (a hyphenated single-word translation like "self-controlled" whose
     neighbor happens to share a Strong's number by coincidence) -- read
     its output as leads to check individually, not confirmed hits.

  3. parent_id groups restricted to members that are ALL tagged as proper
     nouns (parsing_short/parsing_full containing "proper", matching the
     "N-proper-fs"/"Noun - proper - feminine singular" tagging on both of
     1 Sam 1:1's Ramathaim-zophim tokens) -- excludes verb-emphasis and
     other same-word idioms structurally (they're never proper-noun
     tagged), rather than by guessing at English surface patterns like
     scan 2's hyphen check. This is the scan whose count should actually
     drive the fix's scope.

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


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('db', nargs='?', default=DEFAULT_DB, type=Path)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    print("=== [1] parent_id groups sharing one Strong's number (over-counts -- see docstring) ===")
    hits = scan_parent_groups(conn)
    for verse_id, english, strongs, count in hits:
        print(f"  {verse_id}  {english!r}  strongs={strongs}  ({count} tokens)")
    print(f"  {len(hits)} total\n")

    print("=== [2] hyphenated glosses with a matching-Strong's neighbor (leads, not confirmed hits) ===")
    hits2 = scan_hyphenated_neighbors(conn)
    for verse_id, english, strongs in hits2:
        print(f"  {verse_id}  {english!r}  strongs={strongs}")
    print(f"  {len(hits2)} total\n")

    print("=== [3] parent_id groups where every member is tagged a proper noun (the scan that matters) ===")
    hits3 = scan_parent_groups_proper_nouns(conn)
    confirmed = [h for h in hits3 if h[4]]
    suspect   = [h for h in hits3 if not h[4]]
    for verse_id, english, strongs, count, distinct_script in hits3:
        flag = "" if distinct_script else "  <-- SAME source_text repeated, likely not a real compound"
        print(f"  {verse_id}  {english!r}  strongs={strongs}  ({count} tokens){flag}")
    print(f"  {len(hits3)} total ({len(confirmed)} distinct-script, {len(suspect)} flagged for manual check)")

    conn.close()


if __name__ == '__main__':
    main()
