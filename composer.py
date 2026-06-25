"""
composer.py

BibleComposer: loads source data and yields aligned verse tokens.
"""

import csv
import json
import re
from pathlib import Path

from biblelib.book import Books

from models import (
    MappingDirection, NON_STEM_CLASS,
    SourceToken, SourceWord, TargetToken, AlignmentRecord, AlignedToken,
)


# Build at module load — one-time cost, used by composer and writers alike.
BOOK_NUM_MAP: dict = {book.usfmnumber: book.osisID for book in Books().values()}

_book_abbrev = [book.osisID for book in Books().values()]
_OT_ABBREV   = set(_book_abbrev[:39])
_NT_ABBREV   = set(_book_abbrev[60:87])


def verse_id_to_osis(verse_id: str) -> str:
    """Convert BBCCCVVV string to OSIS dotted ref like Gen.1.1"""
    book_num  = verse_id[:2]
    chapter   = int(verse_id[2:5])
    verse     = int(verse_id[5:8])
    book_name = BOOK_NUM_MAP.get(book_num, f"Book{book_num}")
    return f"{book_name}.{chapter}.{verse}"


class BibleComposer:
    """Loads source data and yields (osis_ref, tokens, header, xrefs) per verse.

    direction controls the join strategy:
      TARGET_TO_SOURCE — English-primary tokens (current, used for intralinear
                         and reverse interlinear outputs).
      SOURCE_TO_TARGET — Source-primary tokens (forward interlinear; not yet
                         implemented).
    """

    def __init__(self, config: dict,
                 direction: MappingDirection = MappingDirection.TARGET_TO_SOURCE):
        self.config    = config
        self.direction = direction
        self._books_filter = config.get('books')

    # ------------------------------------------------------------------ public

    def iter_verses(self):
        """Yield (osis_ref, [AlignedToken], header, xrefs) across all testaments."""
        sources    = self.config['sources']
        out        = self.config.get('output', {})
        need_notes   = bool(out.get('notes',   1))
        need_headers = bool(out.get('headers', 1))
        need_xref    = bool(out.get('xref',    0))

        if need_notes or need_headers:
            annotations = _load_annotations(self.config['annotations'])
        else:
            annotations = {}

        headers_index = annotations.get('headers', {}) if need_headers else {}
        notes_index   = annotations.get('notes',   {}) if need_notes   else {}

        tsk = _load_tsk(self.config['tsk']) if need_xref and self.config.get('tsk') else {}

        for testament in ('ot', 'nt'):
            if testament not in sources:
                continue
            if not self._should_process(testament):
                continue
            yield from self._iter_testament(
                testament, sources[testament], headers_index, notes_index, tsk
            )

    # --------------------------------------------------------------- internals

    def _should_process(self, testament: str) -> bool:
        books_filter = self._books_filter
        if testament == 'ot':
            return any(b in _OT_ABBREV for b in (books_filter or ['Gen']))
        return any(b in _NT_ABBREV for b in (books_filter or ['Matt']))

    def _iter_testament(self, testament, tcfg, headers_index, notes_index, tsk):
        print(f"\n{'='*60}")
        print(f"Processing {testament.upper()}")
        print(f"{'='*60}")

        source_index    = _load_source_index(tcfg['source'], testament)
        alignment_index = _load_alignment_index(tcfg['alignment'])

        verse_count = 0
        for verse_id, target_tokens in _iter_target_verses(tcfg['target'], self._books_filter):
            alignment_records = alignment_index.get(verse_id, [])
            tokens   = self._join_verse(verse_id, target_tokens,
                                        alignment_records, source_index, notes_index)
            osis_ref = verse_id_to_osis(verse_id)
            header   = headers_index.get(osis_ref)
            xrefs    = tsk.get(verse_id, {})
            verse_count += 1
            yield osis_ref, tokens, header, xrefs

        print(f"  Processed {verse_count:,} verses.")

    def _join_verse(self, verse_id, target_tokens, alignment_records,
                    source_index, notes_index) -> list:
        if self.direction == MappingDirection.SOURCE_TO_TARGET:
            raise NotImplementedError("Forward interlinear joining not yet implemented")
        return _join_target_to_source(
            target_tokens, alignment_records, source_index, notes_index
        )


# ========================================================= loader functions

def _load_annotations(path: Path) -> dict:
    if not path.exists():
        print(f"  Warning: annotations file not found at {path}, skipping")
        return {'headers': {}, 'notes': {}}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    h = len(data.get('headers', {}))
    n = len(data.get('notes', {}))
    print(f"  Loaded {h:,} headers and {n:,} note anchors from {path.name}")
    return data


def _load_tsk(path: Path) -> dict:
    if not path.exists():
        print(f"  Warning: TSK file not found at {path}, skipping")
        return {}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    print(f"  Loaded TSK cross references from {path.name}")
    return data


def _load_source_index(path: Path, testament: str) -> dict:
    default_lang = 'H' if testament == 'ot' else 'G'
    index = {}
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            lang        = row.get('lang', default_lang)
            raw_strongs = (row.get('strongnumberx') or row.get('strong')
                           or row.get('strongs') or '')
            if raw_strongs:
                raw_strongs = re.sub(r'^[HGA]', '', raw_strongs)
                raw_strongs = re.sub(r'^0*(\d+)[a-z]*$', r'\1', raw_strongs)
                strongs_prefix = 'H' if lang in ('H', 'A') else lang
                raw_strongs = strongs_prefix + raw_strongs

            index[row['xml:id']] = SourceToken(
                id=row['xml:id'],
                text=row['text'],
                strongs=raw_strongs,
                gloss=row.get('gloss', ''),
                token_class=row.get('class', ''),
                pos=row.get('pos', ''),
                noun_type=row.get('type', ''),
                morph=row.get('morph', ''),
                lang=lang,
                lemma=row.get('lemma', ''),
                after=row.get('after', ' '),
            )
    print(f"  Loaded {len(index):,} source tokens from {path.name}")
    return index


