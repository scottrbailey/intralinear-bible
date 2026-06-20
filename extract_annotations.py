"""
extract_annotations.py

One-time script to extract section headers, footnotes, and their
positions from the BSB API and store them as bsb_annotations.json.

Headers are keyed by OSIS verse ref (e.g. "Gen.2.1") — the verse
they precede.

Footnotes are keyed by BSB target token ID (e.g. "01001002003") —
the token they attach to. End-of-verse notes use the last token ID
in the verse.

"""

import csv
import json
import re
import time
import urllib.request
from pathlib import Path

# ================== CONFIG ==================

API_BASE       = ""
BOOKS_ENDPOINT = f"{API_BASE}/books.json"
TARGET_OT      = Path("../Alignments/data/eng/targets/BSB/ot_BSB.tsv")
TARGET_NT      = Path("../Alignments/data/eng/targets/BSB/nt_BSB.tsv")
OUTPUT         = Path("data/bsb_annotations.json")
REQUEST_DELAY  = 0.25

# Set to a list of API book IDs to process only those books, e.g. ['GEN', 'MAT']
# None for full Bible
BOOKS_FILTER   = ['GEN']


# ================== BOOK ID MAPPING ==================

def build_api_to_osis_map() -> dict:
    """Map API book ID (GEN) to biblelib OSIS ID (Gen)."""
    from biblelib.book import Books
    result = {}
    for book in Books().values():
        usfm = getattr(book, 'usfmname', None) or getattr(book, 'usfm', None)
        if usfm:
            result[usfm.upper()] = book.osisID
    return result


def build_book_num_map() -> dict:
    """Map API book ID (GEN) to zero-padded book number ('01')."""
    from biblelib.book import Books
    result = {}
    for book in Books().values():
        usfm = getattr(book, 'usfmname', None) or getattr(book, 'usfm', None)
        if usfm:
            result[usfm.upper()] = book.usfmnumber
    return result


# ================== TARGET TSV INDEX ==================

