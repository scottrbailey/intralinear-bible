#!/usr/bin/env python3
"""
build_esword_alignment.py

Build fresh OT and NT alignment NDJSON files from a BibleHub BSB e-sword
interlinear module (SQLite .bbl database).

Usage:
    python utils/build_esword_alignment.py [--esword PATH] [--books Gen Matt]

Output:
    output/WLCM-BSB-esword.ndjson
    output/SBLGNT-BSB-esword.ndjson
    output/esword_alignment.log
"""

import argparse
import csv
import json
import logging
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml
from biblelib.book import Books

# ── Book tables ──────────────────────────────────────────────────────────────

# OSIS book ID → e-sword/BSB book number (int)
_books = list(Books().values())
OSIS_TO_BOOKNUM: dict[str, int] = {}
BOOKNUM_TO_OSIS: dict[int, str] = {}
for _b in _books:
    try:
        _n = int(_b.usfmnumber)
        OSIS_TO_BOOKNUM[_b.osisID] = _n
        BOOKNUM_TO_OSIS[_n] = _b.osisID
    except (ValueError, TypeError):
        pass

OT_OSIS = {osis for osis, n in OSIS_TO_BOOKNUM.items() if 1 <= n <= 39}
NT_OSIS = {osis for osis, n in OSIS_TO_BOOKNUM.items() if 40 <= n <= 66}

# Macula source book codes → BSB 2-digit zero-padded number
MACULA_OT_BOOK = {
    'GEN': '01', 'EXOD': '02', 'LEV': '03', 'NUM': '04', 'DEUT': '05',
    'JOSH': '06', 'JUDG': '07', 'RUTH': '08', '1SAM': '09', '2SAM': '10',
    '1KGS': '11', '2KGS': '12', '1CHR': '13', '2CHR': '14', 'EZRA': '15',
    'NEH': '16', 'ESTH': '17', 'JOB': '18', 'PS': '19', 'PROV': '20',
    'ECCL': '21', 'SONG': '22', 'ISA': '23', 'JER': '24', 'LAM': '25',
    'EZEK': '26', 'DAN': '27', 'HOS': '28', 'JOEL': '29', 'AMOS': '30',
    'OBAD': '31', 'JONAH': '32', 'MIC': '33', 'NAH': '34', 'HAB': '35',
    'ZEPH': '36', 'HAG': '37', 'ZECH': '38', 'MAL': '39',
}
MACULA_NT_BOOK = {
    'MAT': '40', 'MRK': '41', 'LUK': '42', 'JHN': '43', 'ACT': '44',
    'ROM': '45', '1CO': '46', '2CO': '47', 'GAL': '48', 'EPH': '49',
    'PHP': '50', 'COL': '51', '1TH': '52', '2TH': '53', '1TI': '54',
    '2TI': '55', 'TIT': '56', 'PHM': '57', 'HEB': '58', 'JAS': '59',
    '1PE': '60', '2PE': '61', '1JN': '62', '2JN': '63', '3JN': '64',
    'JUD': '65', 'REV': '66',
}
# Reverse: BSB 2-digit book → macula book code
BSB_TO_MACULA_OT = {v: k for k, v in MACULA_OT_BOOK.items()}
BSB_TO_MACULA_NT = {v: k for k, v in MACULA_NT_BOOK.items()}


# ── HTML cell parser ─────────────────────────────────────────────────────────

_TD_RE   = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE)
_NUM_RE  = re.compile(r'<num>(.*?)</num>', re.IGNORECASE)
_FONT_RE = re.compile(r'<font[^>]*color=["\']?(#[0-9a-fA-F]{6})["\']?[^>]*>(.*?)</font>',
                      re.DOTALL | re.IGNORECASE)
_TAG_RE  = re.compile(r'<[^>]+>')
_BR_RE   = re.compile(r'<br\s*/?>', re.IGNORECASE)
_GREY    = '#808080'
_GREEN   = '#006400'


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub('', html).strip()


