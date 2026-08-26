"""
import_lemma_table.py

Builds `strongs_lemma` in data/bsb_tables.db: one row per (Strong's number,
lang) giving the canonical dictionary-citation spelling and its
transliteration, derived from the Macula Hebrew (WLC) and Macula Greek
(SBLGNT) source files -- the same sources composer.py's alignment-path
composer reads live (see config.yaml's `sources` block), here processed
once up front instead of at render time.

Why precompute rather than transliterate live: multiple tokens can carry
the same Strong's number with slightly different lemma spellings (pointing
variance, data-entry drift in the source). Transliterating live would let
the same Strong's number surface with a different spelling in different
verses -- exactly wrong for something meant to read as "the" canonical
form for that number. Precomputing picks one spelling per number (the
most common one actually seen) so it reads the same everywhere it's shown.

No filtering by token class (article/preposition/conjunction/etc.):
an earlier version of this script skipped models.NON_STEM_CLASS
token classes on the assumption that bsb_tables.db's own `tokens` table
(built by utils/import_bsb_table.py from Bible Hub's BSB interlinear
export -- a different source from WLC/SBLGNT, and the one actually
joined against at render time) never assigns those a Strong's number of
their own. That's true for a fused prefix like the preposition in
בְּרֵאשִׁית ("in [the] beginning") -- Bible Hub's table gives that whole
word one strongs value, 7225, for the noun only -- but it's false for
other NON_STEM_CLASS-tagged tokens: the object-marker class ('om',
Hebrew's untranslated אֵת/אֶת) gets a real, non-placeholder number,
853, every time, confirmed in Bible Hub's own table -- so filtering by
class was silently discarding a legitimate, high-frequency number
(and plausibly others, e.g. 'rel' for the relative particle אֲשֶׁר,
H834). composer.py's own live-alignment loader (_load_source_index)
never filters by class either, for the same reason: class isn't what
decides whether a token carries a real number.

What actually distinguishes a real number from Macula's placeholder
pattern is the trailing letter, not the class: grammatical-morpheme
tokens are sometimes tagged with a pseudo-Strong's-number ('0871a',
'2050b') borrowed from an unrelated real dictionary entry rather than a
genuine sense-disambiguated homograph split (see composer.py's
_load_source_index() docstring for a worked example), and confirmed
against a real WLC run to reach real content-word tokens too (see
_collect_lemmas()'s docstring) -- so that's the one signal this script
actually filters on.

Strong's-number format: keyed as the bare digit string (no H/G/A prefix,
no leading zeros), to match bsb_tables.db's own tokens.strongs column
exactly -- that column is populated verbatim (just .strip()'d) from Bible
Hub's own "Str Heb"/"Str Grk" columns (see import_bsb_table.py's
COL_STR_HEB/COL_STR_GRK), NOT composer.py's alignment-path 'H0776'-style
normalization, which only applies to the non-default live-alignment
composer path. Aramaic tokens' Strong's numbers share the Hebrew number
space (Strong's own dictionary has no separate Aramaic numbering, per
composer.py's own '"H" if lang in ("H", "A")' convention), so Aramaic rows
in the Hebrew source file are folded into lang='H' here too -- one pass
over the whole file, no per-row lang check needed. Note that this table's
'H' also covers Aramaic tokens on the render/query side: a query joining
against a tokens table whose own language column distinguishes 'A' from
'H' needs to treat 'A' as 'H' when looking this table up, not compare
them literally -- there's no 'A' row here to match.

Usage:
    python utils/import_lemma_table.py [--hebrew-source FILE] [--greek-source FILE] [--db FILE]

Defaults assume the sibling-checkout layout config.yaml's data_root ("../")
also assumes: macula-hebrew and macula-greek cloned next to this repo (see
README.md's Macula Hebrew/Greek credits for the source repos). Requires
data/bsb_tables.db to already exist (build it first with
utils/import_bsb_table.py) -- this script only adds strongs_lemma to it,
it never touches the tokens/verses/books/chapters tables already there.
"""

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translit import make_transliterator   # noqa: E402

DEFAULT_HEBREW_SOURCE = ROOT.parent / "macula-hebrew" / "WLC" / "tsv" / "macula-hebrew.tsv"
DEFAULT_GREEK_SOURCE  = ROOT.parent / "macula-greek" / "SBLGNT" / "tsv" / "macula-greek-SBLGNT.tsv"
DEFAULT_DB            = ROOT / "data" / "bsb_tables.db"
CONFIG_PATH           = ROOT / "config.yaml"

