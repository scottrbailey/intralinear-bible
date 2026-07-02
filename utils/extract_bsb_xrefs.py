"""
extract_bsb_xrefs.py

Extract parallel-passage cross-references from BSB USX source files.

USX marks these as a <para style="r"> paragraph sitting between a section
heading and the first verse of that section, e.g.:

    <para style="s1">The Creation</para>
    <para style="r">(<ref loc="JHN 1:1-5">John 1:1-5</ref>; ...)</para>
    <para style="p"><verse style="v" number="1" />In the beginning...

The <para style="r"> itself isn't inside any verse, so each one is attached
to the next <verse> encountered afterward in the file. Footnote-embedded
<ref> tags (<note style="f">) are not touched here — those are already
captured in bsb_annotations.json.

Usage:
    python utils/extract_bsb_xrefs.py [--source DIR] [--output FILE] [--books CODE ...]
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bible_books import USX_TO_ABBREV, BOOK_ORDER, BOOK_NUMBER

DEFAULT_SOURCE = ROOT / "local" / "bsb_usx"
DEFAULT_OUTPUT = ROOT / "output" / "bsb_xrefs.json"

_LOC_RE = re.compile(r'^(\S+)\s+(.*)$')


def convert_loc(loc: str) -> str:
    """Convert a USX ref loc ('JHN 1:1-5') to display form ('Joh 1:1-5')."""
    m = _LOC_RE.match(loc.strip())
    if not m:
        return loc
    book_code, rest = m.groups()
    abbrev = USX_TO_ABBREV.get(book_code, book_code.capitalize())
    return f"{abbrev} {rest}"


def extract_book(usx_path: Path, xrefs: dict) -> int:
    """Parse one USX file, adding its cross-refs to xrefs. Returns count added."""
    root = ET.parse(usx_path).getroot()
    book_elem = root.find('.//book')
    book_code = book_elem.get('code') if book_elem is not None else None
    book_num  = BOOK_NUMBER.get(book_code)
    if not book_num:
        print(f"  Skipping {usx_path.name}: unrecognized book code {book_code!r}")
        return 0

    added         = 0
    current_chapter = None
    pending_refs  = []  # list of "Abbrev C:V; ..." strings awaiting a verse

    for elem in root.iter():
        if elem.tag == 'chapter' and elem.get('style') == 'c' and elem.get('number'):
            current_chapter = int(elem.get('number'))

        elif elem.tag == 'verse' and elem.get('number'):
            if pending_refs and current_chapter is not None:
                verse_id = f"{book_num}{current_chapter:03d}{int(elem.get('number')):03d}"
                verse_xrefs = xrefs.setdefault(verse_id, {})
                for refs in pending_refs:
                    verse_xrefs[str(len(verse_xrefs) + 1)] = refs
                    added += 1
                pending_refs = []

        elif elem.tag == 'para' and elem.get('style') == 'r':
            locs = [child.get('loc') for child in elem
                    if child.tag == 'ref' and child.get('loc')]
            if locs:
                pending_refs.append('; '.join(convert_loc(loc) for loc in locs))

    if pending_refs:
        print(f"  Warning: {usx_path.name} has {len(pending_refs)} "
              f"r-tag(s) with no following verse; dropped")

    return added


def main(source_dir: Path, output_path: Path, books_filter=None):
    xrefs = {}
    total = 0
    order = books_filter or BOOK_ORDER
    for book_code in order:
        usx_path = source_dir / f"{book_code}.usx"
        if not usx_path.exists():
            continue
        added = extract_book(usx_path, xrefs)
        total += added
        print(f"  {book_code}: {added} cross-references")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(xrefs, f, indent=2, ensure_ascii=False)
    print(f"\nWrote {total} cross-references for {len(xrefs)} verses to {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE,
                         help="Directory of [BOOK].usx files (default: local/bsb_usx)")
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                         help="Output JSON path (default: output/bsb_xrefs.json)")
    parser.add_argument('--books', nargs='+', default=None,
                         help="Limit to these USX book codes, e.g. --books GEN MAT")
    args = parser.parse_args()
    main(args.source, args.output, args.books)