def parse_esword_cells(html: str) -> list[dict]:
    """
    Parse one verse's e-sword HTML into an ordered list of cell dicts.
    Each dict has: english (str), script (str), strongs (list[str]).
    """
    cells = []
    for m in _TD_RE.finditer(html):
        cell_html = m.group(1)

        strongs = [s.strip() for s in _NUM_RE.findall(cell_html) if s.strip()]

        script = ''
        for color, content in _FONT_RE.findall(cell_html):
            if color.upper() == _GREEN.upper():
                script = _strip_tags(content).strip()
                break

        # English: the text before the first <br>
        before_br = _BR_RE.split(cell_html, 1)[0]
        english = _strip_tags(before_br).strip()

        cells.append({'english': english, 'script': script, 'strongs': strongs})
    return cells


# ── Text normalization ───────────────────────────────────────────────────────

_HEB_LETTERS = re.compile(r'[^א-תיִ-פֿ]')

def normalize_hebrew(text: str) -> str:
    """Strip all diacritics/cantillation, keep only Hebrew letter codepoints."""
    return _HEB_LETTERS.sub('', text)


def normalize_greek(text: str) -> str:
    """NFD-decompose, strip combining marks, lowercase."""
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn').lower()


def _norm_eng_word(w: str) -> str:
    return re.sub(r"[.,;:!?()\[\]\"'`]", '', w).lower().strip()


def normalize_eng_words(phrase: str) -> list[str]:
    return [n for w in phrase.split() if (n := _norm_eng_word(w))]


# ── Strong's normalization ───────────────────────────────────────────────────

def norm_strong_esword(s: str, lang: str) -> str | None:
    """Normalize an e-sword <num> value. Returns None for morphology codes."""
    s = s.strip()
    prefix = 'H' if lang == 'H' else 'G'
    if not s.upper().startswith(prefix):
        return None
    digits = re.sub(r'[^0-9]', '', s)
    if not digits:
        return None
    return str(int(digits))


def norm_strong_macula(s: str) -> str:
    """Normalize a macula strongnumberx / strong value for comparison."""
    # Strip trailing letter suffix (e.g. '0871a' → '871')
    base = re.sub(r'[a-zA-Z]+$', '', s.strip())
    try:
        return str(int(base))
    except ValueError:
        return s.strip()


# ── Macula loaders ───────────────────────────────────────────────────────────

def _parse_macula_ref(ref: str):
    """Parse 'GEN 1:1!3' → (book_code='GEN', chapter=1, verse=1, word_num=3)."""
    verse_part, word_num = ref.rsplit('!', 1)
    book_code, cv = verse_part.split(' ', 1)
    chapter, verse = cv.split(':')
    return book_code, int(chapter), int(verse), int(word_num)


def load_macula_ot(path: Path, allowed_book_nums: set[str] | None) -> dict:
    """
    Load OT source tokens.
    Returns: {(book_num_str, chapter, verse): [word_dict, ...]} in word order.
    word_dict = {word_num, token_ids, concat_text, strongs_set}
    """
    verses: dict = defaultdict(list)
    current_word: dict | None = None
    current_key = None

    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            book_code, chapter, verse, word_num = _parse_macula_ref(row['ref'])
            book_num = MACULA_OT_BOOK.get(book_code)
            if book_num is None:
                continue
            if allowed_book_nums and book_num not in allowed_book_nums:
                continue

            key = (book_num, chapter, verse)
            word_key = (book_num, chapter, verse, word_num)

            if word_key != current_key:
                if current_word is not None and current_key is not None:
                    vk = current_key[:3]
                    verses[vk].append(current_word)
                current_key = word_key
                current_word = {
                    'word_num':    word_num,
                    'token_ids':   [],
                    'concat_text': '',
                    'strongs_set': set(),
                }

            current_word['token_ids'].append(row['xml:id'])
            current_word['concat_text'] += row['text']
            s = row['strongnumberx'].strip()
            if s:
                current_word['strongs_set'].add(s)

    if current_word is not None and current_key is not None:
        verses[current_key[:3]].append(current_word)

    return dict(verses)


