"""
utils/compare_translit_greek.py

Compare Greek transliteration schemes across unique Strong's numbers in Matthew.

Usage:
    python utils/compare_translit_greek.py [path/to/macula-greek.tsv] [output.tsv]

Defaults:
    source: ../../macula-hebrew/Nestle1904/tsv/macula-greek-Nestle1904.tsv
    output:  ../output/translit_compare_greek.tsv

Output columns (TSV):
    strongs   original  source   SIMPLE  PHONETIC
"""

import csv
import sys
from pathlib import Path


# Add parent dir so we can import translit
sys.path.insert(0, str(Path(__file__).parent.parent))
from translit import make_transliterator

SOURCE_DEFAULT = Path("../../macula-greek/Nestle1904/tsv/macula-greek-Nestle1904.tsv")
OUTPUT_DEFAULT = Path("../output/translit_compare_greek.tsv")

MATTHEW_PREFIX = "n40"   # book number in the xml:id
# Populate to focus on specific words
WATCHLIST = ['4863', '4905', '5537', '2090', '1694', '3177', '2962', '1519', '897 ', '2414', '5590', '1484', '3986']

def main():
    source_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE_DEFAULT
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else OUTPUT_DEFAULT

    simple = make_transliterator('brill_simple', 'SIMPLE')
    # Passing in a Hebrew scheme with no stress marker/syllable sep to prevent syllabification
    phon_bt = make_transliterator('SIMPLE', 'PHONETIC')

    seen_strongs = set()
    rows = []

    with open(source_path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            token_id = row.get('xml:id', '')
            if not token_id.startswith(MATTHEW_PREFIX):
                if seen_strongs:   # past Matthew, stop
                    break
                continue

            strongs = row.get('strong')
            if not strongs or strongs in seen_strongs:
                continue
            if WATCHLIST and strongs not in WATCHLIST:
                continue

            seen_strongs.add(strongs)
            text   = row.get('text', '')
            rows.append({
                'strongs':      strongs,
                'original':     text,
                'simple':       simple(text, 'G'),
                'bt_phonetic':  phon_bt(text, 'G'),
            })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=[
            'strongs', 'original', 'simple', 'bt_phonetic',
        ], delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)

    print(f"Written {len(rows):,} unique Strong's entries to {output_path}")


if __name__ == '__main__':
    main()
