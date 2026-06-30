#!/usr/bin/env python3
"""
build_esword_alignment.py

Build corrected OT and NT alignment NDJSON files by verifying each existing
alignment record against a BibleHub BSB e-sword interlinear SQLite module.

Strategy:
  1. Drive from the existing alignment file (source of truth for disambiguation).
  2. For each existing record, find the matching e-sword cell via BSB token text.
  3. Compare the e-sword Hebrew/Greek script to the record's source tokens.
  4. If they match → emit the record as-is.
  5. If not → try to find the correct source token and emit a corrected record.
  6. Log one entry per verse that has any uncertainty.

Usage:
    python utils/build_esword_alignment.py [--esword PATH] [--books Gen Matt]

Output:
    output/WLCM-BSB-esword.ndjson
    output/SBLGNT-BSB-esword.ndjson
    output/esword_alignment.log
"""

import argparse
import json
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

import yaml

# Reuse loaders and book tables from composer.py
sys.path.insert(0, str(Path(__file__).parent.parent))
from composer import (
    BOOK_NUM_MAP,
    _OT_ABBREV,
    _NT_ABBREV,
    _iter_target_verses,
    _load_alignment_index,
    _load_source_index,
)

# ── HTML cell parser ─────────────────────────────────────────────────────────

_TD_RE   = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL | re.IGNORECASE)
_NUM_RE  = re.compile(r'<num>(.*?)</num>', re.IGNORECASE)
_FONT_RE = re.compile(
    r'<font[^>]*color=["\']?(#[0-9a-fA-F]{6})["\']?[^>]*>(.*?)</font>',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE  = re.compile(r'<[^>]+>')
_BR_RE   = re.compile(r'<br\s*/?>', re.IGNORECASE)
_GREEN   = '#006400'


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub('', html).strip()


def parse_esword_cells(html: str) -> list[dict]:
    """
    Parse one verse's e-sword HTML into ordered cell dicts.
    Each dict: english (str), script (str), strongs (list[str]).
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

        before_br = _BR_RE.split(cell_html, 1)[0]
        english   = _strip_tags(before_br).strip()

        cells.append({'english': english, 'script': script, 'strongs': strongs})
    return cells


# ── Text normalization ───────────────────────────────────────────────────────

def normalize_hebrew(text: str) -> str:
    """Keep only Hebrew base letter codepoints (strip vowels + cantillation)."""
    return re.sub(r'[^א-ת]', '', text)


def normalize_greek(text: str) -> str:
    """NFD-decompose, strip combining diacritics, lowercase."""
    nfd = unicodedata.normalize('NFD', text)
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn').lower()


# Strip these from e-sword English phrases before word-splitting.
# Em/en dashes used as separators, not meaningful words.
_DASH_RE = re.compile(r'[–—―−]')

def _norm_eng_word(w: str) -> str:
    return re.sub(r"[.,;:!?()\[\]\"'`]", '', w).lower().strip()

def phrase_to_words(phrase: str) -> list[str]:
    phrase = _DASH_RE.sub(' ', phrase)
    return [n for w in phrase.split() if (n := _norm_eng_word(w))]


def is_untranslated(english: str) -> bool:
    """True for cells like '~', '~.', or empty."""
    stripped = re.sub(r'[^\w]', '', english, flags=re.UNICODE)
    return stripped in ('', '~')


# ── Strong's normalization ────────────────────────────────────────────────────
# Composer.py normalizes macula strongs to e.g. 'H430', 'H871'.
# We normalize e-sword <num> values the same way.

def norm_strong_esword(s: str, lang: str) -> str | None:
    """
    Normalize an e-sword <num> tag value to match composer.py's format.
    Returns None for morphology codes (no matching prefix) or empty.
    """
    s = s.strip()
    prefix = 'H' if lang in ('H', 'A') else 'G'
    if not s.upper().startswith(prefix):
        return None
    digits = re.sub(r'[^0-9]', '', s)
    if not digits:
        return None
    return prefix + str(int(digits))


# ── Cell → BSB span matching ─────────────────────────────────────────────────

def build_cell_to_bsb_map(
    cells: list[dict],
    target_tokens: list,
) -> tuple[list[tuple[dict, list[str]]], list[str]]:
    """
    Match e-sword cells (English display order) to BSB token ID spans.
    Skips excluded (punctuation) tokens when matching, but records are still
    emitted with only the matched content tokens.
    Returns ([(cell, [bsb_token_ids])], warnings).
    """
    non_excl = [t for t in target_tokens if not t.exclude]
    ptr      = 0
    pairs    = []
    warnings = []

    for cell in cells:
        eng = cell['english'].strip()

        if is_untranslated(eng):
            pairs.append((cell, []))
            continue

        cell_words = phrase_to_words(eng)
        if not cell_words:
            pairs.append((cell, []))
            continue

        span_ids   = []
        ok         = True
        saved_ptr  = ptr

        for cw in cell_words:
            if ptr >= len(non_excl):
                ok = False
                break
            bsb_w = _norm_eng_word(non_excl[ptr].text)
            if bsb_w == cw:
                span_ids.append(non_excl[ptr].id)
                ptr += 1
            else:
                ok = False
                break

        if not ok:
            bsb_at = non_excl[saved_ptr].text if saved_ptr < len(non_excl) else 'EOF'
            warnings.append(f"BSB mismatch: cell '{eng}' vs BSB '{bsb_at}'")
            ptr = saved_ptr
            pairs.append((cell, []))
        else:
            pairs.append((cell, span_ids))

    if ptr < len(non_excl):
        remaining = [t.text for t in non_excl[ptr:]]
        warnings.append(f"Unmatched BSB tokens: {remaining}")

    return pairs, warnings


# ── Source script matching ───────────────────────────────────────────────────

def source_script(source_tokens: list, lang: str) -> str:
    """Normalized concatenated script of a list of SourceToken objects."""
    raw = ''.join(t.text for t in source_tokens)
    return normalize_hebrew(raw) if lang in ('H', 'A') else normalize_greek(raw)


def find_source_for_cell(
    cell: dict,
    lang: str,
    all_source_tokens: list,
    used_ids: set[str],
) -> tuple[list | None, str]:
    """
    Search all_source_tokens for the word matching this e-sword cell.
    Groups tokens into display-words by the `after` field (same as composer.py).
    Returns (token_id_list, confidence) or (None, reason).
    """
    norm_fn = normalize_hebrew if lang in ('H', 'A') else normalize_greek
    script  = norm_fn(cell['script']) if cell['script'] else ''
    esword_strongs = {
        v for s in cell['strongs']
        if (v := norm_strong_esword(s, lang))
    }

    # Group tokens into display-words (tokens joined by after='')
    words: list[list] = []
    current: list = []
    for tok in all_source_tokens:
        current.append(tok)
        if tok.after != '':
            words.append(current)
            current = []
    if current:
        words.append(current)

    candidates = []
    for group in words:
        if any(t.id in used_ids for t in group):
            continue

        word_script  = norm_fn(''.join(t.text for t in group))
        word_strongs = {t.strongs for t in group if t.strongs}

        script_match = bool(script and word_script and script == word_script)
        strong_match = bool(esword_strongs and esword_strongs & word_strongs)

        if script_match or strong_match:
            candidates.append((group, script_match, strong_match))

    if not candidates:
        return None, f'no match (script={script!r}, strongs={esword_strongs})'

    both        = [c for c in candidates if c[1] and c[2]]
    script_only = [c for c in candidates if c[1]]

    def _best(pool, label):
        if len(pool) == 1:
            return [t.id for t in pool[0][0]], label
        return None, f'{len(pool)} {label} matches (ambiguous)'

    if both:
        return _best(both, 'script+strong')
    if script_only:
        return _best(script_only, 'script')
    return _best(candidates, 'strong')


# ── Per-verse processor ──────────────────────────────────────────────────────

def process_verse(
    verse_id: str,
    esword_html: str,
    target_tokens: list,
    alignment_records: list,
    source_index: dict,
    lang: str,
) -> tuple[list[dict], str | None]:
    """
    Produce corrected alignment records for one verse.
    Returns (records, log_message_or_None).
    """
    cells = parse_esword_cells(esword_html)
    if not cells:
        return [], f"{verse_id}: no e-sword cells"

    cell_bsb_pairs, bsb_warns = build_cell_to_bsb_map(cells, target_tokens)

    # Build lookup: target_token_id → alignment record
    target_to_rec: dict[str, object] = {}
    for rec in alignment_records:
        for tid in rec.target_ids:
            target_to_rec[tid] = rec

    # All source tokens for this verse (in source-language order)
    verse_source_ids = []
    for rec in alignment_records:
        for sid in rec.source_ids:
            if sid not in verse_source_ids:
                verse_source_ids.append(sid)
    all_source_tokens = [source_index[sid] for sid in verse_source_ids if sid in source_index]

    used_source_ids: set[str] = set()
    emitted_rec_ids: set[str] = set()
    out_records: list[dict]   = []
    issues: list[str]         = []

    for cell, bsb_ids in cell_bsb_pairs:
        if not bsb_ids:
            continue  # untranslated or failed BSB match

        # Which existing record covers these BSB tokens? (dedupe by record_id)
        covering_recs_list = list(
            {target_to_rec[tid].record_id: target_to_rec[tid]
             for tid in bsb_ids if tid in target_to_rec}.values()
        )

        if len(covering_recs_list) != 1:
            issues.append(
                f"cell '{cell['english']}': spans {len(covering_recs_list)} existing records"
            )
            continue

        rec = covering_recs_list[0]
        if rec.record_id in emitted_rec_ids:
            continue  # already handled (multi-cell cells)

        # Get source tokens for this existing record
        rec_source_tokens = [source_index[sid] for sid in rec.source_ids if sid in source_index]

        # Verify: does the e-sword script match the existing record's source?
        existing_script = source_script(rec_source_tokens, lang)
        norm_fn = normalize_hebrew if lang in ('H', 'A') else normalize_greek
        esword_script   = norm_fn(cell['script']) if cell['script'] else ''

        if esword_script and esword_script == existing_script:
            # Verified — emit as-is
            source_ids = rec.source_ids
            for sid in source_ids:
                used_source_ids.add(sid)
        else:
            # Mismatch — try to correct using e-sword script
            corrected, confidence = find_source_for_cell(
                cell, lang, all_source_tokens, used_source_ids
            )
            if corrected:
                source_ids = corrected
                for sid in source_ids:
                    used_source_ids.add(sid)
                issues.append(
                    f"corrected '{cell['english']}': "
                    f"{rec.source_ids} → {source_ids} ({confidence})"
                )
            else:
                # Can't correct — keep existing record and log
                source_ids = rec.source_ids
                for sid in source_ids:
                    used_source_ids.add(sid)
                issues.append(
                    f"kept existing for '{cell['english']}' "
                    f"(e-sword={esword_script!r} existing={existing_script!r}): {confidence}"
                )

        emitted_rec_ids.add(rec.record_id)
        out_records.append({
            'source': source_ids,
            'target': list(bsb_ids),
            'meta': {
                'id':     rec.record_id,
                'origin': 'esword',
                'status': 'created',
            },
        })

    all_issues = bsb_warns + issues
    log_msg    = f"{verse_id}: " + "; ".join(all_issues) if all_issues else None
    return out_records, log_msg


# ── Config & main ────────────────────────────────────────────────────────────

def load_config(path='config.yaml') -> dict:
    with open(path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    data_root = Path(cfg.get('data_root', '../'))
    for testament in ('ot', 'nt'):
        src = cfg['sources'][testament]
        for key in ('source', 'alignment', 'target'):
            src[key] = data_root / src[key]
    return cfg


def _should_process(testament: str, books_filter: list | None) -> bool:
    if testament == 'ot':
        return any(b in _OT_ABBREV for b in (books_filter or ['Gen']))
    return any(b in _NT_ABBREV for b in (books_filter or ['Matt']))


def run_testament(
    testament: str,
    cfg: dict,
    esword_db: sqlite3.Connection,
    books_filter: list | None,
    out_dir: Path,
    log_lines: list[str],
):
    src_cfg  = cfg['sources'][testament]
    lang     = 'H' if testament == 'ot' else 'G'
    out_name = 'WLCM-BSB-esword.ndjson' if testament == 'ot' else 'SBLGNT-BSB-esword.ndjson'

    print(f"\nLoading {testament.upper()} source index...")
    source_index = _load_source_index(src_cfg['source'], testament)

    print(f"Loading {testament.upper()} alignment index...")
    alignment_index = _load_alignment_index(src_cfg['alignment'])

    out_path     = out_dir / out_name
    total_recs   = 0
    total_verses = 0
    cur          = esword_db.cursor()

    with open(out_path, 'w', encoding='utf-8') as out_f:
        for verse_id, target_tokens in _iter_target_verses(src_cfg['target'], books_filter):
            book_num = int(verse_id[:2])
            chapter  = int(verse_id[2:5])
            verse    = int(verse_id[5:8])

            cur.execute(
                'SELECT Scripture FROM Bible WHERE Book=? AND Chapter=? AND Verse=?',
                (book_num, chapter, verse),
            )
            row = cur.fetchone()
            if not row:
                log_lines.append(f"{verse_id}: no e-sword row")
                continue

            alignment_records = alignment_index.get(verse_id, [])
            if not alignment_records:
                log_lines.append(f"{verse_id}: no existing alignment records")
                continue

            records, log_msg = process_verse(
                verse_id,
                row[0],
                target_tokens,
                alignment_records,
                source_index,
                lang,
            )

            for rec in records:
                out_f.write(json.dumps(rec, ensure_ascii=False) + '\n')

            total_recs   += len(records)
            total_verses += 1
            if log_msg:
                log_lines.append(log_msg)

    print(f"  Wrote {total_recs:,} records for {total_verses:,} verses → {out_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Build corrected alignment NDJSON from e-sword interlinear'
    )
    parser.add_argument('--esword',  default='local/bsbi+.bbl',
                        help='Path to e-sword .bbl SQLite file')
    parser.add_argument('--books',   nargs='+', metavar='OSIS',
                        help='Book filter e.g. --books Gen Matt')
    parser.add_argument('--config',  default='config.yaml')
    parser.add_argument('--out-dir', default='output')
    args = parser.parse_args()

    cfg     = load_config(args.config)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    books_filter = args.books or cfg.get('books')

    esword_path = Path(args.esword)
    if not esword_path.exists():
        raise FileNotFoundError(f"e-sword database not found: {esword_path}")
    conn = sqlite3.connect(esword_path)

    log_lines: list[str] = []

    for testament in ('ot', 'nt'):
        if _should_process(testament, books_filter):
            run_testament(testament, cfg, conn, books_filter, out_dir, log_lines)

    conn.close()

    log_path = out_dir / 'esword_alignment.log'
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(log_lines) + '\n')
    print(f"\n{len(log_lines)} log entries → {log_path}")


if __name__ == '__main__':
    main()
