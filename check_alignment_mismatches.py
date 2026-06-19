"""
check_alignment_mismatches.py

Scan NT alignment JSON for records where source (Greek) and target (BSB English)
verse IDs don't match, then classify each as:

  - VERSIFICATION: the source verse has no BSB tokens (content genuinely lives
                   in a different English verse — expected, not an error)
  - POSSIBLE ERROR: both the source verse AND target verse exist in the BSB,
                   suggesting the alignment crossed a verse boundary incorrectly

Source IDs look like: n49005014001  → verse 49005014
Target IDs look like:   49005013011  → verse 49005013
"""

import csv
import json
import re
from collections import defaultdict
from pathlib import Path

import yaml
from biblelib.book import Books


def load_config(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    data_root = Path(cfg.get("data_root", "../"))
    for testament in ("ot", "nt"):
        src = cfg["sources"][testament]
        for key in ("source", "alignment", "target"):
            src[key] = data_root / src[key]
    return cfg


BOOK_NUM_MAP = {book.usfmnumber: book.osisID for book in Books().values()}

def verse_id_to_ref(verse_id: str) -> str:
    book_num  = verse_id[:2]
    chapter   = int(verse_id[2:5])
    verse     = int(verse_id[5:8])
    book_name = BOOK_NUM_MAP.get(book_num, f"Book{book_num}")
    return f"{book_name} {chapter}:{verse}"


SOURCE_RE = re.compile(r'^n?(\d{8})\d+$')
TARGET_RE = re.compile(r'^(\d{8})\d+$')

def source_verse(sid: str) -> str | None:
    m = SOURCE_RE.match(sid)
    return m.group(1) if m else None

def target_verse(tid: str) -> str | None:
    m = TARGET_RE.match(tid)
    return m.group(1) if m else None


def load_target_verses(target_path: Path) -> set:
    """Return set of verse IDs (BBCCCVVV) that exist in the BSB target TSV."""
    verses = set()
    with open(target_path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            token_id = row.get('id', '')
            v = target_verse(token_id)
            if v:
                verses.add(v)
    print(f"  Loaded {len(verses):,} distinct verses from {target_path.name}")
    return verses


def check_nt(alignment_path: Path, target_path: Path):
    bsb_verses = load_target_verses(target_path)

    with open(alignment_path, encoding='utf-8') as f:
        data = json.load(f)

    versification = []
    errors        = []

    for rec in data['records']:
        src_verses = {source_verse(s) for s in rec['source'] if source_verse(s)}
        tgt_verses = {target_verse(t) for t in rec['target'] if target_verse(t)}

        if not src_verses or not tgt_verses or src_verses == tgt_verses:
            continue

        mismatch = {
            'id':         rec['meta']['id'],
            'src_verses': src_verses,
            'tgt_verses': tgt_verses,
            'source':     rec['source'],
            'target':     rec['target'],
        }

        # If ANY source verse exists in the BSB, both sides agree a verse is
        # there — this is a candidate alignment error, not a versification gap.
        src_in_bsb = src_verses & bsb_verses
        if src_in_bsb:
            errors.append(mismatch)
        else:
            versification.append(mismatch)

    print(f"\nNT: {len(data['records']):,} records, "
          f"{len(versification)} versification differences, "
          f"{len(errors)} possible errors")

    if versification:
        # Group by unique verse pair for a tidy summary
        pairs = defaultdict(list)
        for m in versification:
            key = (
                tuple(sorted(m['src_verses'])),
                tuple(sorted(m['tgt_verses'])),
            )
            pairs[key].append(m['id'])
        print(f"\nVERSIFICATION DIFFERENCES ({len(pairs)} unique verse pairs):")
        for (src_vs, tgt_vs), ids in sorted(pairs.items()):
            src_refs = ', '.join(verse_id_to_ref(v) for v in src_vs)
            tgt_refs = ', '.join(verse_id_to_ref(v) for v in tgt_vs)
            print(f"  {src_refs} → {tgt_refs}  ({len(ids)} records)")

    if errors:
        print(f"\nPOSSIBLE ERRORS ({len(errors)} records):")
        for m in errors:
            src_refs = ', '.join(verse_id_to_ref(v) for v in sorted(m['src_verses']))
            tgt_refs = ', '.join(verse_id_to_ref(v) for v in sorted(m['tgt_verses']))
            print(f"  {m['id']:20s}  src={src_refs}  tgt={tgt_refs}")
            print(f"    source tokens: {m['source']}")
            print(f"    target tokens: {m['target']}")


if __name__ == '__main__':
    cfg = load_config()
    check_nt(cfg['sources']['nt']['alignment'], cfg['sources']['nt']['target'])
