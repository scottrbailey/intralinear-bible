"""
import_lemma_table.py

Builds `strongs_lemma` in data/bsb_tables.db: one row per (Strong's number,
lang) giving the dictionary headword and its transliteration -- feeds the
beginner-tier intralinear module's transliteration-over-canonical-lemma
line (helps a reader recognize the stem underneath Hebrew's fused
prefixes/suffixes and vowel-pattern shifts).

Primary source: the public-domain Strong's Hebrew/Greek lexicons
(openscriptures/HebrewLexicon's HebrewStrong.xml, morphgnt/
strongs-dictionary-xml's strongsgreek.xml -- see data/ for the actual
files and README.md for citation). One authoritative entry per Strong's
number, so unlike deriving a lemma from a Bible text's own token
frequency, there's no corpus-frequency guessing or collision resolution
needed: every number the lexicon defines gets exactly one row.

Fallback for anything the lexicon doesn't cover (should be rare -- the
classic dictionaries are essentially complete): bsb_tables.db's own
`tokens` table, using the most common bare (no fused prefix/suffix)
occurrence Bible Hub's own data records for that number -- see
fill_gaps_from_bsb()'s docstring.

Headword transliteration is run through this project's own
translit.make_transliterator() rather than either lexicon's own provided
romanization (Hebrew's `xlit` attribute, Greek's `translit` attribute) --
deliberate, so the lemma line matches this project's own transliteration
scheme/style instead of picking up two more inconsistent romanizations.

Usage:
    python utils/import_lemma_table.py [--hebrew-lexicon FILE] [--greek-lexicon FILE] [--db FILE]

Requires data/bsb_tables.db to already exist (build it first with
utils/import_bsb_table.py) for the fallback tier to have anything to
check gaps against -- this script only adds/replaces strongs_lemma, it
never touches the tokens/verses/books/chapters tables already there.
"""

import argparse
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translit import make_transliterator   # noqa: E402

DEFAULT_HEBREW_LEXICON = ROOT / "data" / "HebrewStrong.xml"
DEFAULT_GREEK_LEXICON  = ROOT / "data" / "strongsgreek.xml"
DEFAULT_DB             = ROOT / "data" / "bsb_tables.db"
CONFIG_PATH            = ROOT / "config.yaml"


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


_HEBREW_ID_RE = re.compile(r'^[HA](\d+)$')


def _strip_ns(tag: str) -> str:
    """'{http://openscriptures.github.com/morphhb/namespace}entry' -> 'entry'."""
    return tag.rsplit('}', 1)[-1]


def _parse_hebrew_lexicon(path: Path) -> dict:
    """{bare Strong's digit string: headword text}, from openscriptures'
    HebrewStrong.xml. entry/@id is 'H6747'-style (H or A prefix; no
    Extended-Strong's lettered ids in this file, confirmed). The headword
    is the entry's own first <w> child's text -- specifically NOT any <w>
    nested inside <source> (a cross-reference to a root word, e.g.
    '<source>from <w src="H6743">6743</w>;</source>', not the entry's own
    headword) -- ElementTree's .find('w') only looks at direct children of
    <entry>, so it can't accidentally pick that up.

    The file declares a default XML namespace on its root <lexicon>
    element, so every real tag is actually '{http://...}entry'/'{http://
    ...}w', not the bare name -- confirmed against the real file (a
    hand-built sample without the xmlns declaration masked this during
    development). Namespaces are stripped from every element up front
    rather than qualifying every tag lookup, since nothing here needs to
    distinguish same-named elements from different namespaces.
    """
    root = ET.parse(path).getroot()
    for elem in root.iter():
        elem.tag = _strip_ns(elem.tag)

    lemmas = {}
    for entry in root.iter('entry'):
        m = _HEBREW_ID_RE.match(entry.get('id', ''))
        if not m:
            continue
        w = entry.find('w')
        headword = (w.text or '').strip() if w is not None else ''
        # A handful of headwords with an unusual two-vowel-on-one-consonant
        # spelling (H3389 Jerusalem's יְרוּשָׁלִַ͏ם, patach+hiriq both under
        # the same lamed) carry a U+034F COMBINING GRAPHEME JOINER to pin
        # the vowel order for renderers that would otherwise reorder them
        # -- purely typographic, no phonetic content, and nothing downstream
        # of this dict ever renders the pointed Hebrew for display, only
        # reads it to derive a transliteration. Stripped here, at the one
        # place this project pulls a headword out of this specific source
        # file, rather than taught to translit.py's general-purpose
        # transliterator (which has no reason to know this XML file's own
        # typographic conventions) or hand-edited into the vendored XML
        # itself (would silently diverge from upstream on a re-download).
        headword = headword.replace('͏', '')
        if headword:
            lemmas[m.group(1)] = headword
    return lemmas