def load_macula_nt(path: Path, allowed_book_nums: set[str] | None) -> dict:
    """
    Load NT source tokens (one token per word).
    Returns: {(book_num_str, chapter, verse): [word_dict, ...]} in word order.
    """
    verses: dict = defaultdict(list)

    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            book_code, chapter, verse, word_num = _parse_macula_ref(row['ref'])
            book_num = MACULA_NT_BOOK.get(book_code)
            if book_num is None:
                continue
            if allowed_book_nums and book_num not in allowed_book_nums:
                continue

            key = (book_num, chapter, verse)
            verses[key].append({
                'word_num':    word_num,
                'token_ids':   [row['xml:id']],
                'concat_text': row['text'],
                'strongs_set': {row['strong'].strip()} if row.get('strong', '').strip() else set(),
            })

    return dict(verses)


# ── BSB target loader ────────────────────────────────────────────────────────

def load_bsb_target(path: Path, allowed_book_nums: set[str] | None) -> dict:
    """
    Load BSB target tokens.
    Returns: {verse_id_8: [token_dict, ...]} where verse_id_8 is 'BBCCCVVV'.
    token_dict = {id, text, exclude}
    """
    verses: dict = defaultdict(list)
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            book_num = row['id'][:2]
            if allowed_book_nums and book_num not in allowed_book_nums:
                continue
            verse_id = row['source_verse']
            verses[verse_id].append({
                'id':      row['id'],
                'text':    row['text'],
                'exclude': row.get('exclude', '') == 'y',
            })
    return dict(verses)


# ── Cell → BSB token span matching ──────────────────────────────────────────

def match_cells_to_bsb(cells: list[dict], bsb_tokens: list[dict]) -> tuple[list, list[str]]:
    """
    Match e-sword cells (English order) to contiguous BSB token spans.
    Returns (results, warnings) where results = [(cell, [bsb_token_ids])].
    Skips excluded (punctuation) tokens.
    """
    non_excl = [t for t in bsb_tokens if not t['exclude']]
    ptr = 0
    results = []
    warnings = []

    for cell in cells:
        eng = cell['english'].strip()

        if eng in ('~', '') or not eng:
            results.append((cell, []))
            continue

        cell_words = normalize_eng_words(eng)
        if not cell_words:
            results.append((cell, []))
            continue

        span_ids = []
        ok = True
        saved_ptr = ptr

        for cw in cell_words:
            if ptr >= len(non_excl):
                ok = False
                break
            bsb_w = _norm_eng_word(non_excl[ptr]['text'])
            if bsb_w == cw:
                span_ids.append(non_excl[ptr]['id'])
                ptr += 1
            else:
                ok = False
                break

        if not ok:
            warnings.append(
                f"BSB match fail at ptr={saved_ptr}: "
                f"cell '{eng}' vs BSB '{non_excl[saved_ptr]['text'] if saved_ptr < len(non_excl) else 'EOF'}'"
            )
            ptr = saved_ptr  # don't advance; let caller decide
            results.append((cell, []))
        else:
            results.append((cell, span_ids))

    if ptr < len(non_excl):
        remaining = [t['text'] for t in non_excl[ptr:]]
        warnings.append(f"Unmatched BSB tokens at end: {remaining}")

    return results, warnings


# ── Cell → source token matching ────────────────────────────────────────────

