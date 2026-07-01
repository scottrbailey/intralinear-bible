#!/usr/bin/env python3
"""
build_esword_alignment.py

Build corrected OT and NT alignment NDJSON files by verifying each existing
alignment record against a BibleHub BSB e-sword interlinear SQLite module.

Strategy:
  1. Drive from the existing alignment (source of truth for ordering/grouping).
  2. For each e-sword cell, find the covering existing record(s) via BSB tokens.
  3. Verify e-sword script against the record's source tokens.
  4. Emit corrected records; log genuine mismatches or ambiguities.

  Special cases:
    • Cell spans ONE existing record  → verify and optionally correct.
    • Cell spans N>1 existing records → merge into one record, verify/correct.
    • Source token not in any existing record → search full verse source index.

Usage:
    python utils/build_esword_alignment.py [--esword PATH] [--books Gen Matt]

Output:
    output/WLCM-BSB-esword.ndjson
    output/SBLGNT-BSB-esword.ndjson
    output/esword_alignment.log
"""

import argparse
import html as html_module
import json
import re
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

import yaml

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
_TAG_RE = re.compile(r'<[^>]+>')
_BR_RE  = re.compile(r'<br\s*/?>', re.IGNORECASE)
_GREEN  = '#006400'


def _strip_tags(html: str) -> str:
    return _TAG_RE.sub('', html).strip()


def parse_esword_cells(html: str) -> list[dict]:
    """Parse one verse's e-sword HTML into ordered cell dicts."""
    cells = []
    for m in _TD_RE.finditer(html):
        cell_html = m.group(1)
        strongs   = [s.strip() for s in _NUM_RE.findall(cell_html) if s.strip()]
        script    = ''
        for color, content in _FONT_RE.findall(cell_html):
            if color.upper() == _GREEN.upper():
                script = _strip_tags(content).strip()
                break
        # Strip Masoretic paragraph markers (ס setuma, פ petucha) from script end
        if script and script[-1] in ('ס', 'פ'):
            script = script[:-1].strip()
        before_br = _BR_RE.split(cell_html, 1)[0]
        english   = _strip_tags(html_module.unescape(before_br)).strip()
        # Strip BSB variant/footnote anchor marker that appears in some cells
        english   = re.sub(r'^vvv\s*', '', english).strip()
        cells.append({'english': english, 'script': script, 'strongs': strongs})
    return cells


# ── Text normalization ───────────────────────────────────────────────────────

def normalize_hebrew(text: str) -> str:
    """Keep only Hebrew base letter codepoints (U+05D0–U+05EA)."""
    return re.sub(r'[^א-ת]', '', text)


def normalize_greek(text: str) -> str:
    """NFD-decompose, strip combining diacritics and non-letter chars, lowercase."""
    nfd = unicodedata.normalize('NFD', text)
    # Keep only letters (strips Mn combining diacritics, undertie ‿, apostrophes, etc.)
    return ''.join(c for c in nfd if c.isalpha() and unicodedata.category(c) != 'Mn').lower()


# Strip dashes, curly braces, standard + smart punctuation from English.
_DASH_RE  = re.compile(r'[–—―−]')  # en dash, em dash, horizontal bar, minus sign (NOT ASCII hyphen)
_PUNCT_RE = re.compile(r"""[.,;:!?()\[\]{}\u201c\u201d\u2018\u2019"'`]""")


def _norm_eng_word(w: str) -> str:
    w = _PUNCT_RE.sub('', w)
    return w.lower().strip()


def phrase_to_words(phrase: str) -> list[str]:
    phrase = _DASH_RE.sub(' ', phrase)
    return [n for w in phrase.split() if (n := _norm_eng_word(w))]


def is_untranslated(english: str) -> bool:
    """True for '~', '~.', empty, or pure-punctuation cells."""
    stripped = re.sub(r'[^\w]', '', english, flags=re.UNICODE)
    return stripped in ('', '~')


# ── Strong's normalization ────────────────────────────────────────────────────
# Match composer.py format: strip prefix and leading zeros → 'H430', 'G976'.

def norm_strong_esword(s: str, lang: str) -> str | None:
    s      = s.strip()
    prefix = 'H' if lang in ('H', 'A') else 'G'
    if not s.upper().startswith(prefix):
        return None
    digits = re.sub(r'[^0-9]', '', s)
    return (prefix + str(int(digits))) if digits else None


# ── Cell → BSB span matching ─────────────────────────────────────────────────

