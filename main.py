"""
Intralinear Bible — main.py
Reads source TSVs and alignment JSON, joins them, and writes output.
"""

import csv
import json
import re
import sys
from pathlib import Path
from dataclasses import dataclass, field

import yaml

from biblelib.book import Books

from translit import make_transliterator
from osis_writer import OSISWriter

# ================== CONFIG ==================

def load_config(path: str = "config.yaml") -> dict:
    """Load and resolve pipeline configuration from YAML file."""
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    data_root = Path(cfg.get("data_root", "../"))
    translation = cfg["translation"]

    # Resolve source paths relative to data_root
    for testament in ("ot", "nt"):
        src = cfg["sources"][testament]
        for key in ("source", "alignment", "target"):
            src[key] = data_root / src[key]

    # Derive module abbreviations
    cfg["abbrev"] = {
        "intralinear":        f"{translation}i",
        "intralinear_stacked": f"{translation}is",
        "interlinear":        f"{translation}ri+",
    }

    # Resolve paths
    cfg["annotations"] = Path(cfg.get("annotations", "data/bsb_annotations.json"))
    cfg["tsk"] = Path(cfg.get("tsk", "data/tskxref.tsv"))
    cfg["output"]["dir"] = Path(cfg["output"]["dir"])

    return cfg


CONFIG_FILE = sys.argv[1] if len(sys.argv) > 1 else "esword_intralinear.yaml"
config = load_config(CONFIG_FILE)

# ================== DATA STRUCTURES ==================

# Source token classes that are prefixes/particles, not the stem of a display-word.
# Used by group_source_words() to identify the stem within a joined word.
NON_STEM_CLASS = {'art', 'cj', 'prep', 'om', 'ptcl', 'rel'}


@dataclass
class SourceToken:
    """One token from the source language TSV (Hebrew/Aramaic/Greek)."""
    id: str
    text: str
    strongs: str       # normalized: always prefixed + zero-padded, e.g. H0776, G0976
    gloss: str
    token_class: str   # 'class' column: noun, verb, adj, prep, art, cj, etc.
    pos: str           # 'pos' column: noun, verb, adjective, preposition, suffix, etc.
    noun_type: str     # 'type' column: 'common', 'proper', or ''
    morph: str
    lang: str
    lemma: str = ""
    after: str = " "   # '' = join to next token (same display-word); ' ' = word boundary


@dataclass
class SourceWord:
    """One display-word in the source language: one or more SourceTokens joined
    by after=''. Carries the concatenated script, the stem token's Strong's number,
    and the lang for transliteration and link generation."""
    tokens: list          # list[SourceToken], in order
    stem: SourceToken     # token whose Strong's number is used for the dictionary link
    text: str             # concatenated script of all tokens
    lang: str             # lang of the stem token ('H', 'A', or 'G')
    is_proper: bool = False  # True if stem is a proper noun (pos=noun, type=proper)


@dataclass
class TargetToken:
    """One token from the BSB target TSV."""
    id: str
    verse_id: str
    text: str
    skip_space_after: bool = False
    exclude: bool = False

@dataclass
class AlignmentRecord:
    """One alignment record mapping source token IDs to target token IDs."""
    source_ids: list
    target_ids: list
    record_id: str

@dataclass
class IntralinearToken:
    """One output token: English text with aligned source language annotations.

    source_words is a list of SourceWord — one per display-word in the source.
    Each SourceWord carries its own script, Strong's number, and lang, so the
    renderer can emit one dictionary link per display-word.
    """
    english: str
    skip_space_after: bool
    source_words: list = field(default_factory=list)   # list[SourceWord]
    is_plain_text: bool = False
    notes: list = field(default_factory=list)  # list of {noteId, text} dicts


# ================== BOOK REFERENCE UTILITIES ==================

def build_book_num_map() -> dict:
    """Build a dict mapping zero-padded book number string to OSIS book name."""
    return {book.usfmnumber: book.osisID
            for book in Books().values()}

