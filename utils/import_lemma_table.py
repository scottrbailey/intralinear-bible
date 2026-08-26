"""
import_lemma_table.py

Builds `strongs_lemma` in data/bsb_tables.db: one row per (Strong's number,
language) giving the canonical dictionary-citation spelling and its
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

Grammatical-morpheme tokens (article/preposition/conjunction/object
marker/particle/relative -- see models.NON_STEM_CLASS) are skipped
entirely: bsb_tables.db's own `tokens` table (built by
utils/import_bsb_table.py from Bible Hub's BSB interlinear export -- a
different source from WLC/SBLGNT, and the one actually joined against at
render time) never assigns those a Strong's number of their own. A fused
word like בְּרֵאשִׁית ("in [the] beginning") carries one strongs value,
7225 (for the noun), with no separate number for the prefixed
preposition -- so a lemma row for a NON_STEM_CLASS token would be dead
weight nothing will ever join against. It also sidesteps real messiness
in the Macula data itself: those same grammatical-morpheme tokens are
sometimes tagged with a trailing-letter pseudo-Strong's-number ('0871a',
'2050b') borrowed from an unrelated real dictionary entry rather than a
genuine sense-disambiguated homograph split (see composer.py's
_load_source_index() docstring for a worked example) -- content words
don't have this problem in practice, so filtering by class sidesteps it
rather than needing to replicate that suppression logic here.

Strong's-number format: keyed as the bare digit string (no H/G/A prefix,
no leading zeros), to match bsb_tables.db's own tokens.strongs column
exactly -- that column is populated verbatim (just .strip()'d) from Bible
Hub's own "Str Heb"/"Str Grk" columns (see import_bsb_table.py's
COL_STR_HEB/COL_STR_GRK), NOT composer.py's alignment-path 'H0776'-style
normalization, which only applies to the non-default live-alignment
composer path. Aramaic tokens' Strong's numbers share the Hebrew number
space (Strong's own dictionary has no separate Aramaic numbering, per
composer.py's own '"H" if lang in ("H", "A")' convention), so Aramaic rows
in the Hebrew source file are folded into language='H' here too -- one
pass over the whole file, no per-row language check needed.

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

from models import NON_STEM_CLASS          # noqa: E402
from translit import make_transliterator   # noqa: E402

DEFAULT_HEBREW_SOURCE = ROOT.parent / "macula-hebrew" / "WLC" / "tsv" / "macula-hebrew.tsv"
DEFAULT_GREEK_SOURCE  = ROOT.parent / "macula-greek" / "SBLGNT" / "tsv" / "macula-greek-SBLGNT.tsv"
DEFAULT_DB            = ROOT / "data" / "bsb_tables.db"
CONFIG_PATH           = ROOT / "config.yaml"

# Leading zeros stripped, trailing letter captured separately (see
# _bare_strongs) rather than discarded silently -- NON_STEM_CLASS
# filtering keeps content-word rows from ever needing that suppression,
# but this still flags any survivor for a manual look at real data.
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
    """One pass over a Macula source TSV -> {strongs: Counter({lemma_text: count})},
    restricted to genuine stem/content-word tokens (see module docstring).
    Hebrew prefers the Strong's-specific `stronglemma` column over the
    general `lemma` column when both are present (stronglemma is tied
    directly to the number a reader would look up); Greek's source has no
    stronglemma column at all, so `lemma` is used there. Also returns the
    count of content-word rows whose Strong's number survived with a
    trailing letter, for the caller's diagnostic print.
    """
    counts: dict[str, Counter] = defaultdict(Counter)
    letter_survivors = 0
    with open(tsv_path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            if row.get('class', '') in NON_STEM_CLASS:
                continue
            strongs, had_letter = _bare_strongs(
                row.get('strongnumberx') or row.get('strong') or row.get('strongs') or ''
            )
            if not strongs:
                continue
            if had_letter:
                letter_survivors += 1
            lemma_text = (row.get('stronglemma') or row.get('lemma') or '').strip()
            if not lemma_text:
                continue
            counts[strongs][lemma_text] += 1
    return counts, letter_survivors


def build_lemma_table(hebrew_source: Path, greek_source: Path, db_path: Path,
                       transliterate=None) -> None:
    """Read both Macula sources, pick the most-common lemma spelling per
    Strong's number, transliterate it, and (re)write `strongs_lemma` into
    db_path. `transliterate` is injectable for testing; defaults to the
    live pipeline's own configured scheme (see _load_transliterate_config)."""
    if transliterate is None:
        transliterate = _load_transliterate_config()

    rows = []  # (strongs, language, lemma, transliteration, variant_count)
    collisions = []  # (language, strongs, {spelling: count})

    for source_path, language, label in (
        (hebrew_source, 'H', 'Hebrew/Aramaic'),
        (greek_source, 'G', 'Greek'),
    ):
        if not source_path.exists():
            print(f"WARNING: {source_path} not found -- skipping {label} lemmas entirely.")
            continue
        counts, letter_survivors = _collect_lemmas(source_path)
        for strongs, spellings in counts.items():
            winner, _ = spellings.most_common(1)[0]
            variant_count = len(spellings)
            if variant_count > 1:
                collisions.append((language, strongs, dict(spellings)))
            xlit = transliterate(winner, language)
            rows.append((strongs, language, winner, xlit, variant_count))
        print(f"  {label}: {len(counts):,} distinct Strong's number(s) from {source_path.name}")
        if letter_survivors:
            print(f"  NOTE: {letter_survivors} content-word row(s) in {source_path.name} carried "
                  f"a trailing-letter Strong's number (kept, digits only) -- worth a manual look.")

    if not db_path.exists():
        print(f"WARNING: {db_path} does not exist yet -- creating it with only strongs_lemma "
              f"in it. Run utils/import_bsb_table.py first for a complete database.")
    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE IF EXISTS strongs_lemma")
    conn.execute("""
        CREATE TABLE strongs_lemma (
            strongs         TEXT NOT NULL,
            language        TEXT NOT NULL CHECK(language IN ('H','G')),
            lemma           TEXT NOT NULL,
            transliteration TEXT NOT NULL,
            variant_count   INTEGER NOT NULL,
            PRIMARY KEY (strongs, language)
        )
    """)
    conn.executemany(
        "INSERT INTO strongs_lemma (strongs, language, lemma, transliteration, variant_count) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    print(f"Wrote {len(rows):,} strongs_lemma row(s) to {db_path}")
    if collisions:
        print(f"WARNING: {len(collisions)} Strong's number(s) had more than one lemma spelling "
              f"in the source data (most-common spelling kept -- see variant_count column):")
        for language, strongs, spellings in collisions[:10]:
            print(f"  {language}{strongs}: {spellings}")
        if len(collisions) > 10:
            print(f"  ... and {len(collisions) - 10} more")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hebrew-source', type=Path, default=DEFAULT_HEBREW_SOURCE,
                         help=f"Path to macula-hebrew.tsv (default: {DEFAULT_HEBREW_SOURCE})")
    parser.add_argument('--greek-source', type=Path, default=DEFAULT_GREEK_SOURCE,
                         help=f"Path to macula-greek-SBLGNT.tsv (default: {DEFAULT_GREEK_SOURCE})")
    parser.add_argument('--db', type=Path, default=DEFAULT_DB,
                         help=f"bsb_tables.db path to add strongs_lemma to (default: {DEFAULT_DB})")
    args = parser.parse_args()
    build_lemma_table(args.hebrew_source, args.greek_source, args.db)