def _parse_greek_lexicon(path: Path) -> dict:
    """{bare Strong's digit string: headword text}, from morphgnt's
    strongsgreek.xml. entry/@strongs is zero-padded ('00004', no G
    prefix). The headword is the entry's <greek unicode="..."/> child's
    'unicode' attribute -- that element is self-closing, no text content
    of its own (unlike the Hebrew lexicon's <w>text</w>).
    """
    lemmas = {}
    for entry in ET.parse(path).getroot().iter('entry'):
        raw = entry.get('strongs', '')
        if not raw.isdigit():
            continue
        greek = entry.find('greek')
        headword = (greek.get('unicode') or '').strip() if greek is not None else ''
        if headword:
            lemmas[str(int(raw))] = headword
    return lemmas


def build_lemma_table(hebrew_lexicon: Path, greek_lexicon: Path, db_path: Path,
                       transliterate=None) -> None:
    """Read both Strong's lexicons, transliterate each entry's headword,
    and (re)write `strongs_lemma` into db_path; then fall back to
    bsb_tables.db's own data (fill_gaps_from_bsb) for anything the
    lexicons didn't cover. `transliterate` is injectable for testing;
    defaults to the live pipeline's own configured scheme (see
    _load_transliterate_config)."""
    if transliterate is None:
        transliterate = _load_transliterate_config()

    rows = []  # (strongs, lang, lemma, transliteration)
    for lexicon_path, lang, label, parse in (
        (hebrew_lexicon, 'H', 'Hebrew', _parse_hebrew_lexicon),
        (greek_lexicon, 'G', 'Greek', _parse_greek_lexicon),
    ):
        if not lexicon_path.exists():
            print(f"WARNING: {lexicon_path} not found -- skipping {label} lemmas entirely.")
            continue
        lemmas = parse(lexicon_path)
        for strongs, headword in lemmas.items():
            rows.append((strongs, lang, headword, transliterate(headword, lang)))
        print(f"  {label}: {len(lemmas):,} entries from {lexicon_path.name}")

    if not db_path.exists():
        print(f"WARNING: {db_path} does not exist yet -- creating it with only strongs_lemma "
              f"in it. Run utils/import_bsb_table.py first for a complete database.")
    conn = sqlite3.connect(db_path)

    # find_text/replace_text are hand-curated by utils/build_restored_names.py,
    # not derived from either lexicon -- this function drops and fully rebuilds
    # strongs_lemma on every run (to pick up a refreshed lexicon file), so
    # without this save/restore step a rerun would silently wipe out every
    # curated override. Carried over by (strongs, lang), the table's own key.
    preserved = {}
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strongs_lemma'"
    ).fetchone():
        preserved = {
            (r[0], r[1]): (r[2], r[3])
            for r in conn.execute(
                "SELECT strongs, lang, find_text, replace_text FROM strongs_lemma "
                "WHERE find_text IS NOT NULL OR replace_text IS NOT NULL"
            ).fetchall()
        }

    conn.execute("DROP TABLE IF EXISTS strongs_lemma")
    conn.execute("""
        CREATE TABLE strongs_lemma (
            strongs         TEXT NOT NULL,
            lang            TEXT NOT NULL CHECK(lang IN ('H','G')),
            lemma           TEXT NOT NULL,
            transliteration TEXT NOT NULL,
            source          TEXT NOT NULL DEFAULT 'lexicon' CHECK(source IN ('lexicon','bsb')),
            find_text       TEXT,   -- restored-names override, see build_restored_names.py
            replace_text    TEXT,
            PRIMARY KEY (strongs, lang)
        )
    """)
    conn.executemany(
        "INSERT INTO strongs_lemma (strongs, lang, lemma, transliteration, source) "
        "VALUES (?, ?, ?, ?, 'lexicon')",
        rows,
    )
    conn.commit()
    print(f"Wrote {len(rows):,} strongs_lemma row(s) to {db_path}")

    fill_gaps_from_bsb(conn)

    restored = 0
    orphaned = 0
    for (strongs, lang), (find_text, replace_text) in preserved.items():
        cur = conn.execute(
            "UPDATE strongs_lemma SET find_text = ?, replace_text = ? "
            "WHERE strongs = ? AND lang = ?",
            (find_text, replace_text, strongs, lang),
        )
        if cur.rowcount:
            restored += 1
        else:
            orphaned += 1
    conn.commit()
    if preserved:
        print(f"Restored {restored:,} curated find_text/replace_text override(s) "
              f"across the rebuild" + (f"; {orphaned:,} referenced a (strongs, lang) "
              f"no longer in the rebuilt table -- lexicon file changed?" if orphaned else "."))

    conn.close()