BOOK_NUM_MAP = build_book_num_map()


def verse_id_to_osis(verse_id: str) -> str:
    """Convert BBCCCVVV string to OSIS dotted ref like Gen.1.1"""
    book_num  = verse_id[:2]
    chapter   = int(verse_id[2:5])
    verse     = int(verse_id[5:8])
    book_name = BOOK_NUM_MAP.get(book_num, f"Book{book_num}")
    return f"{book_name}.{chapter}.{verse}"


# ================== LOADERS ==================

def load_annotations(path: Path) -> dict:
    """Load bsb_annotations.json. Returns {'headers': {}, 'notes': {}}
    or empty dicts if file not found."""
    if not path.exists():
        print(f"  Warning: annotations file not found at {path}, skipping")
        return {'headers': {}, 'notes': {}}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    h = len(data.get('headers', {}))
    n = len(data.get('notes', {}))
    print(f"  Loaded {h:,} headers and {n:,} note anchors from {path.name}")
    return data

def load_tsk(path: Path):
    if not path.exists():
        print(f"  Warning: TSK file not found at {path}, skipping")
        return dict()
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    print(f"  Loaded TSK file cross references from {path.name}")
    return data


def load_source_index(path: Path, testament: str) -> dict:
    """Load source TSV into a dict keyed by token id."""
    default_lang = 'H' if testament == 'ot' else 'G'
    index = {}
    with open(path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            lang = row.get('lang', default_lang)
            raw_strongs = (row.get('strongnumberx')
                           or row.get('strong')
                           or row.get('strongs')
                           or '')
            # Normalize: strip any existing H/G/A prefix and leading zeros, then re-add lang prefix
            if raw_strongs:
                # Strip leading H/G prefix if present (re-add from lang below)
                raw_strongs = re.sub(r'^[HGA]', '', raw_strongs)
                # Strip leading zeros, preserving trailing alpha (e.g. 0871a -> 871a)
                raw_strongs = re.sub(r'^0+(\w)', r'\1', raw_strongs)
                raw_strongs = lang + raw_strongs

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
                after=row.get('after', ' ')
            )
    print(f"  Loaded {len(index):,} source tokens from {path.name}")
    return index


