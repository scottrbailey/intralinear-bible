"""
utils/compare_translit.py

Compare Hebrew transliteration schemes across unique Strong's numbers in Genesis.

Usage:
    python utils/compare_translit.py [path/to/macula-hebrew.tsv] [output.tsv]

Defaults:
    source: ../../macula-hebrew/WLC/tsv/macula-hebrew.tsv
    output:  ../output/translit_compare.tsv

Output columns (TSV):
    strongs   original  source   brill_simple   sbl_academic   phonetic_dot   bt_phonetic
"""

import csv
import sys
from pathlib import Path

import biblical_transliteration as bt

# Add parent dir so we can import translit
sys.path.insert(0, str(Path(__file__).parent.parent))
from translit import make_transliterator

SOURCE_DEFAULT = Path("../../macula-hebrew/WLC/tsv/macula-hebrew.tsv")
OUTPUT_DEFAULT = Path("../output/translit_compare.tsv")

GENESIS_PREFIX = "o01"   # book number in the xml:id
# Populate to focus on specific words
WATCHLIST = []

def main():
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE_DEFAULT
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT_DEFAULT

    brill = make_transliterator('brill_simple', 'SIMPLE')
    sbl_ac = make_transliterator('sbl_academic', 'SIMPLE')
    phon_d = make_transliterator('phonetic_dot', 'SIMPLE')
    phon_bt = make_transliterator('PHONETIC', 'SIMPLE')

    seen_strongs = set()
    rows = []

    with open(source_path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            token_id = row.get('xml:id', '')
            if not token_id.startswith(GENESIS_PREFIX):
                if seen_strongs:   # past Genesis, stop
                    break
                continue

            strongs = row.get('strongnumberx')
            if not strongs or strongs in seen_strongs:
                continue
            if WATCHLIST and strongs not in WATCHLIST:
                continue

            seen_strongs.add(strongs)
            text   = row.get('text', '')
            native = row.get('transliteration', '')


            rows.append({
                'strongs':      strongs,
                'original':     text,
                'source':       native,
                'brill_simple': brill(text, 'H'),
                'sbl_academic': sbl_ac(text, 'H'),
                'phonetic_dot': phon_d(text, 'H'),
                'bt_phonetic':  phon_bt(text, 'H'),
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'strongs', 'original', 'source', 'brill_simple', 'sbl_academic',
            'phonetic_dot', 'bt_phonetic',
        ], delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows):,} unique Strong's entries to {output_path}")


if __name__ == '__main__':
    main()