def match_cell_to_source(
    cell: dict,
    macula_words: list[dict],
    used_token_ids: set[str],
    lang: str,
) -> tuple[list[str] | None, str]:
    """
    Find the macula word matching this e-sword cell.
    Returns (token_ids, confidence_str) or (None, reason_str).
    Uses script text first, Strong's as tiebreaker.
    """
    script = cell['script']
    raw_strongs = cell['strongs']

    if lang == 'H':
        norm_fn   = normalize_hebrew
        strong_fn = lambda s: norm_strong_esword(s, 'H')
    else:
        norm_fn   = normalize_greek
        strong_fn = lambda s: norm_strong_esword(s, 'G')

    norm_script  = norm_fn(script) if script else ''
    norm_strongs = {v for s in raw_strongs if (v := strong_fn(s))}

    candidates = []
    for word in macula_words:
        if any(tid in used_token_ids for tid in word['token_ids']):
            continue

        word_norm    = norm_fn(word['concat_text'])
        word_strongs = {norm_strong_macula(s) for s in word['strongs_set']}

        script_match = bool(norm_script and word_norm and norm_script == word_norm)
        strong_match = bool(norm_strongs and norm_strongs & word_strongs)

        if script_match or strong_match:
            candidates.append((word, script_match, strong_match))

    if not candidates:
        return None, f'no match (script={norm_script!r}, strongs={norm_strongs})'

    # Prefer script+strong > script-only > strong-only
    both   = [c for c in candidates if c[1] and c[2]]
    script = [c for c in candidates if c[1]]

    if len(both) == 1:
        return both[0][0]['token_ids'], 'script+strong'
    if len(both) > 1:
        return None, f'{len(both)} script+strong matches (ambiguous)'
    if len(script) == 1:
        return script[0][0]['token_ids'], 'script'
    if len(script) > 1:
        return None, f'{len(script)} script matches (ambiguous)'
    if len(candidates) == 1:
        return candidates[0][0]['token_ids'], 'strong'
    return None, f'{len(candidates)} strong-only matches (ambiguous)'


# ── Per-verse processor ──────────────────────────────────────────────────────

def process_verse(
    verse_key: tuple,         # (book_num_str, chapter, verse)
    esword_html: str,
    bsb_tokens: list[dict],
    macula_words: list[dict],
    lang: str,
) -> tuple[list[dict], str | None]:
    """
    Build alignment records for one verse.
    Returns (records, log_message_or_None).
    """
    book_num, chapter, verse = verse_key
    verse_id_8 = f"{book_num}{chapter:03d}{verse:03d}"

    cells = parse_esword_cells(esword_html)
    if not cells:
        return [], f"{verse_id_8}: no e-sword cells parsed"

    cell_bsb_pairs, bsb_warnings = match_cells_to_bsb(cells, bsb_tokens)

    used_source_ids: set[str] = set()
    records = []
    issues = []
    record_seq = 1

    for cell, bsb_ids in cell_bsb_pairs:
        # Skip untranslated cells
        if not cell['english'].strip() or cell['english'].strip() == '~':
            continue
        # Skip cells that failed BSB matching (already in bsb_warnings)
        if not bsb_ids:
            continue

        source_ids, confidence = match_cell_to_source(
            cell, macula_words, used_source_ids, lang
        )

        if source_ids is None:
            issues.append(
                f"cell '{cell['english']}' (script={cell['script']!r}): {confidence}"
            )
            continue

        for tid in source_ids:
            used_source_ids.add(tid)

        records.append({
            'source': source_ids,
            'target': bsb_ids,
            'meta': {
                'id':     f"{verse_id_8}.{record_seq:03d}",
                'origin': 'esword',
                'status': 'created',
            },
        })
        record_seq += 1

    all_issues = bsb_warnings + issues
    log_msg = None
    if all_issues:
        log_msg = f"{verse_id_8}: " + "; ".join(all_issues)

    return records, log_msg


# ── Config & main ────────────────────────────────────────────────────────────