def build_cell_to_bsb_map(cells, target_tokens) -> tuple[list, list[str]]:
    """
    Match e-sword cells (English display order) to BSB token ID spans.
    Returns ([(cell, [bsb_ids])], warnings).
    """
    # All alphabetic tokens (excl or not); punctuation-only tokens skipped entirely.
    # Excluded tokens (proper names, brackets) are matched when the e-sword cell
    # mentions them, but silently skipped when it doesn't.
    scannable = [t for t in target_tokens if any(c.isalpha() for c in t.text)]
    ptr       = 0
    pairs     = []
    warnings  = []

    for cell in cells:
        eng = cell['english'].strip()
        if is_untranslated(eng):
            # Store adjacent token for position anchoring in alignment output
            adj = scannable[ptr].id if ptr < len(scannable) else None
            pairs.append(({**cell, 'adjacent_id': adj}, []))
            continue

        cell_words = phrase_to_words(eng)
        if not cell_words:
            pairs.append((cell, []))
            continue

        span_ids  = []
        ok        = True
        saved_ptr = ptr

        for cw in cell_words:
            # Advance past excluded tokens that don't match; stop on non-excluded
            # mismatch. This lets "Now Deborah," match ["Now"(non-excl),
            # "Deborah"(excl)] while silently skipping "[Jacob]"(excl) when the
            # e-sword cell doesn't mention it.
            look    = ptr
            matched = False
            while look < len(scannable):
                bsb_w = _norm_eng_word(scannable[look].text)
                if bsb_w == cw:
                    span_ids.append(scannable[look].id)
                    ptr     = look + 1
                    matched = True
                    break
                elif scannable[look].exclude:
                    look += 1  # skip excluded non-matching token
                else:
                    break      # non-excluded mismatch → scan failure
            if not matched:
                ok = False
                break

        if not ok:
            bsb_at = scannable[saved_ptr].text if saved_ptr < len(scannable) else 'EOF'
            warnings.append(f"BSB mismatch: cell '{eng}' vs BSB '{bsb_at}'")
            ptr = saved_ptr
            pairs.append((cell, []))
        else:
            pairs.append((cell, span_ids))

    if ptr < len(scannable):
        warnings.append(f"Unmatched BSB tokens: {[t.text for t in scannable[ptr:]]}")

    return pairs, warnings


# ── Source script helpers ────────────────────────────────────────────────────

def concat_script(source_tokens, lang: str) -> str:
    raw = ''.join(t.text for t in source_tokens)
    return normalize_hebrew(raw) if lang in ('H', 'A') else normalize_greek(raw)


def find_source_for_cell(cell, lang, verse_source_tokens, used_ids) -> tuple:
    """
    Search verse_source_tokens for the display-word matching the e-sword cell.
    Groups consecutive tokens sharing `after=''` into display-words (OT only).
    Returns (token_id_list, confidence_str) or (None, reason_str).
    """
    norm_fn = normalize_hebrew if lang in ('H', 'A') else normalize_greek
    script  = norm_fn(cell['script']) if cell['script'] else ''
    esword_strongs = {
        v for s in cell['strongs'] if (v := norm_strong_esword(s, lang))
    }

    # Build position index and find last-used position for proximity tiebreak.
    tok_pos = {t.id: i for i, t in enumerate(verse_source_tokens)}
    used_positions = [tok_pos[tid] for tid in used_ids if tid in tok_pos]
    last_used = max(used_positions) if used_positions else -1

    # Group tokens into display-words by the `after` field.
    words: list[list] = []
    current: list = []
    for tok in verse_source_tokens:
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
        s_match = bool(script and word_script and script == word_script)
        q_match = bool(esword_strongs and esword_strongs & word_strongs)
        if s_match or q_match:
            candidates.append((group, s_match, q_match))

    if not candidates:
        return None, f'no match (script={script!r}, strongs={esword_strongs})'

    both        = [c for c in candidates if c[1] and c[2]]
    script_only = [c for c in candidates if c[1]]

    def _pick(pool, label):
        if len(pool) == 1:
            return [t.id for t in pool[0][0]], label
        # Ambiguous — pick the candidate closest to where we last matched.
        def _dist(c):
            pos = tok_pos.get(c[0][0].id, 0)
            return abs(pos - last_used)
        best = min(pool, key=_dist)
        return [t.id for t in best[0]], f'{label} (proximity)'

    if both:        return _pick(both, 'script+strong')
    if script_only: return _pick(script_only, 'script')
    return _pick(candidates, 'strong')


