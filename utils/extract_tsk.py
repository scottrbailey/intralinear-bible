import csv
import json
import re
from pathlib import Path

SOURCE = Path("data/tskxref.tsv")
OUTPUT = Path("../data/tsk_xrefs.json")

# TSK abbreviation → standard display abbreviation
TSK_ABBREV = {
    'ge':   'Gen', 'ex':   'Exo', 'le':   'Lev', 'nu':   'Num', 'de':   'Deu',
    'jos':  'Jos', 'jud':  'Jdg', 'ru':   'Rut', '1sa':  '1Sa', '2sa':  '2Sa',
    '1ki':  '1Ki', '2ki':  '2Ki', '1ch':  '1Ch', '2ch':  '2Ch', 'ezr':  'Ezr',
    'ne':   'Neh', 'es':   'Est', 'job':  'Job', 'ps':   'Psa', 'pr':   'Pro',
    'ec':   'Ecc', 'so':   'Sol', 'isa':  'Isa', 'jer':  'Jer', 'la':   'Lam',
    'eze':  'Eze', 'da':   'Dan', 'ho':   'Hos', 'joe':  'Joe', 'am':   'Amo',
    'ob':   'Oba', 'jon':  'Jon', 'mic':  'Mic', 'na':   'Nah', 'hab':  'Hab',
    'zep':  'Zep', 'hag':  'Hag', 'zec':  'Zec', 'mal':  'Mal', 'mt':   'Mat',
    'mr':   'Mar', 'lu':   'Luk', 'joh':  'Joh', 'ac':   'Act', 'ro':   'Rom',
    '1co':  '1Co', '2co':  '2Co', 'ga':   'Gal', 'eph':  'Eph', 'php':  'Php',
    'col':  'Col', '1th':  '1Th', '2th':  '2Th', '1ti':  '1Ti', '2ti':  '2Ti',
    'tit':  'Tit', 'phm':  'Phm', 'heb':  'Heb', 'jas':  'Jas', '1pe':  '1Pe',
    '2pe':  '2Pe', '1jo':  '1Jo', '2jo':  '2Jo', '3jo':  '3Jo', 'jude': 'Jude',
    're':   'Rev',
}

_REF_RE = re.compile(r'([0-9]?[a-z]+)\s+(\d+):(\d+(?:-\d+)?)((?:,\d+(?:-\d+)?)*)')

def normalize_refs(raw: str) -> str:
    """Expand TSK abbreviated refs into clean semicolon-separated verse refs.

    Comma notation is expanded into separate entries (same book and chapter):
      'ps 33:6,9'     → 'Psa 33:6; Psa 33:9'
    Ranges stay as one entry:
      'pr 8:22-24'    → 'Pro 8:22-24'
    Single-chapter books:
      'jude 3'        → 'Jude 3'
    """
    entries = []
    for ref in raw.split(';'):
        ref = ref.strip()
        m = _REF_RE.match(ref)
        if m:
            abbrev  = TSK_ABBREV.get(m.group(1), m.group(1).capitalize())
            chapter = m.group(2)
            first   = m.group(3)
            rest    = m.group(4)
            entries.append(f"{abbrev} {chapter}:{first}")
            if rest:
                for extra in rest.lstrip(',').split(','):
                    entries.append(f"{abbrev} {chapter}:{extra}")
        elif re.match(r'([0-9]?[a-z]+)\s+(\d+)$', ref):
            bk, vn = ref.split()
            abbrev = TSK_ABBREV.get(bk, bk.capitalize())
            entries.append(f"{abbrev} {vn}")
        elif ref:
            entries.append(ref)
    return '; '.join(entries)


def main(reader, xrefs):
    last_chapter = None
    _xref_cnt = 0
    for row in reader:
        chapter   = f"{row['book']:>02}{row['chapter']:>03}"
        verse_ref = f"{chapter}{row['verse']:>03}"
        if chapter != last_chapter:
            _xref_cnt = 0
            last_chapter = chapter
        _xref_cnt += 1
        if not row['xref']:
            continue
        normalized = normalize_refs(row['xref'])
        if verse_ref not in xrefs:
            xrefs[verse_ref] = {_xref_cnt: normalized}
        else:
            xrefs[verse_ref][_xref_cnt] = normalized
    json.dump(xrefs, open(OUTPUT, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    print(f"Wrote TSK cross references to {OUTPUT}")


if __name__ == '__main__':
    xrefs = dict()
    with open(SOURCE, encoding='latin-1', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        main(reader, xrefs)
