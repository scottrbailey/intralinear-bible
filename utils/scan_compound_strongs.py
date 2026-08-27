"""
utils/scan_compound_strongs.py

Diagnostic (not part of the build pipeline): scans bsb_tables.db's tokens
table for cases where multiple source tokens are meant to display as one
compound English word but Strong's-number annotation still renders one
ruby/lemma block per token instead of one for the whole compound -- the
pattern behind 1 Sam 1:1's "Ramathaim-zophim" showing two separate blocks
(see docs/BSB_TABLES_SOURCE_ERRORS.md item 5).

Two independent scans, since they can catch different things:

  1. parent_id groups -- the exact mechanism import_bsb_table.py already
     uses for the 'vvv' ("continuation_before") marker: a token with no
     English gloss of its own defers to the next real-gloss token, and
     that owner's bsb_sort becomes every deferred token's parent_id.
     table_composer.py's _build_verse() already groups by this owner for
     the *English text* (so "Ramathaim-zophim" isn't duplicated) -- but it
     still calls _to_source_word() once per member row and appends all of
     them to the same AlignedToken.source_words, so the *annotation*
     (lemma/translit/Strong's link) still repeats once per member. A group
     of 2+ members that all share one Strong's number is the precise,
     structural signature of that: one compound entry, artificially split
     across as many annotation blocks as it has member tokens.

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

    print("=== parent_id groups sharing one Strong's number ===")
    hits = scan_parent_groups(conn)
    for verse_id, english, strongs, count in hits:
        print(f"  {verse_id}  {english!r}  strongs={strongs}  ({count} tokens)")
    print(f"  {len(hits)} total\n")

    print("=== hyphenated glosses with a matching-Strong's neighbor (leads, not confirmed hits) ===")
    hits2 = scan_hyphenated_neighbors(conn)
    for verse_id, english, strongs in hits2:
        print(f"  {verse_id}  {english!r}  strongs={strongs}")
    print(f"  {len(hits2)} total")

    conn.close()


if __name__ == '__main__':
    main()