# ── Per-verse processor ──────────────────────────────────────────────────────

def process_verse(
    verse_id: str,
    esword_html: str,
    target_tokens: list,
    alignment_records: list,
    source_index: dict,
    verse_source_tokens: list,   # ALL source tokens for this verse, in order
    lang: str,
) -> tuple[list[dict], str | None]:
    """
    Produce corrected alignment NDJSON records for one verse.
    Returns (records, log_message_or_None).
    """
    cells = parse_esword_cells(esword_html)
    if not cells:
        return [], f"{verse_id}: no e-sword cells"

    cell_bsb_pairs, bsb_warns = build_cell_to_bsb_map(cells, target_tokens)

    # Build lookups: target_token_id → record, source_token_id → record
    target_to_rec: dict[str, object] = {}
    source_to_rec: dict[str, object] = {}
    for rec in alignment_records:
        for tid in rec.target_ids:
            target_to_rec[tid] = rec
        for sid in rec.source_ids:
            source_to_rec[sid] = rec

    used_source_ids:    set[str] = set()
    emitted_rec_ids:    set[str] = set()
    emitted_target_ids: set[str] = set()   # BSB token IDs put into output records
    out_records: list[dict]      = []
    issues: list[str]            = []
    new_rec_counter: int         = 0

    norm_fn = normalize_hebrew if lang in ('H', 'A') else normalize_greek

    for cell, bsb_ids in cell_bsb_pairs:
        esword_script = norm_fn(cell['script']) if cell['script'] else ''

        if not bsb_ids:
            if is_untranslated(cell['english']):
                # Untranslated cell (~) — emit with adjacent BSB anchor if possible.
                adj = cell.get('adjacent_id')
                if adj and cell.get('script'):
                    source_ids, _ = find_source_for_cell(
                        cell, lang, verse_source_tokens, used_source_ids
                    )
                    if source_ids:
                        new_rec_counter += 1
                        out_records.append({
                            'source': source_ids,
                            'target': [adj],
                            'meta': {
                                'id': f"{verse_id}.new{new_rec_counter}",
                                'origin': 'esword',
                                'status': 'untranslated',
                            },
                        })
                        for sid in source_ids:
                            used_source_ids.add(sid)
            else:
                # BSB text scan failed (e.g. spelling variant like Allon-bacuth vs
                # Allon-bachuth). Try finding source via Strong's, then fall back to
                # the existing alignment's target IDs so the record isn't lost.
                if cell.get('script') or cell.get('strongs'):
                    source_ids, confidence = find_source_for_cell(
                        cell, lang, verse_source_tokens, used_source_ids
                    )
                    if source_ids:
                        # Find existing record(s) for these source tokens
                        fb_covering = {
                            source_to_rec[sid].record_id: source_to_rec[sid]
                            for sid in source_ids if sid in source_to_rec
                        }
                        if fb_covering:
                            fb_target: list[str] = []
                            for r in fb_covering.values():
                                for tid in r.target_ids:
                                    if tid not in fb_target:
                                        fb_target.append(tid)
                            rec_id = next(iter(fb_covering.values())).record_id
                            # If an untranslated record was already emitted for
                            # the same target (e.g. allon ~ before Allon-bachuth),
                            # merge into one record rather than emitting a duplicate.
                            merged = False
                            fb_target_set = set(fb_target)
                            for i, prev in enumerate(out_records):
                                if (prev['meta'].get('status') == 'untranslated' and
                                        set(prev['target']) == fb_target_set):
                                    merged_src = list(prev['source']) + [
                                        s for s in source_ids if s not in prev['source']
                                    ]
                                    out_records[i] = {
                                        'source': merged_src,
                                        'target': fb_target,
                                        'meta': {'id': rec_id, 'origin': 'esword', 'status': 'created'},
                                    }
                                    merged = True
                                    break
                            if not merged:
                                out_records.append({
                                    'source': source_ids,
                                    'target': fb_target,
                                    'meta': {'id': rec_id, 'origin': 'esword', 'status': 'created'},
                                })
                            for sid in source_ids:
                                used_source_ids.add(sid)
                            emitted_rec_ids.update(fb_covering.keys())
                            emitted_target_ids.update(fb_target)
                            issues.append(
                                f"BSB text mismatch for '{cell['english']}': "
                                f"used existing target (Strong's fallback, {confidence})"
                            )
                        else:
                            issues.append(
                                f"BSB text mismatch for '{cell['english']}': "
                                f"no existing record for source ({confidence})"
                            )
                    else:
                        issues.append(
                            f"BSB text mismatch for '{cell['english']}': "
                            f"source not found ({confidence})"
                        )
            continue

        # Which un-emitted existing records cover these BSB tokens?
        covering: dict[str, object] = {}
        for tid in bsb_ids:
            if tid in target_to_rec:
                r = target_to_rec[tid]
                if r.record_id not in emitted_rec_ids:
                    covering[r.record_id] = r

        if len(covering) == 0:
            # covering is empty either because:
            #   (a) the existing record was already emitted AND we put these
            #       tokens into that output record → truly covered, skip
            #   (b) the existing record was already emitted but it was emitted
            #       for a different e-sword cell (e-sword re-groups tokens) →
            #       these tokens are NOT yet in any output record, need new one
            # Distinguish by checking emitted_target_ids, not emitted_rec_ids.
            if all(tid in emitted_target_ids for tid in bsb_ids):
                continue  # case (a): tokens already in an output record

            # case (b): tokens not yet output — search whole verse for source
            source_ids, confidence = find_source_for_cell(
                cell, lang, verse_source_tokens, used_source_ids
            )
            if source_ids is None:
                issues.append(f"no existing record + no source for '{cell['english']}': {confidence}")
                continue
            issues.append(f"new record for '{cell['english']}' ({confidence})")

        elif len(covering) == 1:
            rec = next(iter(covering.values()))
            rec_toks = [source_index[sid] for sid in rec.source_ids if sid in source_index]
            existing_script = concat_script(rec_toks, lang)

            script_match = bool(
                esword_script and existing_script and (
                    esword_script == existing_script or
                    esword_script in existing_script or
                    existing_script in esword_script
                )
            )
            if script_match:
                source_ids = rec.source_ids
            else:
                corrected, confidence = find_source_for_cell(
                    cell, lang, verse_source_tokens, used_source_ids
                )
                if corrected:
                    source_ids = corrected
                    if corrected != rec.source_ids:
                        issues.append(
                            f"corrected '{cell['english']}': "
                            f"{rec.source_ids} → {corrected} ({confidence})"
                        )
                else:
                    source_ids = rec.source_ids  # keep existing
                    issues.append(
                        f"kept existing for '{cell['english']}' "
                        f"(e-sword={esword_script!r} existing={existing_script!r}): {confidence}"
                    )
            emitted_rec_ids.add(rec.record_id)

        else:
            # Cell spans multiple existing records.
            # First check if any single covering record individually matches the
            # e-sword script — if so, treat it as a 1-record case and leave the
            # other records for later cells (avoids nonsense concatenated scripts).
            single_match = None
            if esword_script:
                for r in covering.values():
                    rec_toks = [source_index[sid] for sid in r.source_ids if sid in source_index]
                    if concat_script(rec_toks, lang) == esword_script:
                        if single_match is None:
                            single_match = r
                        else:
                            single_match = None  # ambiguous among covering records
                            break

            if single_match is not None:
                # Narrow covering to just this record; others remain for later cells
                source_ids = single_match.source_ids
                covering   = {single_match.record_id: single_match}
            else:
                # Fall back to merging all covering records
                merged_source_ids: list[str] = []
                for r in covering.values():
                    for sid in r.source_ids:
                        if sid not in merged_source_ids:
                            merged_source_ids.append(sid)

                merged_toks   = [source_index[sid] for sid in merged_source_ids if sid in source_index]
                merged_script = concat_script(merged_toks, lang)

                merged_script_match = bool(
                    esword_script and merged_script and (
                        esword_script == merged_script or
                        esword_script in merged_script or
                        merged_script in esword_script
                    )
                )
                if merged_script_match:
                    source_ids = merged_source_ids
                else:
                    corrected, confidence = find_source_for_cell(
                        cell, lang, verse_source_tokens, used_source_ids
                    )
                    if corrected:
                        source_ids = corrected
                        issues.append(
                            f"merged+corrected '{cell['english']}' ({len(covering)} recs): "
                            f"{merged_source_ids} → {corrected} ({confidence})"
                        )
                    else:
                        source_ids = merged_source_ids
                        issues.append(
                            f"merged '{cell['english']}' ({len(covering)} recs, "
                            f"e-sword={esword_script!r} merged={merged_script!r}): {confidence}"
                        )

            for rid in covering:
                emitted_rec_ids.add(rid)

        # Use the e-sword cell's BSB span as the output target.
        # This ensures the annotation attaches to exactly the English words the
        # e-sword cell covers, avoiding off-by-one errors when the existing
        # alignment grouped tokens differently than the e-sword does.
        if covering:
            rec_id = next(iter(covering.values())).record_id
        else:
            new_rec_counter += 1
            rec_id = f"{verse_id}.new{new_rec_counter}"
        target_ids = list(bsb_ids)

        for sid in source_ids:
            used_source_ids.add(sid)
        for tid in target_ids:
            emitted_target_ids.add(tid)

        out_records.append({
            'source': source_ids,
            'target': target_ids,
            'meta':   {'id': rec_id, 'origin': 'esword', 'status': 'created'},
        })

    all_issues = bsb_warns + issues
    log_msg    = (f"{verse_id}: " + "; ".join(all_issues)) if all_issues else None
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
        if 'base_alignment' in src:
            src['base_alignment'] = data_root / src['base_alignment']
    return cfg