# Leading zeros stripped, trailing letter captured separately (see
# _bare_strongs) so the caller can drop it rather than merge it into the
# bare number -- see module docstring on why trailing letter, not token
# class, is what actually flags Macula's placeholder numbers.
_STRONGS_RE = re.compile(r'^0*(\d+)([a-zA-Z]*)$')


def _bare_strongs(raw: str) -> tuple[str, bool]:
    """'0871a' -> ('871', True); '07225' -> ('7225', False); '' -> ('', False)."""
    raw = (raw or '').strip()
    if not raw:
        return '', False
    m = _STRONGS_RE.match(raw)
    if not m:
        return '', False
    return m.group(1), bool(m.group(2))


def _load_transliterate_config():
    """Mirror main.py's own transliterator setup so the lemma line matches
    whatever scheme the live pipeline is actually using -- see config.yaml's
    transliteration block."""
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    xlit_cfg = cfg.get('transliteration', {})
    return make_transliterator(
        hebrew_scheme=xlit_cfg.get('hebrew', 'brill_simple'),
        greek_scheme=xlit_cfg.get('greek', 'SIMPLE'),
        syllable_sep=xlit_cfg.get('syllable_sep'),
        stress_marker=xlit_cfg.get('stress_marker'),
    )


def _collect_lemmas(tsv_path: Path) -> tuple[dict, int]:
    """One pass over a Macula source TSV -> {strongs: Counter({lemma_text: count})}.
    No token-class filtering (see module docstring on why class isn't a
    reliable signal for this); the only thing excluded is a token whose
    Strong's number has no value at all, or survives with a trailing
    letter -- confirmed against a real WLC run that the letter-suffixed
    placeholder/borrowed-number pattern (see composer.py's
    _load_source_index() docstring) reaches real content-word tokens too:
    bare '2050' picked up 11,944 occurrences of 'הוא' plus a handful of
    totally unrelated words once rows carrying '2050<letter>' got
    stripped and merged in. Dropping them entirely (composer.py's own
    rule) loses those rows' lemma data rather than risk that kind of false
    collision. Hebrew prefers the Strong's-specific `stronglemma` column
    over the general `lemma` column when both are present (stronglemma is
    tied directly to the number a reader would look up); Greek's source
    has no stronglemma column at all, so `lemma` is used there. Returns
    the count of dropped-for-letter rows for the caller's diagnostic print.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    dropped_for_letter = 0
    with open(tsv_path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            strongs, had_letter = _bare_strongs(
                row.get('strongnumberx') or row.get('strong') or row.get('strongs') or ''
            )
            if had_letter:
                dropped_for_letter += 1
                continue
            if not strongs:
                continue
            lemma_text = (row.get('stronglemma') or row.get('lemma') or '').strip()
            if not lemma_text:
                continue
            counts[strongs][lemma_text] += 1
    return counts, dropped_for_letter


def build_lemma_table(hebrew_source: Path, greek_source: Path, db_path: Path,
                       transliterate=None) -> None:
    """Read both Macula sources, pick the most-common lemma spelling per
    Strong's number, transliterate it, and (re)write `strongs_lemma` into
    db_path. `transliterate` is injectable for testing; defaults to the
    live pipeline's own configured scheme (see _load_transliterate_config)."""
    if transliterate is None:
        transliterate = _load_transliterate_config()

    rows = []  # (strongs, lang, lemma, transliteration, variant_count)
    collisions = []  # (lang, strongs, {spelling: count})

    for source_path, lang, label in (
        (hebrew_source, 'H', 'Hebrew/Aramaic'),
        (greek_source, 'G', 'Greek'),
    ):
        if not source_path.exists():
            print(f"WARNING: {source_path} not found -- skipping {label} lemmas entirely.")
            continue
        counts, dropped_for_letter = _collect_lemmas(source_path)
        for strongs, spellings in counts.items():
            winner, _ = spellings.most_common(1)[0]
            variant_count = len(spellings)
            if variant_count > 1:
                collisions.append((lang, strongs, dict(spellings)))
            xlit = transliterate(winner, lang)
            rows.append((strongs, lang, winner, xlit, variant_count))
        print(f"  {label}: {len(counts):,} distinct Strong's number(s) from {source_path.name}")
        if dropped_for_letter:
            print(f"  NOTE: {dropped_for_letter:,} content-word row(s) in {source_path.name} carried "
                  f"a trailing-letter Strong's number and were dropped entirely (not merged into "
                  f"the bare number) -- see _collect_lemmas()'s docstring.")

    if not db_path.exists():
        print(f"WARNING: {db_path} does not exist yet -- creating it with only strongs_lemma "
              f"in it. Run utils/import_bsb_table.py first for a complete database.")
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS strongs_lemma")
    conn.execute("""
        CREATE TABLE strongs_lemma (
            strongs         TEXT NOT NULL,
            lang            TEXT NOT NULL CHECK(lang IN ('H','G')),
            lemma           TEXT NOT NULL,
            transliteration TEXT NOT NULL,
            variant_count   INTEGER NOT NULL,
            PRIMARY KEY (strongs, lang)
        )
    """)
    conn.executemany(
        "INSERT INTO strongs_lemma (strongs, lang, lemma, transliteration, variant_count) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    print(f"Wrote {len(rows):,} strongs_lemma row(s) to {db_path}")
    if collisions:
        print(f"WARNING: {len(collisions)} Strong's number(s) had more than one lemma spelling "
              f"in the source data (most-common spelling kept -- see variant_count column):")
        for lang, strongs, spellings in collisions[:10]:
            print(f"  {lang}{strongs}: {spellings}")
        if len(collisions) > 10:
            print(f"  ... and {len(collisions) - 10} more")


def explain_strongs(numbers: set, hebrew_source: Path, greek_source: Path) -> None:
    """Diagnostic for a coverage hole: for each bare Strong's number in
    `numbers`, scan both Macula sources and print every distinct raw
    (unstripped) value seen for it, with its class/lemma and how often.
    Distinguishes three cases a bare coverage-hole count can't tell apart:
    the number never appears in the source at all (a genuine cross-dataset
    gap, nothing this script can do about it); it always appears with the
    same single trailing letter (a real, unambiguous entry that
    _collect_lemmas() is currently dropping too cautiously -- worth
    reconsidering if this shows up a lot); or it appears with multiple
    different letters/senses (genuinely ambiguous, correctly dropped, see
    that function's docstring on the H2050 case).
    """
    for source_path, label in ((hebrew_source, 'Hebrew/Aramaic'), (greek_source, 'Greek')):
        print(f"--- {label} ({source_path.name}) ---")
        if not source_path.exists():
            print(f"  WARNING: not found -- skipping.")
            continue
        found: dict[str, Counter] = defaultdict(Counter)
        with open(source_path, encoding='utf-8', newline='') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for row in reader:
                raw = (row.get('strongnumberx') or row.get('strong') or row.get('strongs') or '').strip()
                if not raw:
                    continue
                bare, _ = _bare_strongs(raw)
                if bare in numbers:
                    lemma_text = (row.get('stronglemma') or row.get('lemma') or '').strip()
                    found[bare][(raw, row.get('class', ''), lemma_text)] += 1
        for number in sorted(numbers, key=int):
            variants = found.get(number)
            if not variants:
                print(f"  {number}: not found in this source at all")
                continue
            print(f"  {number}:")
            for (raw, cls, lemma_text), n in variants.most_common():
                print(f"    raw={raw!r} class={cls!r} lemma={lemma_text!r} count={n}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hebrew-source', type=Path, default=DEFAULT_HEBREW_SOURCE,
                         help=f"Path to macula-hebrew.tsv (default: {DEFAULT_HEBREW_SOURCE})")
    parser.add_argument('--greek-source', type=Path, default=DEFAULT_GREEK_SOURCE,
                         help=f"Path to macula-greek-SBLGNT.tsv (default: {DEFAULT_GREEK_SOURCE})")
    parser.add_argument('--db', type=Path, default=DEFAULT_DB,
                         help=f"bsb_tables.db path to add strongs_lemma to (default: {DEFAULT_DB})")
    parser.add_argument('--explain', type=str, default=None,
                         help="Comma-separated bare Strong's numbers (no H/G prefix) to diagnose "
                              "against the source files instead of building the table, e.g. "
                              "--explain 197,3887,6974")
    args = parser.parse_args()
    if args.explain:
        explain_strongs({n.strip() for n in args.explain.split(',') if n.strip()},
                         args.hebrew_source, args.greek_source)
    else:
        build_lemma_table(args.hebrew_source, args.greek_source, args.db)
