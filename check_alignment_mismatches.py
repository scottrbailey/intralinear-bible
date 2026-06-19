"""
check_alignment_mismatches.py

Scan alignment JSON files for records where the source verse and target verse
don't match — i.e., Greek/Hebrew words from verse X are aligned to English
tokens in verse Y.

Source IDs look like: n49005014001  → verse 49005014
Target IDs look like:   49005013011  → verse 49005013
"""

import json
import re
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

def extract_verse_from_source(sid: str) -> str:
    """n49005014001 -> 49005014"""
    return re.sub(r'^n?0*', '', sid)[:8].lstrip('n')

SOURCE_RE = re.compile(r'^n?(\d{8})\d+$')
TARGET_RE = re.compile(r'^(\d{8})\d+$')

def source_verse(sid: str) -> str | None:
    m = SOURCE_RE.match(sid)
    return m.group(1) if m else None

def target_verse(tid: str) -> str | None:
    m = TARGET_RE.match(tid)
    return m.group(1) if m else None


def check_file(alignment_path: Path, label: str):
    with open(alignment_path, encoding='utf-8') as f:
        data = json.load(f)

    mismatches = []
    for rec in data['records']:
        src_verses = {source_verse(s) for s in rec['source'] if source_verse(s)}
        tgt_verses = {target_verse(t) for t in rec['target'] if target_verse(t)}

        if not src_verses or not tgt_verses:
            continue

        if src_verses != tgt_verses:
            mismatches.append({
                'id':          rec['meta']['id'],
                'src_verses':  src_verses,
                'tgt_verses':  tgt_verses,
                'source':      rec['source'],
                'target':      rec['target'],
            })

    print(f"\n{label}: {len(data['records']):,} records, {len(mismatches)} mismatches")
    for m in mismatches:
        src_refs = ', '.join(verse_id_to_ref(v) for v in sorted(m['src_verses']))
        tgt_refs = ', '.join(verse_id_to_ref(v) for v in sorted(m['tgt_verses']))
        print(f"  {m['id']:20s}  src={src_refs}  tgt={tgt_refs}")
        print(f"    source tokens: {m['source']}")
        print(f"    target tokens: {m['target']}")


if __name__ == '__main__':
    cfg = load_config()
    check_file(cfg['sources']['ot']['alignment'], 'OT')
    check_file(cfg['sources']['nt']['alignment'], 'NT')