def _should_process(testament: str, books_filter) -> bool:
    if testament == 'ot':
        return any(b in _OT_ABBREV for b in (books_filter or ['Gen']))
    return any(b in _NT_ABBREV for b in (books_filter or ['Matt']))


def _rekey_by_target(alignment_index: dict) -> dict:
    """
    Re-key alignment index by the verse of the first target token instead of
    the record ID prefix. Fixes versification mismatches where BSB and the
    source language number verses differently (e.g. Gen 32:32-33).
    """
    out: dict[str, list] = {}
    for recs in alignment_index.values():
        for rec in recs:
            if not rec.target_ids:
                continue
            verse_id = rec.target_ids[0][:8]
            out.setdefault(verse_id, []).append(rec)
    return out


def _build_verse_source_index(source_index: dict) -> dict:
    """
    Group source tokens by verse.
    Verse ID is token_id[1:9] (strips 'o'/'n' prefix, yields BBCCCVVV).
    Tokens are sorted by id to preserve source-language word order.
    """
    by_verse: dict[str, list] = defaultdict(list)
    for token in source_index.values():
        by_verse[token.id[1:9]].append(token)
    return {v: sorted(toks, key=lambda t: t.id) for v, toks in by_verse.items()}


def run_testament(testament, cfg, esword_db, books_filter, out_dir, log_lines):
    src_cfg  = cfg['sources'][testament]
    lang     = 'H' if testament == 'ot' else 'G'
    out_name = 'WLCM-BSB-esword.ndjson' if testament == 'ot' else 'SBLGNT-BSB-esword.ndjson'

    print(f"\nLoading {testament.upper()} source index...")
    source_index = _load_source_index(src_cfg['source'], testament)
    verse_source  = _build_verse_source_index(source_index)

    print(f"Loading {testament.upper()} alignment index...")
    align_path = src_cfg.get('base_alignment') or src_cfg['alignment']
    alignment_index = _rekey_by_target(_load_alignment_index(align_path))

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

            # Derive source verse(s) from alignment records, not from the BSB verse
            # number, to handle versification mismatches (e.g. Gen 32 where BSB
            # verse 5 corresponds to Hebrew verse 6).
            src_verse_ids: set[str] = set()
            for rec in alignment_records:
                for sid in rec.source_ids:
                    src_verse_ids.add(sid[1:9])  # strip 'o'/'n' prefix → BBCCCVVV
            if not src_verse_ids:
                src_verse_ids = {verse_id}
            verse_source_toks: list = []
            for svid in sorted(src_verse_ids):
                verse_source_toks.extend(verse_source.get(svid, []))

            records, log_msg = process_verse(
                verse_id, row[0], target_tokens, alignment_records,
                source_index, verse_source_toks, lang,
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
    parser.add_argument('--esword',  default='local/bsib+.bbli')
    parser.add_argument('--books',   nargs='+', metavar='OSIS')
    parser.add_argument('--config',  default='config.yaml')
    parser.add_argument('--out-dir', default='output')
    args = parser.parse_args()

    cfg     = load_config(args.config)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(exist_ok=True)

    books_filter = args.books or cfg.get('books')
    esword_path  = Path(args.esword)
    if not esword_path.exists():
        raise FileNotFoundError(f"e-sword database not found: {esword_path}")

    conn       = sqlite3.connect(esword_path)
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
