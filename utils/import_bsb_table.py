"""
import_bsb_table.py

One-time import: parse bsb_tables.tsv into a normalized SQLite `tokens`
table for TableComposer to read at pipeline run time.

bsb_tables.tsv has two columns both literally named "Parsing" (short code
and full description) and one header with embedded spaces (" BSB version
"). csv.DictReader collapses duplicate header names to the last one seen —
confirmed empirically to silently drop the short-code column — so this
module indexes columns positionally instead of trusting header text. That
also means it doesn't care what the file's header row actually says, so
there's no need to edit it.

See docs/DEVELOPMENT.md for the schema/gloss_type rationale — this was
worked out against the real file, not guessed.

Usage:
    python utils/import_bsb_table.py [--source FILE] [--output FILE]
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_SOURCE = ROOT / "local" / "bsb_tables.tsv"
DEFAULT_OUTPUT = ROOT / "data" / "bsb_tables.db"
BOOKS_DB       = ROOT / "data" / "books.db"
RMAC_JSON      = ROOT / "data" / "rmac.json"

# Full English book name, as cited in bsb_tables.tsv's VerseId column
# (e.g. "Genesis 1:1", "1 Samuel 3:2") -> OSIS book id, in canonical order.
# This is a BSB-citation-format quirk, not a general fact about books, so
# it stays local to this import script rather than living in data/books.db
# (which is shared pipeline-wide infrastructure). Can't be derived live from
# biblelib either: biblelib's own .name differs from the file in exactly two
# places ("Psalms" vs the file's "Psalm", "Song of Songs" vs "Song of
# Solomon") — verified against the actual file: exactly these 66 names, in
# exactly this order, appear as VerseId prefixes.
_FULL_NAMES_IN_CANONICAL_ORDER = [
    'Genesis', 'Exodus', 'Leviticus', 'Numbers', 'Deuteronomy',
    'Joshua', 'Judges', 'Ruth', '1 Samuel', '2 Samuel',
    '1 Kings', '2 Kings', '1 Chronicles', '2 Chronicles', 'Ezra',
    'Nehemiah', 'Esther', 'Job', 'Psalm', 'Proverbs',
    'Ecclesiastes', 'Song of Solomon', 'Isaiah', 'Jeremiah', 'Lamentations',
    'Ezekiel', 'Daniel', 'Hosea', 'Joel', 'Amos',
    'Obadiah', 'Jonah', 'Micah', 'Nahum', 'Habakkuk',
    'Zephaniah', 'Haggai', 'Zechariah', 'Malachi', 'Matthew',
    'Mark', 'Luke', 'John', 'Acts', 'Romans',
    '1 Corinthians', '2 Corinthians', 'Galatians', 'Ephesians', 'Philippians',
    'Colossians', '1 Thessalonians', '2 Thessalonians', '1 Timothy', '2 Timothy',
    'Titus', 'Philemon', 'Hebrews', 'James', '1 Peter',
    '2 Peter', '1 John', '2 John', '3 John', 'Jude',
    'Revelation',
]
# The full books table (all 66 rows, unfiltered) gets copied verbatim into
# bsb_tables.db's own `books` table below — so a query against bsb_tables.db
# alone (e.g. "just the OT" or "the first 15 books") doesn't need a second
# connection to data/books.db just to get testament/canon_order.
with sqlite3.connect(BOOKS_DB) as _conn:
    _BOOKS_ROWS = _conn.execute(
        "SELECT usx_code, osis_id, display_abbrev, usfm_number, testament, "
        "canon_order FROM books ORDER BY canon_order").fetchall()
    _osis_in_canon_order = [r[1] for r in _BOOKS_ROWS]
    _OSIS_TO_ABBREV = {r[1]: r[2] for r in _BOOKS_ROWS}
FULL_NAME_TO_OSIS = dict(zip(_FULL_NAMES_IN_CANONICAL_ORDER, _osis_in_canon_order))

# ------------------------------------------------------------- column layout

COL_HEB_SORT      = 0
COL_GRK_SORT      = 1
COL_BSB_SORT      = 2
COL_VERSE_NUM     = 3
COL_LANGUAGE      = 4
COL_SOURCE_TEXT   = 5
# COL 6 is the apparatus-annotated WLC/Nestle text ({TR} etc.) — deliberately
# not modeled; see DEVELOPMENT.md.
COL_TRANSLIT      = 7
COL_PARSING_SHORT = 8
COL_PARSING_FULL  = 9
COL_STR_HEB       = 10
COL_STR_GRK       = 11
COL_VERSE_ID      = 12
COL_HDG           = 13
COL_CROSSREF      = 14
COL_PAR           = 15
COL_SPACE         = 16
COL_BEGQ          = 17
COL_BSB_VERSION   = 18
COL_PNC           = 19
COL_ENDQ          = 20
COL_FOOTNOTES     = 21
COL_END_TEXT      = 22

_LANGUAGE_MAP = {'Hebrew': 'H', 'Aramaic': 'A', 'Greek': 'G'}
_VERSE_ID_RE  = re.compile(r'^(.+?)\s+(\d+):(\d+)$')

# BegQ occasionally holds the source website's own verse-number anchor
# markup instead of a real opening quote mark — confirmed as exactly one
# literal value, '<span class=|reftext|><a href=|#|><b>1</b></a></span>',
# across all 116 occurrences, always on a psalm's first content verse (the
# explicit "1" the site shows when the psalm's superscription itself isn't
# counted as verse 1). Not real quote content; stripped rather than glued
# onto the token's assembled text as literal HTML (e.g. Psalm 121:1).
_REFTEXT_MARKER_RE = re.compile(r'<span class=\|reftext\|>.*?</span>')


def _strip_reftext_marker(value: str | None) -> str | None:
    if not value:
        return value
    cleaned = _REFTEXT_MARKER_RE.sub('', value).strip()
    return cleaned or None

# The BSB version column has occasional typographical inconsistencies of its
# own — e.g. Exodus 12:42's "[is to be a vigil ]" (space before the closing
# bracket), Leviticus 1:17's "[ the bird ]" (space after the opening one
# too), and Leviticus 7:9's "Likewise , every" (space before the comma).
# Not a pipeline bug, just the source data; cleaned up on the way in.
_SPACE_BEFORE_PUNCT_RE = re.compile(r'\s+([,.;:!?)\]’”])')
_SPACE_AFTER_OPEN_RE = re.compile(r'([(\[])\s+')
_INTERNAL_WHITESPACE_RE = re.compile(r'\s{2,}')


def _normalize_english(text: str) -> str:
    text = _SPACE_BEFORE_PUNCT_RE.sub(r'\1', text)
    text = _SPACE_AFTER_OPEN_RE.sub(r'\1', text)
    text = _INTERNAL_WHITESPACE_RE.sub(' ', text)
    return _TYPO_FIXES.get(text, text)


# One-off exact-string corrections to the BSB version's own wording, found
# while auditing tokens for the restored-names build (utils/build_restored_names.py):
# a lone all-caps "THE LORD" (vs. the surrounding text's consistent "the
# LORD") that reads as a stray shift-key slip rather than deliberate
# emphasis -- unlike e.g. "TO THE LORD", which recurs and is left alone.
# Matched by exact post-normalization string, not by bsb_sort, since the
# rest of the pipeline doesn't otherwise need to know which verse it's in.
_TYPO_FIXES = {
    'THE LORD': 'the LORD',
}


# Eleven rows carry the properly-spaced ". . ." continuation marker glued to
# real content in the same cell, e.g. Numbers 26:18's raw BSB version is
# " . . . 40,500 " — not the exact-match ". . ." that gets classified as a
# continuation row, so it falls through to plain text with the marker still
# embedded. Confirmed against the actual published BSB text row by row: these
# eleven have no ellipsis in the real text (the marker is a leftover
# artifact, not content) and should just become their trailing content
# ("40,500"). Ephesians 3:14 looks similar at a glance but is NOT one of
# these — its raw value is " ... for this reason " (a bare, unspaced "...",
# a completely different string), and it genuinely does have an ellipsis in
# the published text (Paul's sentence resuming after the digression since
# 3:1), so it's deliberately excluded and left untouched.
#
# This has to run *before* _normalize_english(): that function's
# space-before-punctuation cleanup would otherwise match the spaces inside
# ". . ." too (each "space then period" looks identical to "word ." needing
# its space removed) and mangle the marker into "..." instead of stripping
# it — which is exactly how this was first miscategorized as a bare-ellipsis
# bug instead of a leftover-marker bug.
#
# Keyed by bsb_sort since that's this file's own stable identifier; if
# bsb_tables.tsv is ever refreshed, re-verify this list rather than trusting
# it blindly.
_ELLIPSIS_ARTIFACT_BSB_SORTS = {
    106223, 106302, 106356, 106397, 106531,  # Numbers 26 census verses
    106590, 106672, 106712, 106786, 106841,
    638979,  # John 21:7
}
_SPACED_ELLIPSIS_PREFIX_RE = re.compile(r'^\.\s*\.\s*\.\s*')


def _strip_ellipsis_artifact(bsb_sort: int, text: str) -> str:
    if bsb_sort in _ELLIPSIS_ARTIFACT_BSB_SORTS:
        return _SPACED_ELLIPSIS_PREFIX_RE.sub('', text)
    return text

# heading and crossref are verse-level facts, not token-level ones — moved
# to their own `verses` table rather than living on whichever token happens
# to be first in bsb_sort order (that "first" token isn't meaningful once
# source_sort ordering is used, e.g. forward interlinear). Verified against
# the real file: all 1328 non-null Crossref values sit on the verse's first
# bsb_sort row with zero exceptions once 3 known mis-column rows are
# corrected — those 3 (BSB Sort 392396, 457348, 560983) hold Par-shaped
# content ("<p class=|...|>") instead of a real cross-reference
# ("<br /><span class=|cross|>..."), and belong on that token's own
# par_class, not on the verse's crossref.
_CROSSREF_MISFILED_PREFIX = '<p class='

# The raw Crossref cell is HTML: '<br /><span class=|cross|>(<a href
# =|../john/1.htm|>John 1:1–5</a>; <a href =|../hebrews/11.htm|>Hebrews
# 1:1-3</a>)</span>' — pull out each <a> tag's label text and convert it to
# the same plain "Joh 1:1-5; Heb 11:1-3" shape AlignmentComposer's own
# bsb_xrefs.json already uses, so TableComposer needs no special-casing to
# feed VerseFormatter.render_crossref(). Verified against all 2,033 <a>
# labels across the file's 1,325 cross-reference cells: every one parses as
# either a normal book/chapter:verse[-verse] reference (2,028, including one
# cross-chapter range, "1 Chronicles 15:29–16:3"), a whole-chapter range with
# no verse (4: "Genesis 4–9", "Genesis 15–22", "Genesis 27–50", "Exodus
# 2–15"), or a book-only span (1: "Joshua–Malachi") — zero unparseable
# labels, zero unknown book names.
_XREF_LABEL_RE      = re.compile(r'<a[^>]*>([^<]+)</a>')
_XREF_VERSE_RE      = re.compile(r'^(.+?)\s+(\d+):(\d+)(?:-(?:(\d+):)?(\d+))?$')
_XREF_CHAP_RANGE_RE = re.compile(r'^(.+?)\s+(\d+)-(\d+)$')
_XREF_BOOK_SPAN_RE  = re.compile(r'^(.+?)-(.+)$')


def _abbrev_book(full_name: str) -> str | None:
    osis = FULL_NAME_TO_OSIS.get(full_name)
    return _OSIS_TO_ABBREV.get(osis) if osis else None


def _convert_xref_label(label: str) -> str:
    """One <a> tag's label text ("John 1:1–5", "Genesis 4–9", "Joshua–
    Malachi") -> our abbreviated plain-text shape ("Joh 1:1-5", "Gen 4-9",
    "Jos-Mal"). Falls back to the original label, verbatim, if a book name
    doesn't resolve — defensive only; not expected given the verification
    above.
    """
    label = label.replace('–', '-').strip()

    m = _XREF_VERSE_RE.match(label)
    if m:
        book, chap, verse, end_chap, end_verse = m.groups()
        abbrev = _abbrev_book(book)
        if abbrev:
            ref = f"{abbrev} {chap}:{verse}"
            if end_verse:
                ref += f"-{end_chap}:{end_verse}" if end_chap else f"-{end_verse}"
            return ref

    m = _XREF_CHAP_RANGE_RE.match(label)
    if m:
        book, chap, end_chap = m.groups()
        abbrev = _abbrev_book(book)
        if abbrev:
            return f"{abbrev} {chap}-{end_chap}"

    m = _XREF_BOOK_SPAN_RE.match(label)
    if m:
        book1, book2 = m.groups()
        a1, a2 = _abbrev_book(book1), _abbrev_book(book2)
        if a1 and a2:
            return f"{a1}-{a2}"

    return label


def _parse_crossref_cell(raw: str) -> str | None:
    labels = _XREF_LABEL_RE.findall(raw)
    if not labels:
        return None
    return '; '.join(_convert_xref_label(lbl) for lbl in labels)

# BSB's own Parsing (short) column already matches RMAC one-for-one for every
# non-verb Hebrew/Greek category (nouns, adjectives, articles, prepositions,
# pronouns, the direct-object marker, numbers) and for the great majority of
# Greek verb forms — just not case-for-case, and RMAC spells the ambiguous
# middle/passive slash as a hyphen (`V-PIM/P-1P` -> `V-PIM-P-1P`). Confirmed
# against data/rmac.json (2,492 codes extracted from the real MySword/e-Sword
# RMAC dictionary module — not ours to extend, since that module is already
# installed by thousands of readers): every one of those categories resolves
# once normalized this way.
#
# A compound Parsing value (a fused Hebrew word carrying more than one
# morpheme, joined with " | ", e.g. `Prep-b | N-fs`, sometimes with several
# stacked prefixes comma-separated within one slot, e.g.
# `Conj-w, Prep-l, Art | N-ms`) is resolved segment-by-segment and stored
# pipe-delimited (`PREP-B|N-FS`) so the verse renderer can split it back
# apart and emit one linked <tvm> per morpheme.
#
# A trailing bare pronominal-suffix segment (`2ms`, `3fs`...) is NOT a
# separate linkable morpheme -- confirmed against data/rmac.json: it's fused
# onto the *stem's own* code with a hyphen (`N-fsc | 3ms` -> `N-FSC-3MS`,
# `Prep | 3ms` -> `PREP-3MS`), not stored as its own pipe segment. A rare
# variant-form suffix carries a trailing digit BSB's own data doesn't explain
# (`2fs2`); tried as-is first, then with the digit stripped.
#
# One segment shape never resolves, and the whole token's morph is left NULL
# rather than a partial/guessed result: Hebrew/Aramaic verb stem+conjugation
# forms (`V-Qal-Perf-3ms`, `V-Hifil-Prtcpl-ms`) -- RMAC has no Hebrew verb
# morphology at all; needs a real stem/conjugation equivalence table, not
# attempted here. (A small number of other combinations -- proper noun +
# suffix, interjection + suffix -- also have no fused entry in rmac.json;
# same treatment, left NULL rather than guessed.)
with open(RMAC_JSON, encoding='utf-8') as _f:
    _RMAC_CODES = set(json.load(_f))

# Hebrew's article and conjunctive waw carry no case/gender/number of their
# own (unlike Greek's), so a bare `ART`/`CONJ-W` code is the linguistically
# correct one even though the real RMAC dictionary — Greek-only — has no
# entry for either. Stored anyway: it'll display as plain, unlinked text in
# the reader (the popup dictionary just won't resolve it), which beats
# dropping the tag entirely.
_KNOWN_GOOD_UNLINKED = {'ART', 'CONJ-W'}

_SUFFIX_PRONOUN_RE = re.compile(r'^[1-3][a-z]{2}[a-z0-9]?$', re.IGNORECASE)

_HEBREW_VERB_STEMS = frozenset({
    'QAL', 'NIFAL', 'NIPHAL', 'PIEL', 'PUAL', 'HIFIL', 'HIPHIL', 'HOFAL',
    'HOPHAL', 'HITPAEL', 'HITHPAEL', 'NITHPAEL', 'QALPASS', 'POLEL', 'POLAL',
    'HITPOLEL', 'PILPEL', 'PALEL', 'PULAL', 'HISHTAPHEL', 'TIPHIL', 'POEL',
    'POAL',
})

# Hebrew's binyan (stem) encodes voice-like distinctions derivationally --
# there's no 1:1 match to Greek's three voices, but this is the standard
# approximation: Qal/Piel/Hifil are the "active" stems (simple/intensive/
# causative); Nifal/Pual/Hofal are their passives; Hitpael/Nithpael are
# reflexive/reciprocal, closest to Greek's middle.
_BINYAN_VOICE = {
    'QAL': 'A', 'PIEL': 'A', 'HIFIL': 'A', 'HIPHIL': 'A',
    'NIFAL': 'P', 'NIPHAL': 'P', 'PUAL': 'P', 'HOFAL': 'P', 'HOPHAL': 'P',
    'HITPAEL': 'M', 'HITHPAEL': 'M', 'NITHPAEL': 'M',
    'QALPASS': 'P',
    'POLEL': 'A', 'PILPEL': 'A', 'PALEL': 'A', 'POEL': 'A', 'TIPHIL': 'A',
    'POAL': 'P', 'PULAL': 'P',
    'HITPOLEL': 'M', 'HISHTAPHEL': 'M',
}

# Hebrew's conjugation (aspect-based) has no 1:1 match to Greek's tense
# system either. Approximation used here, to be revisited once checked
# against real usage:
#   Perfect / wayyiqtol (narrative past) -> Aorist Indicative -- Hebrew's
#     two main "completed action" forms, both read as simple past in
#     narrative; RMAC's Perfect tense is stative/resultative and would
#     overstate that nuance across thousands of tokens.
#   Imperfect / weqatal (prospective/incomplete) -> Future Indicative.
#   Cohortative/jussive (volitional) -> Aorist Subjunctive -- Greek has no
#     future subjunctive, and subjunctive is the closest functional match
#     for "let it happen" volitional forms.
#   Imperative -> Aorist Imperative.
#   Infinitive construct/absolute -> both collapse to one Aorist Infinitive
#     code; RMAC has no construct/absolute distinction to preserve.
#   Participle -> Present Participle -- Hebrew's participle is durative/
#     ongoing in force, closer to Greek's present than aorist participle.
# (tense, mood) letters; voice comes from _BINYAN_VOICE separately.
_CONJUGATION_TENSE_MOOD = {
    'PERF': ('A', 'I'), 'CONSECIMPERF': ('A', 'I'),
    'IMPERF': ('F', 'I'), 'CONJPERF': ('F', 'I'), 'CONJIMPERF': ('F', 'I'),
    'IMP': ('A', 'M'),
    'INF': ('A', 'N'), 'INFABS': ('A', 'N'),
    'PRTCPL': ('P', 'P'), 'QALPASSPRTCPL': ('P', 'P'),
}
# Cohortative/jussive variants (encoded as e.g. "Imperf.Cohort",
# "ConjImperf.Jus") each override mood regardless of the base conjugation --
# see _compose_hebrew_verb for why they DON'T share one mapping. The
# paragogic-he variant (".h") carries no mood change of its own -- same
# (tense, mood) as its base conjugation.

_PERSON_NUMBER_RE = re.compile(r'^([1-3])[cmf]([sp])$', re.IGNORECASE)
_GENDER_NUMBER_RE = re.compile(r'^([cmf])([sp])(?:[cd])?$', re.IGNORECASE)
_IMPERATIVE_TAIL_RE = re.compile(r'^[cmf]([sp])$', re.IGNORECASE)

_PARTICIPLE_GENDER = {'M': 'M', 'F': 'F', 'C': 'M'}  # RMAC has no common gender; default to masculine

# Hebrew's directional/locative *he* (e.g. Egypt -> "Egypt-ward") is
# orthographically identical to a 3rd-person singular possessive suffix --
# same final letter either way -- so BSB's data tags it with the same
# `3fs`/`3ms`-shaped code, even though it's not a possessor at all.
# Confirmed against real examples (Gen 10:19, 12:10, 18:22, 26:1...): every
# `N-proper-fs | 3fs` gloss is directional ("toward Gerar", "to Egypt", "at
# Sodom"), never possessive ("her Egypt"). The tell: a genuine possessive
# suffix's person/gender/number is independent of the noun's own -- it
# would be a coincidence for a random possessor to always match the head
# noun's own gender/number. Here it always does, because it's the same
# marker. Greek has its own tag for exactly this use (a place name used
# adverbially for direction), the "Location" suffix -- reused here instead
# of a fictitious possessive fusion.
_DIRECTIONAL_HE_LOCATION = {'FS': 'N-ASF-L', 'MS': 'N-ASM-L'}


def _directional_he_location(stem_upper: str, suffix: str) -> str | None:
    if not stem_upper.startswith('N-PROPER-'):
        return None
    gender_number = stem_upper[len('N-PROPER-'):]
    if suffix[:1] != '3' or suffix[1:] != gender_number:
        return None
    return _resolve_code(_DIRECTIONAL_HE_LOCATION.get(gender_number))


_OBJECT_SUFFIX_RE = re.compile(r'^([1-3])([CMF])([SP])[A-Z0-9]?$')


def _compose_object_pronoun(suffix: str) -> str | None:
    """A verb's pronominal suffix is a direct object -- maps to RMAC's
    accusative personal pronoun. Hebrew's 1st person suffix carries no
    gender (always common), matching RMAC's genderless PPRO-A1S/A1P forms;
    2nd/3rd person are always gendered in Hebrew, matching RMAC's gendered
    PPRO-AM.../PPRO-AF... forms."""
    m = _OBJECT_SUFFIX_RE.match(suffix)
    if not m:
        return None
    person, gender, number = m.groups()
    if gender == 'C':
        return _resolve_code(f'PPRO-A{person}{number}')
    return _resolve_code(f'PPRO-A{gender}{person}{number}')


def _compose_hebrew_verb(segment: str) -> str | None:
    """Compose an RMAC verb code from a Hebrew Parsing value the CSV
    catalogued but never translated (e.g. `V-Qal-ConsecImperf-3ms` ->
    `V-AAI-3S`). See the module docstring above for the binyan/conjugation
    approximations this rests on -- a real linguistic judgment call, not a
    mechanical fact, and worth revisiting against real usage."""
    parts = segment.split('-')
    if len(parts) < 3 or parts[0].upper() != 'V':
        return None
    binyan = parts[1].upper()
    voice = _BINYAN_VOICE.get(binyan)
    if voice is None:
        return None

    conj_field = parts[2]
    conj_base, _, variant = conj_field.upper().partition('.')
    if conj_base == 'QALPASSPRTCPL':
        voice = 'P'  # passive regardless of binyan -- that's the whole point of this form

    tail = parts[3] if len(parts) > 3 else None

    # Cohortative (1st person volitional) and jussive (3rd person volitional)
    # DON'T share a mapping, confirmed against real LXX renderings (Gen
    # 1:3, 1:26): cohortative POIHSWMEN "let us make" is Aorist Active
    # SUBJUNCTIVE (Greek has no 1st-person imperative, subjunctive is the
    # functional match) -- but jussive GENHQHTW "let there be" and
    # ARXETWSAN "let them rule" are both Aorist/Present *Imperative*,
    # 3rd person -- because Greek, unlike English, actually has a 3rd
    # person imperative, and that's what jussive maps onto directly.
    if variant == 'COHORT':
        tense, mood = 'A', 'S'
    elif variant == 'JUS':
        tense, mood = 'A', 'M'
        if tail is None:
            return None
        m = _PERSON_NUMBER_RE.match(tail)
        if not m:
            return None
        person, number = m.groups()
        return _resolve_code(f'V-{tense}{voice}{mood}-{person}{number.upper()}')
    else:
        tense_mood = _CONJUGATION_TENSE_MOOD.get(conj_base)
        if tense_mood is None:
            return None
        tense, mood = tense_mood

    if mood == 'N':  # infinitive: no person/number
        return _resolve_code(f'V-{tense}{voice}{mood}')

    if mood == 'P':  # participle: case(default N)+number+gender, no construct
        if tail is None:
            return None
        m = _GENDER_NUMBER_RE.match(tail)
        if not m:
            return None
        gender, number = m.groups()
        gender = _PARTICIPLE_GENDER.get(gender.upper())
        if gender is None:
            return None
        return _resolve_code(f'V-{tense}{voice}{mood}-N{number.upper()}{gender}')

    if mood == 'M':  # imperative (from the Imp conjugation itself): always 2nd person
        if tail is None:
            return None
        m = _IMPERATIVE_TAIL_RE.match(tail)
        if not m:
            return None
        return _resolve_code(f'V-{tense}{voice}{mood}-2{m.group(1).upper()}')

    # indicative / subjunctive: person + number, gender dropped
    if tail is None:
        return None
    m = _PERSON_NUMBER_RE.match(tail)
    if not m:
        return None
    person, number = m.groups()
    return _resolve_code(f'V-{tense}{voice}{mood}-{person}{number.upper()}')


def _is_hebrew_verb_stem(segment: str) -> bool:
    parts = segment.upper().split('-')
    return len(parts) > 1 and parts[0] == 'V' and parts[1] in _HEBREW_VERB_STEMS


def _resolve_code(code: str) -> str | None:
    return code if (code in _RMAC_CODES or code in _KNOWN_GOOD_UNLINKED) else None


def _resolve_segment(segment: str) -> str | None:
    if _is_hebrew_verb_stem(segment):
        return _compose_hebrew_verb(segment)
    return _resolve_code(segment.upper().replace('/', '-'))


def _resolve_morph(parsing_short: str | None) -> str | None:
    if not parsing_short:
        return None
    groups = [g.strip() for g in parsing_short.split('|') if g.strip()]
    if not groups:
        return None

    # Paragogic nun ("Pn") is an emphatic/energic marker on some imperfect
    # 2nd/3rd plural forms -- no RMAC morpheme of its own, so it's dropped
    # rather than treated as an unresolvable suffix. It can appear alone
    # (`V-Qal-Imperf-3mp | Pn`) or alongside a real object suffix
    # (`V-Piel-Imperf-3mp | 1cs, Pn`).
    if len(groups) >= 2:
        last_items = [x.strip() for x in groups[-1].split(',')]
        if any(x.upper() == 'PN' for x in last_items):
            remaining = [x for x in last_items if x.upper() != 'PN']
            if remaining:
                groups[-1] = ', '.join(remaining)
            else:
                groups.pop()

    # A trailing bare suffix isn't its own morpheme -- it fuses onto the
    # stem's own code with a hyphen (N-fsc | 3ms -> N-FSC-3MS). A rare
    # variant-form suffix carries a trailing digit or letter BSB's own data
    # doesn't explain (`2fs2`, `1cse`) -- ignored, resolved as the base form.
    suffix = None
    if len(groups) >= 2 and _SUFFIX_PRONOUN_RE.match(groups[-1]):
        suffix = groups.pop().upper()

    segments = [s.strip() for group in groups for s in group.split(',') if s.strip()]
    if not segments:
        return None

    codes = [_resolve_segment(s) for s in segments[:-1]]
    stem = segments[-1]

    if suffix is None:
        codes.append(_resolve_segment(stem))
    elif _is_hebrew_verb_stem(stem):
        # A verb's own suffix is a pronominal *object*, not a possessive
        # like a noun's -- Greek has no equivalent fused verb+object-pronoun
        # code, so this stores it as its own separate linked morpheme
        # (an accusative personal pronoun) rather than trying to fuse it.
        # If the verb itself resolves but we don't have a confident mapping
        # for this particular suffix, the verb code is still worth keeping
        # -- the raw suffix rides along bracketed (unlinked) rather than
        # losing the verb's own match entirely.
        verb_code = _compose_hebrew_verb(stem)
        codes.append(verb_code)
        if verb_code is not None:
            pronoun_code = _compose_object_pronoun(suffix)
            codes.append(pronoun_code if pronoun_code is not None else f'[{suffix}]')
    else:
        stem_upper = stem.upper().replace('/', '-')
        fused = (_resolve_code(f'{stem_upper}-{suffix}')
                 or _resolve_code(f'{stem_upper}-{re.sub(r"[0-9]$", "", suffix)}')
                 or _directional_he_location(stem_upper, suffix))
        if fused is not None:
            codes.append(fused)
        else:
            # No fused or composed form exists for this stem+suffix pairing
            # (e.g. Number-mdc | 3mp, Interjection | 1cs -- rmac.json has no
            # entry either way). Rather than lose a stem that resolves fine
            # on its own, link that and carry the raw suffix along
            # unlinked/bracketed instead of discarding the whole token.
            bare = _resolve_code(stem_upper)
            codes.append(bare)
            if bare is not None:
                codes.append(f'[{suffix}]')

    if any(code is None for code in codes):
        return None
    return '|'.join(codes)


_INSERT_COLUMNS = [
    'bsb_sort', 'verse_id', 'source_sort', 'language',
    'source_text', 'translit', 'strongs', 'parsing_short', 'parsing_full',
    'morph', 'gloss_type', 'english', 'parent_id',
    'beg_quote', 'end_quote', 'punctuation', 'space',
    'suffix_html', 'par_class', 'footnote',
]

DDL = """
CREATE TABLE books (
    usx_code       TEXT PRIMARY KEY,   -- 'GEN', 'MAT', ... (biblelib's own dict key)
    osis_id        TEXT UNIQUE NOT NULL,
    display_abbrev TEXT NOT NULL,      -- 'Gen', 'Mat', '1Co', ... (TSK_ABBREV convention)
    usfm_number    INTEGER NOT NULL,
    testament      TEXT NOT NULL CHECK(testament IN ('OT','NT')),
    canon_order    INTEGER NOT NULL UNIQUE
);

CREATE TABLE verses (
    verse_id INTEGER PRIMARY KEY,  -- bsb_tables.tsv's own "Verse" column
    book     TEXT NOT NULL REFERENCES books(osis_id),
    chapter  INTEGER NOT NULL,
    verse    INTEGER NOT NULL,
    heading  TEXT,
    crossref TEXT
);
CREATE INDEX verses_book ON verses (book, verse_id);

-- One row per book/chapter, populated once from verses (see
-- import_bsb_table()'s post-processing step) rather than every consumer
-- re-running SELECT MAX(verse) ... GROUP BY book, chapter for itself --
-- e.g. utils/build_heb_devotional_esword.py needs a chapter's real verse
-- count to turn a bare "book chapter" reference into the verse range
-- e-Sword's <ref> tag actually requires.
CREATE TABLE chapters (
    book        TEXT NOT NULL REFERENCES books(osis_id),
    chapter     INTEGER NOT NULL,
    verse_count INTEGER NOT NULL,
    PRIMARY KEY (book, chapter)
);

CREATE TABLE tokens (
    bsb_sort      INTEGER PRIMARY KEY,
    verse_id      INTEGER NOT NULL REFERENCES verses(verse_id),
    source_sort   REAL NOT NULL,
    language      TEXT NOT NULL CHECK(language IN ('H','A','G')),
    source_text   TEXT NOT NULL,
    translit      TEXT,
    strongs       TEXT,
    parsing_short TEXT,
    parsing_full  TEXT,
    morph         TEXT,  -- RMAC code, populated only where parsing_short maps directly (see _resolve_morph)
    gloss_type    TEXT NOT NULL
                  CHECK(gloss_type IN ('text','untranslated',
                                       'continuation_after','continuation_before')),
    english       TEXT,
    parent_id     INTEGER REFERENCES tokens(bsb_sort),
    beg_quote     TEXT,
    end_quote     TEXT,
    punctuation   TEXT,
    space         TEXT,
    suffix_html   TEXT,
    par_class     TEXT,
    footnote      TEXT
);
CREATE INDEX tokens_verse      ON tokens (verse_id, bsb_sort);
CREATE INDEX tokens_source_sort ON tokens (source_sort);
"""

_INSERT_SQL = (
    "INSERT INTO tokens (" + ", ".join(_INSERT_COLUMNS) + ") VALUES ("
    + ", ".join("?" for _ in _INSERT_COLUMNS) + ")"
)
_INSERT_VERSE_SQL = "INSERT INTO verses (verse_id, book, chapter, verse, heading, crossref) VALUES (?, ?, ?, ?, ?, ?)"
_INSERT_BOOKS_SQL = (
    "INSERT INTO books (usx_code, osis_id, display_abbrev, usfm_number, "
    "testament, canon_order) VALUES (?, ?, ?, ?, ?, ?)"
)


def import_bsb_table(tsv_path: Path, db_path: Path, batch_size: int = 5000) -> None:
    """Parse bsb_tables.tsv into a fresh SQLite database at db_path.

    Single forward pass over the file (already in bsb_sort order). Rows with
    gloss_type 'continuation_before' ("vvv") are buffered until the next
    'text'/'untranslated' owner is found; rows with gloss_type
    'continuation_after' (". . .") resolve immediately against the current
    owner — except when a vvv buffer is already open, in which case the
    ". . ." is actually part of that same forward-looking group (confirmed
    against 7 real cases in the file where "vvv" is immediately followed by
    ". . ." with no owner between them).
    """
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)
    cur = conn.cursor()
    cur.executemany(_INSERT_BOOKS_SQL, _BOOKS_ROWS)

    batch: list = []

    def flush():
        if batch:
            cur.executemany(_INSERT_SQL, batch)
            batch.clear()

    def row_tuple(params: dict) -> tuple:
        return tuple(params[c] for c in _INSERT_COLUMNS)

    pending_vvv: list = []
    current_owner_bsb_sort = None
    verse_id = None
    prev_verse_col = None
    discarded_at_boundary = 0
    misfiled_crossref_count = 0
    row_count = 0

    with open(tsv_path, encoding='utf-8', newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        next(reader)  # header — not inspected; see module docstring

        for cols in reader:
            src_text = cols[COL_SOURCE_TEXT].strip()
            if not src_text:
                discarded_at_boundary += len(pending_vvv)
                pending_vvv = []
                current_owner_bsb_sort = None
                continue

            verse_col   = cols[COL_VERSE_NUM].strip()
            par_correction = None
            if verse_col != prev_verse_col:
                prev_verse_col = verse_col
                verse_id = int(verse_col)
                vid = cols[COL_VERSE_ID].strip()
                if vid:
                    m = _VERSE_ID_RE.match(vid)
                    book_name, chap_s, verse_s = m.groups()
                    book, chapter, verse = FULL_NAME_TO_OSIS[book_name], int(chap_s), int(verse_s)

                    heading  = cols[COL_HDG] or None
                    crossref = cols[COL_CROSSREF] or None
                    if crossref and crossref.startswith(_CROSSREF_MISFILED_PREFIX):
                        # Par-shaped content sitting in the Crossref column —
                        # redirect it onto this row's own par_class instead.
                        par_correction = crossref
                        crossref = None
                        misfiled_crossref_count += 1
                    elif crossref:
                        crossref = _parse_crossref_cell(crossref)
                    cur.execute(_INSERT_VERSE_SQL, (verse_id, book, chapter, verse, heading, crossref))
                discarded_at_boundary += len(pending_vvv)
                pending_vvv = []
                current_owner_bsb_sort = None

            language = _LANGUAGE_MAP.get(cols[COL_LANGUAGE])
            if language is None:
                continue  # stray/garbage Language value; only seen paired with blank source text

            bsb_sort    = int(cols[COL_BSB_SORT])
            source_sort = float(cols[COL_GRK_SORT]) if language == 'G' else float(cols[COL_HEB_SORT])
            strongs     = (cols[COL_STR_GRK] if language == 'G' else cols[COL_STR_HEB]).strip() or None

            bsb_version = cols[COL_BSB_VERSION].strip()
            extra_punctuation = None
            if bsb_version == '-':
                gloss_type, english = 'untranslated', None
            elif bsb_version == '. . .':
                gloss_type, english = 'continuation_after', None
            elif bsb_version == 'vvv':
                gloss_type, english = 'continuation_before', None
            else:
                bsb_version = _strip_ellipsis_artifact(bsb_sort, bsb_version)
                english = _normalize_english(bsb_version)
                if english and not any(c.isalnum() for c in english):
                    # John 21:7's stripped residue is bare ")" -- pure
                    # punctuation with no translated content. Left in
                    # `english`, TableComposer's merge routing (which only
                    # looks at the punctuation/end_quote *fields*, not raw
                    # text) wouldn't know to attach it to the previous word;
                    # moving it to `punctuation` lets that logic work as designed.
                    gloss_type, english, extra_punctuation = 'untranslated', None, english
                else:
                    gloss_type = 'text'

            params = dict(
                bsb_sort=bsb_sort, verse_id=verse_id,
                source_sort=source_sort, language=language,
                source_text=cols[COL_SOURCE_TEXT],
                translit=cols[COL_TRANSLIT] or None,
                strongs=strongs,
                parsing_short=cols[COL_PARSING_SHORT] or None,
                parsing_full=cols[COL_PARSING_FULL] or None,
                morph=_resolve_morph(cols[COL_PARSING_SHORT]),
                gloss_type=gloss_type,
                english=english,
                parent_id=None,
                beg_quote=_strip_reftext_marker(cols[COL_BEGQ] or None),
                end_quote=cols[COL_ENDQ] or None,
                punctuation=extra_punctuation or cols[COL_PNC] or None,
                space=cols[COL_SPACE] or None,
                suffix_html=cols[COL_END_TEXT] or None,
                par_class=par_correction or cols[COL_PAR] or None,
                footnote=cols[COL_FOOTNOTES] or None,
            )

            if gloss_type in ('text', 'untranslated'):
                for pending in pending_vvv:
                    pending['parent_id'] = bsb_sort
                    batch.append(row_tuple(pending))
                pending_vvv = []
                current_owner_bsb_sort = bsb_sort
                batch.append(row_tuple(params))
            elif gloss_type == 'continuation_before':
                pending_vvv.append(params)
            elif gloss_type == 'continuation_after':
                if pending_vvv:
                    pending_vvv.append(params)
                else:
                    params['parent_id'] = current_owner_bsb_sort
                    batch.append(row_tuple(params))

            row_count += 1
            if len(batch) >= batch_size:
                flush()

        discarded_at_boundary += len(pending_vvv)  # trailing, at EOF

    flush()
    conn.execute(
        "INSERT INTO chapters (book, chapter, verse_count) "
        "SELECT book, chapter, MAX(verse) FROM verses GROUP BY book, chapter"
    )
    conn.commit()
    conn.close()

    print(f"Imported {row_count:,} rows from {tsv_path.name} to {db_path}")
    print(f"Copied {len(_BOOKS_ROWS)} books from {BOOKS_DB.name} into {db_path.name}'s own books table")
    if misfiled_crossref_count:
        print(f"Redirected {misfiled_crossref_count} misfiled Crossref value(s) "
              f"(Par-shaped content) onto their own token's par_class.")
    if discarded_at_boundary:
        print(f"Warning: {discarded_at_boundary} 'vvv' row(s) discarded unresolved "
              f"at a verse/blank-row boundary — unexpected, worth investigating.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE,
                         help=f"Path to bsb_tables.tsv (default: {DEFAULT_SOURCE})")
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT,
                         help=f"Output SQLite path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()
    import_bsb_table(args.source, args.output)