def load_verse_token_index(ot_path: Path, nt_path: Path) -> dict:
    """Build index: verse_id (BBCCCVVV) -> list of (token_id, token_text) tuples.
    Excludes tokens marked exclude=True (punctuation not in alignment)."""
    index = {}
    for path in [ot_path, nt_path]:
        if not path.exists():
            print(f"  Warning: {path} not found, skipping")
            continue
        with open(path, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                token_id = row['id']
                verse_id = token_id[:8]
                if not row.get('exclude', ''):
                    index.setdefault(verse_id, []).append(
                        (token_id, row['text'])
                    )
    print(f"  Loaded token index for {len(index):,} verses")
    return index


# ================== API HELPERS ==================

def fetch_json(url: str) -> dict:
    """Fetch JSON from URL."""
    req = urllib.request.Request(url, headers={'User-Agent': 'BSBIntralinear/1.0'})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8'))


def osis_ref(book_osis: str, chapter: int, verse: int) -> str:
    return f"{book_osis}.{chapter}.{verse}"


def verse_id_from_ref(book_num: str, chapter: int, verse: int) -> str:
    return f"{book_num}{chapter:03d}{verse:03d}"


# ================== TOKEN MATCHING ==================

def find_token_for_text(preceding_text: str,
                        verse_tokens: list) -> str:
    """Find the first token ID whose text matches the last word of
    preceding_text (punctuation stripped).
    Falls back to last token in verse if no match found.

    verse_tokens: list of (token_id, token_text) tuples
    """
    if not verse_tokens:
        return None

    if not preceding_text or not preceding_text.strip():
        return verse_tokens[-1][0]

    # Extract last word, stripping all punctuation
    words = re.split(r'[\s,\.;:!?\'"—–\-\u2013\u2014\u201c\u201d\u2018\u2019]+',
                     preceding_text.strip())
    words = [w for w in words if w]
    if not words:
        return verse_tokens[-1][0]

    last_word = words[-1].lower()

    # Find FIRST token whose text matches
    for token_id, token_text in verse_tokens:
        if token_text.lower().strip() == last_word:
            return token_id

    # No match — fall back to last token
    return verse_tokens[-1][0]


# ================== CONTENT PARSER ==================

def get_preceding_text(verse_content: list, note_idx: int) -> str:
    """Extract the text string immediately preceding a noteId in the
    verse content array. Handles plain strings, poem dicts, and
    lineBreak dicts."""
    for i in range(note_idx - 1, -1, -1):
        part = verse_content[i]
        if isinstance(part, str) and part.strip():
            return part.strip()
        elif isinstance(part, dict):
            # poem content has a 'text' key
            text = part.get('text', '')
            if text.strip():
                return text.strip()
            # lineBreak — keep looking back
    return ''


def parse_chapter(chapter_data: dict, book_osis: str, book_num: str,
                  verse_token_index: dict,
                  headers: dict, notes: dict):
    """Parse one chapter's content from the API.
    Mutates headers and notes dicts."""

    content        = chapter_data.get('content', [])
    footnotes_list = chapter_data.get('footnotes', [])

    ch_num         = chapter_data['number']

    # Build footnote text lookup by noteId
    fn_text = {fn['noteId']: fn['text'] for fn in footnotes_list}

    pending_header = None

    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')

        # ---- Heading ----
        if item_type == 'heading':
            parts = item.get('content', [])
            text  = ' '.join(p for p in parts if isinstance(p, str)).strip()
            if text:
                pending_header = text
            continue

        # ---- Verse ----
        if item_type == 'verse':
            verse_num = item['number']
            ref = osis_ref(book_osis, ch_num, verse_num)
            vid = verse_id_from_ref(book_num, ch_num, verse_num)

            # Attach pending header to this verse
            if pending_header:
                headers[ref] = pending_header
                pending_header = None

            verse_tokens = verse_token_index.get(vid, [])
            last_token_id = verse_tokens[-1][0] if verse_tokens else None

            # Walk verse content for noteIds
            verse_content = item.get('content', [])
            for ci, part in enumerate(verse_content):
                if not isinstance(part, dict):
                    continue
                note_id = part.get('noteId')
                if note_id is None:
                    continue

                note_text      = fn_text.get(note_id, '')
                preceding_text = get_preceding_text(verse_content, ci)
                token_id       = find_token_for_text(preceding_text, verse_tokens)

                if token_id is None:
                    token_id = last_token_id

                if token_id:
                    notes.setdefault(token_id, []).append({
                        'noteId': note_id,
                        'text':   note_text,
                    })


# ================== MAIN ==================

def main():
    print("Loading biblelib book maps...")
    api_to_osis = build_api_to_osis_map()
    api_to_num  = build_book_num_map()

    print("Loading verse token index...")
    verse_token_index = load_verse_token_index(TARGET_OT, TARGET_NT)

    print("Fetching book list from API...")
    books_data = fetch_json(BOOKS_ENDPOINT)
    books      = books_data['books']
    print(f"  Found {len(books)} books")

    if BOOKS_FILTER:
        books = [b for b in books if b['id'] in BOOKS_FILTER]
        print(f"  Filtered to {len(books)} book(s): {BOOKS_FILTER}")

    headers = {}
    notes   = {}

    for book in books:
        api_id   = book['id']
        osis_id  = api_to_osis.get(api_id)
        book_num = api_to_num.get(api_id)

        if not osis_id or not book_num:
            print(f"  Warning: could not map book '{api_id}', skipping")
            continue

        n_chapters   = book['numberOfChapters']
        book_headers = 0
        book_notes   = 0
        print(f"  {api_id} ({n_chapters} chapters)...", end=' ', flush=True)

        for ch_num in range(1, n_chapters + 1):
            url = f"{API_BASE}/{api_id}/{ch_num}.json"
            try:
                ch_data = fetch_json(url)
                prev_h  = len(headers)
                prev_n  = len(notes)
                parse_chapter(ch_data['chapter'], osis_id, book_num,
                              verse_token_index, headers, notes)
                book_headers += len(headers) - prev_h
                book_notes   += len(notes)   - prev_n
            except Exception as e:
                print(f"\n    Error fetching {url}: {e}")

            time.sleep(REQUEST_DELAY)

        print(f"{book_headers} headers, {book_notes} note anchors")

    print(f"\nTotal: {len(headers)} headers, {len(notes)} note anchors")

    OUTPUT.write_text(
        json.dumps({'headers': headers, 'notes': notes},
                   indent=2, ensure_ascii=False),
        encoding='utf-8'
    )
    print(f"Written to {OUTPUT}")


if __name__ == '__main__':
    main()