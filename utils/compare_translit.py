"""
utils/compare_translit.py

Compare Hebrew transliteration schemes across unique Strong's numbers in Genesis.

Usage:
    python utils/compare_translit.py [path/to/macula-hebrew.tsv] [output.tsv]

Defaults:
    source: ../macula-hebrew/WLC/tsv/macula-hebrew.tsv
    output: data/translit_compare.tsv

Output columns (TSV):
    strongs   original   brill_simple   sbl_academic   phonetic_dot   bt_phonetic   native
"""

import csv
import sys
from pathlib import Path

import biblical_transliteration as bt

# Add parent dir so we can import translit
sys.path.insert(0, str(Path(__file__).parent.parent))
from translit import HebrewTransliterator

SOURCE_DEFAULT = Path("../macula-hebrew/WLC/tsv/macula-hebrew.tsv")
OUTPUT_DEFAULT = Path("data/translit_compare.tsv")

GENESIS_PREFIX = "01"   # book number in the xml:id


def main():
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE_DEFAULT
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT_DEFAULT

    brill   = HebrewTransliterator('brill_simple')
    sbl_ac  = HebrewTransliterator('sbl_academic')
    phon_d  = HebrewTransliterator('phonetic_dot')

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

            strongs = row.get('strongnumberx') or row.get('strong') or row.get('strongs') or ''
            if not strongs or strongs in seen_strongs:
                continue

            seen_strongs.add(strongs)
            text   = row.get('text', '')
            native = row.get('translit', '')

            bt_result = bt.hebrew(text, bt.HebrewOptions(scheme=bt.HebrewScheme.PHONETIC))

            rows.append({
                'strongs':      strongs,
                'original':     text,
                'brill_simple': brill.transliterate(text),
                'sbl_academic': sbl_ac.transliterate(text),
                'phonetic_dot': phon_d.transliterate(text),
                'bt_phonetic':  bt_result,
                'native':       native,
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'strongs', 'original', 'brill_simple', 'sbl_academic',
            'phonetic_dot', 'bt_phonetic', 'native',
        ], delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows):,} unique Strong's entries to {output_path}")


if __name__ == '__main__':
    main()