def fill_gaps_from_bsb(conn: sqlite3.Connection) -> None:
    """Fallback tier for coverage holes: a (strongs, lang) that bsb_tables.db's
    own `tokens` table uses but the lexicon-derived build above has no entry
    for. No cross-referencing needed: a token whose parsing_short has no
    '|' (a single, unprefixed and unsuffixed morpheme -- confirmed against
    real data that both a leading prefix group, 'Conj-w | N-ms', and a
    trailing suffix group, 'N-msc | 3ms', use the same '|' separator) has
    its bare word as source_text already, with Bible Hub's own translit
    column giving that exact word's transliteration -- so the most common
    such (source_text, translit) pair recorded under a given number is
    already a usable lemma/transliteration pair. Rows land with
    source='bsb' rather than 'lexicon' so provenance stays visible. A
    no-op if `tokens` doesn't exist yet in this database (fresh db, no BSB
    import run yet) -- nothing to check gaps against.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tokens'"
    ).fetchone():
        print("NOTE: no 'tokens' table in this database yet (run utils/import_bsb_table.py "
              "first) -- skipping the bsb_tables.db fallback fill.")
        return

    conn.row_factory = sqlite3.Row
    holes = conn.execute("""
        SELECT DISTINCT t.strongs AS strongs,
               CASE WHEN t.language = 'A' THEN 'H' ELSE t.language END AS lang
        FROM tokens t
        WHERE t.strongs IS NOT NULL AND t.strongs != ''
          AND NOT EXISTS (
              SELECT 1 FROM strongs_lemma sl
              WHERE sl.strongs = t.strongs
                AND sl.lang = CASE WHEN t.language = 'A' THEN 'H' ELSE t.language END
          )
    """).fetchall()

    filled = 0
    for hole in holes:
        strongs, lang = hole['strongs'], hole['lang']
        best = conn.execute("""
            SELECT source_text, translit, COUNT(*) AS n
            FROM tokens
            WHERE strongs = ?
              AND (language = ? OR (? = 'H' AND language = 'A'))
              AND (parsing_short IS NULL OR parsing_short NOT LIKE '%|%')
            GROUP BY source_text, translit
            ORDER BY n DESC
            LIMIT 1
        """, (strongs, lang, lang)).fetchone()
        if best is None or not best['source_text']:
            continue
        conn.execute(
            "INSERT INTO strongs_lemma (strongs, lang, lemma, transliteration, source) "
            "VALUES (?, ?, ?, ?, 'bsb')",
            (strongs, lang, best['source_text'], best['translit'] or ''),
        )
        filled += 1
    conn.commit()

    print(f"Filled {filled:,} of {len(holes):,} coverage hole(s) from bsb_tables.db's own "
          f"unprefixed/unsuffixed occurrences (source='bsb'); {len(holes) - filled:,} had no "
          f"such occurrence of that number to fall back to.")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hebrew-lexicon', type=Path, default=DEFAULT_HEBREW_LEXICON,
                         help=f"Path to HebrewStrong.xml (default: {DEFAULT_HEBREW_LEXICON})")
    parser.add_argument('--greek-lexicon', type=Path, default=DEFAULT_GREEK_LEXICON,
                         help=f"Path to strongsgreek.xml (default: {DEFAULT_GREEK_LEXICON})")
    parser.add_argument('--db', type=Path, default=DEFAULT_DB,
                         help=f"bsb_tables.db path to add strongs_lemma to (default: {DEFAULT_DB})")
    args = parser.parse_args()
    build_lemma_table(args.hebrew_lexicon, args.greek_lexicon, args.db)
