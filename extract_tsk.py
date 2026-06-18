
import csv
import json
from pathlib import Path

SOURCE  = Path("data/tskxref.tsv")
OUTPUT  = Path("data/tsk_xrefs.json")

def main(reader, xrefs):
    last_chapter = None
    _xref_cnt = 0
    for row in reader:
        chapter = f"{row['book']:>02}{row['chapter']:>03}"
        verse_ref = f"{chapter}{row['verse']:>03}"
        if chapter != last_chapter:
            # reset count after every chapter
            _xref_cnt = 0
            last_chapter = chapter
        _xref_cnt += 1
        if verse_ref not in xrefs:
            xrefs[verse_ref] = {_xref_cnt: row["xref"]}
        else:
            xrefs[verse_ref][_xref_cnt] = row["xref"]
    json.dump(xrefs, open(OUTPUT, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    print(f"Wrote TSK cross references to {OUTPUT}")


if __name__ == '__main__':
    xrefs = dict()
    with open(SOURCE, encoding='latin-1', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        main(reader, xrefs)