def load_config(path='config.yaml') -> dict:
    with open(path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    data_root = Path(cfg.get('data_root', '../'))
    for testament in ('ot', 'nt'):
        src = cfg['sources'][testament]
        for key in ('source', 'target'):
            src[key] = data_root / src[key]
    return cfg


def resolve_books_filter(books_arg: list[str] | None, cfg_books: list | None):
    """Return set of 2-digit book number strings, or None for all books."""
    raw = books_arg or cfg_books
    if not raw:
        return None
    nums = set()
    for osis in raw:
        n = OSIS_TO_BOOKNUM.get(osis)
        if n:
            nums.add(f"{n:02d}")
        else:
            print(f"Warning: unknown book '{osis}'")
    return nums or None


def _bsb_verse_ids_for_books(bsb_target: dict, allowed: set[str] | None):
    """Yield (book_num_str, chapter, verse, verse_id_8) for each BSB verse."""
    for verse_id_8 in sorted(bsb_target):
        book_num = verse_id_8[:2]
        if allowed and book_num not in allowed:
            continue
        chapter = int(verse_id_8[2:5])
        verse   = int(verse_id_8[5:8])
        yield book_num, chapter, verse, verse_id_8


def run_testament(
    testament: str,
    cfg: dict,
    esword_db: Path,
    allowed_book_nums: set[str] | None,
    out_dir: Path,
    log_lines: list[str],
):
    src_cfg = cfg['sources'][testament]
    lang    = 'H' if testament == 'ot' else 'G'

    print(f"\nLoading {testament.upper()} data...")
    if testament == 'ot':
        macula_all = load_macula_ot(src_cfg['source'], allowed_book_nums)
        out_name   = 'WLCM-BSB-esword.ndjson'
    else:
        macula_all = load_macula_nt(src_cfg['source'], allowed_book_nums)
        out_name   = 'SBLGNT-BSB-esword.ndjson'

    bsb_all = load_bsb_target(src_cfg['target'], allowed_book_nums)
    print(f"  {len(macula_all):,} source verses, {len(bsb_all):,} BSB verses")

    conn = sqlite3.connect(esword_db)
    cur  = conn.cursor()

    out_path    = out_dir / out_name
    total_recs  = 0
    total_verses = 0

    with open(out_path, 'w', encoding='utf-8') as out_f:
        for book_num, chapter, verse, verse_id_8 in _bsb_verse_ids_for_books(bsb_all, allowed_book_nums):
            esword_book = int(book_num)
            cur.execute(
                'SELECT Scripture FROM Bible WHERE Book=? AND Chapter=? AND Verse=?',
                (esword_book, chapter, verse),
            )
            row = cur.fetchone()
            if not row:
                log_lines.append(f"{verse_id_8}: no e-sword row found")
                continue

            esword_html  = row[0]
            bsb_tokens   = bsb_all.get(verse_id_8, [])
            macula_words = macula_all.get((book_num, chapter, verse), [])

            if not macula_words:
                log_lines.append(f"{verse_id_8}: no macula source tokens")
                continue

            records, log_msg = process_verse(
                (book_num, chapter, verse), esword_html, bsb_tokens, macula_words, lang
            )

            for rec in records:
                out_f.write(json.dumps(rec, ensure_ascii=False) + '\n')

            total_recs   += len(records)
            total_verses += 1
            if log_msg:
                log_lines.append(log_msg)

    conn.close()
    print(f"  Wrote {total_recs:,} records for {total_verses:,} verses → {out_path}")


def main():
    parser = argparse.ArgumentParser(description='Build alignment NDJSON from e-sword interlinear')
    parser.add_argument('--esword',  default='local/bsbi+.bbl', help='Path to e-sword .bbl SQLite file')
    parser.add_argument('--books',   nargs='+', metavar='OSIS',  help='Book filter, e.g. Gen Matt')
    parser.add_argument('--config',  default='config.yaml')
    parser.add_argument('--out-dir', default='output')
    args = parser.parse_args()

    cfg = load_config(args.config)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    allowed = resolve_books_filter(args.books, cfg.get('books'))
    if allowed:
        ot_allowed = allowed & {f"{n:02d}" for n in range(1, 40)}
        nt_allowed = allowed & {f"{n:02d}" for n in range(40, 67)}
    else:
        ot_allowed = None
        nt_allowed = None

    esword_db = Path(args.esword)
    if not esword_db.exists():
        raise FileNotFoundError(f"e-sword database not found: {esword_db}")

    log_lines: list[str] = []

    if ot_allowed is None or ot_allowed:
        run_testament('ot', cfg, esword_db, ot_allowed, out_dir, log_lines)
    if nt_allowed is None or nt_allowed:
        run_testament('nt', cfg, esword_db, nt_allowed, out_dir, log_lines)

    log_path = out_dir / 'esword_alignment.log'
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(log_lines) + '\n')
    print(f"\n{len(log_lines)} log entries → {log_path}")


if __name__ == '__main__':
    main()