def load_alignment_index(path: Path) -> dict:
    """Load alignment JSON into a dict keyed by verse_id (BBCCCVVV)."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)

    index = {}
    for rec in data['records']:
        record = AlignmentRecord(
            source_ids=rec['source'],
            target_ids=rec['target'],
            record_id=rec['meta']['id'],
        )
        verse_id = record.record_id.split('.')[0]
        index.setdefault(verse_id, []).append(record)

    print(f"  Loaded alignment records for {len(index):,} verses from {path.name}")
    return index


def iter_target_verses(path: Path, books_filter: list):
    """Iterate over BSB target TSV, yielding (verse_id, [TargetToken]) tuples."""
    allowed_book_nums = None
    if books_filter:
        reverse_map = {v: k for k, v in BOOK_NUM_MAP.items()}
        allowed_book_nums = set()
        for osis_id in books_filter:
            num = reverse_map.get(osis_id)
            if num:
                allowed_book_nums.add(num)
            else:
                print(f"  Warning: could not resolve book '{osis_id}'")

    current_verse_id = None
    current_tokens = []

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
                    current_tokens = []
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
                current_tokens = []

            current_tokens.append(token)

    if current_verse_id and current_tokens:
        yield current_verse_id, current_tokens


# ================== SOURCE WORD GROUPING ==================

def group_source_words(source_tokens: list) -> list:
    """Group a flat list of SourceTokens into SourceWords using the 'after' field.

    Tokens with after='' are joined to the next token (same display-word).
    Tokens with after=' ' (or any non-empty value) end their display-word.

    Within each display-word, the stem is the first token whose token_class
    is not in NON_STEM_CLASS. Falls back to the last token if all are non-stem
    (e.g. a bare article with no following noun in this alignment group).
    """
    if not source_tokens:
        return []

    words = []
    current = []

    for token in source_tokens:
        current.append(token)
        if token.after != '':
            # Word boundary — flush current group
            words.append(current)
            current = []

    # Flush any remaining tokens (last word in alignment group may not
    # have a trailing space in the source, but we still need to emit it)
    if current:
        words.append(current)

    result = []
    for group in words:
        # Find stem: first token whose class is not a prefix/particle
        stem = next(
            (t for t in group if t.token_class not in NON_STEM_CLASS),
            group[-1]  # fallback: last token
        )

        text = ''.join(t.text for t in group)
        lang = stem.lang

        is_proper = stem.noun_type == 'proper'

        result.append(SourceWord(
            tokens=group,
            stem=stem,
            text=text,
            lang=lang,
            is_proper=is_proper,
        ))

    return result


# ================== JOIN ==================

def join_verse(verse_id: str, target_tokens: list,
               alignment_records: list, source_index: dict,
               notes_index: dict) -> list:
    """Join target tokens with alignment records and source tokens.
    Attaches notes from notes_index to the appropriate IntralinearToken."""

    target_to_alignment = {}
    for rec in alignment_records:
        for tid in rec.target_ids:
            target_to_alignment[tid] = rec

    result = []
    seen_records = set()
    absorbed_ids = set()

    for i, token in enumerate(target_tokens):
        rec = target_to_alignment.get(token.id)

        if token.id in absorbed_ids:
            continue

        if token.exclude or rec is None:
            it = IntralinearToken(
                english=token.text,
                skip_space_after=token.skip_space_after,
                is_plain_text=True,
            )
            # Notes can attach to excluded/unaligned tokens too
            if token.id in notes_index:
                it.notes = notes_index[token.id]
            result.append(it)
            continue

        if rec.record_id in seen_records:
            continue

        seen_records.add(rec.record_id)

        rec_target_ids = set(rec.target_ids)

        group_positions = [j for j, t in enumerate(target_tokens)
                           if t.id in rec_target_ids]
        first_pos = group_positions[0]
        last_pos  = group_positions[-1]

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
        english = ''.join(parts)

        last_skip = span_tokens[-1].skip_space_after if span_tokens else False

        source_tokens = [
            source_index[sid] for sid in rec.source_ids if sid in source_index
        ]
        source_words = group_source_words(source_tokens)

        # Collect notes for any target token in this group
        token_notes = []
        for t in span_tokens:
            if not t.exclude and t.id in notes_index:
                token_notes.extend(notes_index[t.id])

        result.append(IntralinearToken(
            english=english,
            skip_space_after=last_skip,
            source_words=source_words,
            notes=token_notes,
        ))

    return result


# ================== TESTAMENT PROCESSING ==================

def process_testament(testament: str, sources: dict, books_filter: list,
                      writer, annotations: dict, tsk: dict):
    """Process one testament, adding verses to the writer."""
    tcfg = sources[testament]
    print(f"\n{'='*60}")
    print(f"Processing {testament.upper()}")
    print(f"{'='*60}")

    print("Loading source index...")
    source_index = load_source_index(tcfg['source'], testament)

    print("Loading alignment index...")
    alignment_index = load_alignment_index(tcfg['alignment'])

    headers     = annotations.get('headers', {})
    notes_index = annotations.get('notes', {})

    print("Processing verses...")
    verse_count = 0

    for verse_id, target_tokens in iter_target_verses(tcfg['target'], books_filter):
        alignment_records = alignment_index.get(verse_id, [])
        intralinear = join_verse(verse_id, target_tokens, alignment_records,
                                 source_index, notes_index)
        osis_ref = verse_id_to_osis(verse_id)
        header   = headers.get(osis_ref) if writer.headers else None
        xrefs    = tsk.get(verse_id, {})
        writer.add_verse(osis_ref, intralinear, header=header, xrefs=xrefs)
        verse_count += 1

    print(f"  Processed {verse_count:,} verses.")


# ================== MAIN ==================

if __name__ == '__main__':
    books_filter = config.get('books')
    book_abbrev  = [book.osisID for book in Books().values()]
    ot_abbrev    = set(book_abbrev[:39])
    nt_abbrev    = set(book_abbrev[60:87])

    xlit        = config.get('transliteration', {})
    transliterate = make_transliterator(
        hebrew_scheme=xlit.get('hebrew', 'brill_simple'),
        greek_scheme=xlit.get('greek', 'SIMPLE'),
    )

    print(f"Config: {CONFIG_FILE}")
    print(f"Translation: {config['translation']} v{config['version']}")

    print("Loading annotations...")
    annotations = load_annotations(config['annotations'])

    output_cfg    = config['output']
    output_format = output_cfg['format'].lower()
    render_mode   = output_cfg['mode']
    output_dir    = output_cfg['dir']
    abbrev        = config['abbrev']
    out_headers   = output_cfg.get('headers', 1)
    out_notes     = output_cfg.get('notes', 1)
    out_xref      = output_cfg.get('xref', 0)

    tsk = {}
    if out_xref:
        print("Loading TSK cross references...")
        tsk = load_tsk(config['tsk'])

    if output_format == 'mysword':
        from mysword_writer import MySwordWriter

        if render_mode == 'intralinear':
            base_abbrev = abbrev['intralinear']
            base_path   = output_dir / base_abbrev
            writer = MySwordWriter(transliterate=transliterate,
                                   render_mode='intralinear',
                                   headers=out_headers,
                                   notes=out_notes,
                                   xref=out_xref)
            writer.open(base_path, work_id=base_abbrev)
        else:
            base_abbrev = abbrev['interlinear']
            base_path   = output_dir / base_abbrev
            writer = MySwordWriter(transliterate=transliterate,
                                   render_mode='interlinear',
                                   headers=out_headers,
                                   notes=out_notes,
                                   xref=out_xref)
            writer.open(base_path, work_id=base_abbrev)

    elif output_format == 'esword':
        from esword_writer import ESwordWriter
        base_abbrev = abbrev['intralinear'] if render_mode == 'intralinear' \
                      else abbrev['interlinear']
        base_path   = output_dir / base_abbrev
        writer = ESwordWriter(transliterate=transliterate,
                              render_mode=render_mode,
                              headers=out_headers,
                              notes=out_notes,
                              xref=out_xref)
        writer.open(base_path, work_id=base_abbrev)

    else:  # osis
        base_path = output_dir / f"{config['translation']}.osis.xml"
        writer = OSISWriter(transliterate=transliterate)

    sources = config['sources']
    if 'ot' in sources and any(b in ot_abbrev for b in (books_filter or ['Gen'])):
        process_testament('ot', sources, books_filter, writer, annotations, tsk)

    if 'nt' in sources and any(b in nt_abbrev for b in (books_filter or ['Matt'])):
        process_testament('nt', sources, books_filter, writer, annotations, tsk)

    if output_format == 'osis':
        writer.write(base_path)
    else:
        writer.write(base_path)

    # For MySword intralinear, also produce the stacked variant by copying
    # the SQLite and swapping the Details table CSS/VerseRules.
    if output_format == 'mysword' and render_mode == 'intralinear':
        import shutil
        stacked_abbrev = abbrev['intralinear_stacked']
        stacked_path   = output_dir / stacked_abbrev
        src = writer.output_path
        dst = src.parent / (stacked_abbrev + MySwordWriter.file_extension)
        shutil.copy2(src, dst)
        writer.conn = __import__('sqlite3').connect(dst)
        writer.work_id = stacked_abbrev
        writer.render_mode = 'intralinear_stacked'
        writer.conn.execute("DROP TABLE IF EXISTS Details")
        writer.insert_details()
        writer.conn.commit()
        writer.conn.close()
        print(f"Stacked variant written to {dst}")
    r"""
    osis2mod.exe "$HOME\AppData\Roaming\Sword\modules\texts\ztext\bsbi" .\BSBi.osis.xml -z
    """