def _load_alignment_index(path: Path) -> dict:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    index = {}
    for rec in data['records']:
        record   = AlignmentRecord(
            source_ids=rec['source'],
            target_ids=rec['target'],
            record_id=rec['meta']['id'],
        )
        verse_id = record.record_id.split('.')[0]
        index.setdefault(verse_id, []).append(record)
    print(f"  Loaded alignment records for {len(index):,} verses from {path.name}")
    return index


def _iter_target_verses(path: Path, books_filter: list):
    """Iterate BSB target TSV, yielding (verse_id, [TargetToken]) tuples."""
    allowed_book_nums = None
    if books_filter:
        reverse_map       = {v: k for k, v in BOOK_NUM_MAP.items()}
        allowed_book_nums = set()
        for osis_id in books_filter:
            num = reverse_map.get(osis_id)
            if num:
                allowed_book_nums.add(num)
            else:
                print(f"  Warning: could not resolve book '{osis_id}'")

    current_verse_id = None
    current_tokens   = []

    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            token_id = row['id']
            verse_id = token_id[:8]
            book_num = token_id[:2]

            if allowed_book_nums and book_num not in allowed_book_nums:
                if current_verse_id and current_tokens:
                    yield current_verse_id, current_tokens
                    current_verse_id = None
                    current_tokens   = []
                continue

            token = TargetToken(
                id=token_id,
                verse_id=verse_id,
                text=row['text'],
                skip_space_after=bool(row.get('skip_space_after', '')),
                exclude=bool(row.get('exclude', '')),
            )

            if verse_id != current_verse_id:
                if current_verse_id and current_tokens:
                    yield current_verse_id, current_tokens
                current_verse_id = verse_id
                current_tokens   = []

            current_tokens.append(token)

    if current_verse_id and current_tokens:
        yield current_verse_id, current_tokens


# ===================================================== join / group functions

def _group_source_words(source_tokens: list) -> list:
    """Group a flat list of SourceTokens into SourceWords using the 'after' field."""
    if not source_tokens:
        return []

    words   = []
    current = []

    for token in source_tokens:
        current.append(token)
        if token.after != '':
            words.append(current)
            current = []

    if current:
        words.append(current)

    result = []
    for group in words:
        stem = next(
            (t for t in group if t.token_class not in NON_STEM_CLASS),
            group[-1],
        )
        result.append(SourceWord(
            tokens=group,
            stem=stem,
            text=''.join(t.text for t in group),
            lang=stem.lang,
            is_proper=(stem.noun_type == 'proper'),
        ))
    return result


def _join_target_to_source(target_tokens, alignment_records,
                            source_index, notes_index) -> list:
    """Join target tokens with alignment records; attach source words and notes."""
    target_to_alignment = {}
    for rec in alignment_records:
        for tid in rec.target_ids:
            target_to_alignment[tid] = rec

    result      = []
    seen_records = set()
    absorbed_ids = set()

    for i, token in enumerate(target_tokens):
        rec = target_to_alignment.get(token.id)

        if token.id in absorbed_ids:
            continue

        if token.exclude or rec is None:
            it = AlignedToken(
                english=token.text,
                skip_space_after=token.skip_space_after,
                is_plain_text=True,
            )
            if token.id in notes_index:
                it.notes = notes_index[token.id]
            result.append(it)
            continue

        if rec.record_id in seen_records:
            continue
        seen_records.add(rec.record_id)

        rec_target_ids = set(rec.target_ids)
        group_positions = [j for j, t in enumerate(target_tokens) if t.id in rec_target_ids]
        first_pos = group_positions[0]
        last_pos  = group_positions[-1]

        # Absorb any leading excluded+glued tokens
        prefix_tokens = []
        j = first_pos - 1
        while j >= 0:
            prev = target_tokens[j]
            if prev.exclude and prev.skip_space_after and prev.id not in absorbed_ids:
                prefix_tokens.insert(0, prev)
                absorbed_ids.add(prev.id)
                if result and result[-1].is_plain_text and result[-1].english == prev.text:
                    result.pop()
                j -= 1
            else:
                break

        span_tokens = target_tokens[first_pos:last_pos + 1]
        for t in span_tokens:
            absorbed_ids.add(t.id)

        all_tokens = prefix_tokens + span_tokens
        parts = []
        for k, t in enumerate(all_tokens):
            if k == 0:
                parts.append(t.text)
            elif all_tokens[k - 1].skip_space_after:
                parts.append(t.text)
            else:
                parts.append(' ' + t.text)
        english   = ''.join(parts)
        last_skip = span_tokens[-1].skip_space_after if span_tokens else False

        source_tokens = [source_index[sid] for sid in rec.source_ids if sid in source_index]
        source_words  = _group_source_words(source_tokens)

        token_notes = []
        for t in span_tokens:
            if not t.exclude and t.id in notes_index:
                token_notes.extend(notes_index[t.id])

        result.append(AlignedToken(
            english=english,
            skip_space_after=last_skip,
            source_words=source_words,
            notes=token_notes,
        ))

    return result
