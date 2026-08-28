"""
build_restored_names.py

Populates `strongs_lemma.find_text`/`replace_text` for proper nouns (the
control surface a human reviews and hand-corrects) and, from those,
`tokens.english_restored` (disposable, regenerated-on-every-run output) --
see docs/DEVELOPMENT.md's restored-names section for the design behind the
split. Meant to run after both utils/import_bsb_table.py and
utils/import_lemma_table.py, since it needs `tokens` for its find_text
bootstrap and `strongs_lemma` for its transliteration column.

Three passes, each additive (never overwrites a value someone already
hand-curated into strongs_lemma):

1. replace_text, mechanical -- for every Hebrew/Aramaic Strong's number
   HebrewStrong.xml itself tags as a proper noun (pos contains "n-pr"),
   capitalize strongs_lemma's own transliteration and strip this project's
   configured syllable/stress marks. For Greek Strong's numbers that
   strongsgreek.xml's own <strongs_derivation> cites as transliterating a
   Hebrew proper noun (<strongsref language="HEBREW" strongs="…"/> -- e.g.
   G3475 Mōÿsēs citing H4872), inherit that Hebrew name's replace_text
   instead of transliterating the Greek independently, so "Moses" reads the
   same restored way in both Testaments.

2. find_text, bootstrapped -- the most common tokens.english value for that
   Strong's number, for every name replace_text now covers. This is a
   first-pass guess, not a citation: review the generated CSV before
   trusting it, especially for names sparse enough that one odd verse's
   wording could win the vote.

3. The divine name (H3068) is seeded directly rather than bootstrapped --
   see _SEED_FIND_REPLACE's comment for why a plain "most common value"
   vote is the wrong mental model for it specifically.

Then: add tokens.english_restored if missing, apply every strongs_lemma
find_text/replace_text pair as a per-token substring swap (scoped by that
token's own strongs, so it can never cross-contaminate a different name),
strip a leading "the"/"The"/"THE" immediately before the restored name
(Hebrew proper nouns take no definite article -- "the Yehovah" is never
correct), and write a review CSV of every distinct old->new change.

Usage:
    python utils/build_restored_names.py [--db FILE] [--hebrew-lexicon FILE]
                                          [--greek-lexicon FILE] [--review-csv FILE]
"""

import argparse
import csv
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from translit import SCHEMES  # noqa: E402
from import_lemma_table import _strip_ns, _HEBREW_ID_RE  # noqa: E402

DEFAULT_HEBREW_LEXICON = ROOT / "data" / "HebrewStrong.xml"
DEFAULT_GREEK_LEXICON  = ROOT / "data" / "strongsgreek.xml"
DEFAULT_DB             = ROOT / "data" / "bsb_tables.db"
DEFAULT_REVIEW_CSV     = ROOT / "data" / "restored_names_review.csv"
CONFIG_PATH            = ROOT / "config.yaml"

_PROPER_NOUN_POS_RE = re.compile(r'\bn-pr\b')


def _parse_hebrew_proper_nouns(path: Path) -> set[str]:
    """{bare Strong's digit string}, for every HebrewStrong.xml entry whose
    headword <w pos="..."> contains an 'n-pr' component (n-pr-m, n-pr-f,
    n-pr-loc, or a combined tag like 'n-pr-m n-pr-loc') -- Strong's own
    proper-noun tagging, not guessed from the gloss text."""
    root = ET.parse(path).getroot()
    for elem in root.iter():
        elem.tag = _strip_ns(elem.tag)

    strongs_ids = set()
    for entry in root.iter('entry'):
        m = _HEBREW_ID_RE.match(entry.get('id', ''))
        if not m:
            continue
        w = entry.find('w')
        pos = (w.get('pos') if w is not None else '') or ''
        if _PROPER_NOUN_POS_RE.search(pos):
            strongs_ids.add(m.group(1))
    return strongs_ids


def _first_strongsref(entry: ET.Element, language: str) -> str | None:
    xref = entry.find(f'.//strongsref[@language="{language}"]')
    strongs = xref.get('strongs') if xref is not None else None
    return str(int(strongs)) if strongs and strongs.isdigit() else None


def _parse_greek_hebrew_origin(path: Path) -> dict:
    """{Greek bare strongs digit string: Hebrew bare strongs digit string},
    for every strongsgreek.xml entry whose <strongs_derivation> cites a
    Hebrew Strong's number, directly or by chasing a chain of GREEK
    cross-references -- Strong's own etymology, not something inferred from
    spelling. G2385 James ("the same as G2384 Grcized") has no direct
    HEBREW strongsref of its own; G2384 Jacob does (H3290) -- without
    following that chain James (and 456 other entries like it, most of
    them one hop, a few up to six) silently got no restored form at all,
    with nothing in the review CSV to say why, since it's not a "found but
    didn't match" case, it's a "never even got a rule" case.

    Guards against a cycle with `seen`; caps at 10 hops as a sanity bound
    (real chains top out at 6) rather than trusting the data to be
    well-formed.

    Covers Hebrew-origin common words too (e.g. hallelujah, amen), not just
    names; the caller filters to Hebrew numbers _parse_hebrew_proper_nouns
    also tagged as proper nouns."""
    entries = {e.get('strongs'): e for e in ET.parse(path).getroot().iter('entry')
               if (e.get('strongs') or '').isdigit()}

    result = {}
    for raw, entry in entries.items():
        greek_strongs = str(int(raw))
        seen = {raw}
        current = entry
        for _ in range(10):
            hebrew_strongs = _first_strongsref(current, 'HEBREW')
            if hebrew_strongs:
                result[greek_strongs] = hebrew_strongs
                break
            next_raw_int = _first_strongsref(current, 'GREEK')
            next_raw = next_raw_int.zfill(5) if next_raw_int else None
            if not next_raw or next_raw in seen or next_raw not in entries:
                break
            seen.add(next_raw)
            current = entries[next_raw]
    return result


def _load_syllable_chars() -> tuple[str, str]:
    """Effective (syllable_sep, stress_marker) for the live pipeline's
    configured Hebrew scheme -- the same override-over-bundled-default
    resolution translit.make_transliterator() uses internally (see its own
    docstring), needed here only so replace_text derivation can strip these
    back out of strongs_lemma.transliteration."""
    with open(CONFIG_PATH, encoding='utf-8') as f:
        cfg = yaml.safe_load(f)
    xlit_cfg = cfg.get('transliteration', {})
    bundled = SCHEMES.get(xlit_cfg.get('hebrew', 'brill_simple'), {})
    sep    = xlit_cfg.get('syllable_sep')
    sep    = bundled.get('syllable_sep', '') if sep is None else sep
    stress = xlit_cfg.get('stress_marker')
    stress = bundled.get('stress_marker', '') if stress is None else stress
    return sep or '', stress or ''


def _capitalize_name(transliteration: str, sep: str, stress: str) -> str:
    """'mo·sheh' -> 'Mosheh', 'beer sheva' -> 'Beer Sheva': strip this
    scheme's syllable/stress marks and title-case what's left, so a
    multi-word name doesn't come out with only its first word capitalized.

    str.title() treats any uncased character as a word break, which is
    exactly right for a *leading* aleph/ayin glyph (it capitalizes the
    letter right after it, same as wanted) but would over-capitalize a
    *mid-word* one under a scheme that keeps aleph/ayin as ʼ/ʻ instead of
    dropping them (sbl_academic; not brill_simple, this project's current
    default) -- e.g. 'yisrāʼēl' -> 'YisrāʼĒl'. Revisit if restored names
    ever moves to one of those schemes.
    """
    text = transliteration
    if sep:
        text = text.replace(sep, '')
    if stress:
        text = text.replace(stress, '')
    return text.title()


# A modal tokens.english value is usually the bare name ("Moses"), but not
# always -- "of Jesus"/"to Jesus" are just as common a shape for a
# frequently-mentioned person, and blindly using the whole phrase as
# find_text either eats the preposition on the occurrences that happen to
# match it or silently fails to match every other preposition's spelling.
# Pulling out the longest capitalized run instead ("of Jesus" -> "Jesus")
# sidesteps both: a real regression caught by exactly the review CSV this
# script exists to produce, on the very first test run against real data.
#
# A hyphen continuation allows a lowercase start ('-jearim'): BSB's own
# convention lowercases the second half of a hyphenated compound place name
# ('Beth-shemesh', 'Kiriath-jearim' -- both single Strong's numbers, single
# Hebrew headwords, confirmed against HebrewStrong.xml), so without the
# hyphen exception this only captured 'Kiriath', leaving '-jearim'
# unmatched and the compound half-restored.
#
# Deliberately NOT a space continuation ('Beer Sheva' as one match spanning
# two capitalized words): a rare name's modal value is often its only
# occurrence, and if that occurrence happens to open a sentence, the
# capitalized sentence-starter reads identically to a second word of the
# same name ('And Aaron', 'Although Job') -- confirmed against real output.
# No case in this corpus's actual English source text needed multi-word
# space-joining to begin with (a two-word restored *replace_text*, e.g.
# 'Beer Sheva', comes from _capitalize_name()'s own title-casing of the
# transliteration, a separate mechanism unaffected by this).
_CAPITALIZED_WORD_RE = re.compile(r"[A-Z]+[a-z’']*(?:-[a-z’']+)*")

# Closed-class English words that are only capitalized here because BSB
# happened to open a sentence with them, not because they're the name --
# excluded outright rather than relying on "longest wins" (that heuristic
# alone still loses to a short real name, e.g. 'Although' beats 'Job' on
# raw length). Not exhaustive -- can't be, English's closed classes are
# large -- but covers what's actually shown up leading a modal value in
# this corpus; extend if the review CSV turns up another one.
_SENTENCE_STARTER_STOPWORDS = {
    'after', 'again', 'also', 'although', 'and', 'anyone', 'as', 'at',
    'because', 'before', 'belonging', 'but', 'by', 'can', 'concerning',
    'did', 'does', 'finally', 'for', 'from', 'furthermore', 'he', 'her',
    'here', 'him', 'his', 'however', 'i', 'if', 'in', 'indeed', 'instead',
    'is', 'it', 'its', 'let', 'may', 'meanwhile', 'moreover',
    'nevertheless', 'not', 'now', 'of', 'on', 'or', 'rather', 'she',
    'since', 'so', 'some', 'still', 'surely', 'that', 'then', 'there',
    'therefore', 'these', 'they', 'this', 'those', 'though', 'thus', 'to',
    'unless', 'was', 'we', 'were', 'what', 'when', 'where', 'while', 'who',
    'will', 'with', 'would', 'yet', 'you',
}


def _most_common_english(conn: sqlite3.Connection, strongs: str, lang: str):
    row = conn.execute("""
        SELECT english, COUNT(*) AS n
        FROM tokens
        WHERE strongs = ?
          AND (language = ? OR (? = 'H' AND language = 'A'))
          AND english IS NOT NULL
        GROUP BY english
        ORDER BY n DESC
        LIMIT 1
    """, (strongs, lang, lang)).fetchone()
    if row is None:
        return None
    words = [w for w in _CAPITALIZED_WORD_RE.findall(row[0])
             if w.split('-', 1)[0].lower() not in _SENTENCE_STARTER_STOPWORDS]
    return max(words, key=len) if words else None


# The divine name is seeded directly rather than bootstrapped from "most
# common tokens.english value": that vote picks one *exact* string, but
# YHWH's BSB rendering has ~250 distinct surface strings ("the LORD", "But
# the LORD", "LORD's", "O LORD", ...) all sharing one core substring,
# "LORD" -- exactly what find_text is supposed to capture. A plain
# substring swap on "LORD" -> "Yehovah" handles all of them in one rule
# *and* leaves the pronoun-glossed occurrences (He/Him/His) untouched for
# free, since those strings never contained "LORD" to begin with -- not a
# special case, just how substring matching already behaves.
#
# Deliberately NOT included: the ~9 occurrences where YHWH is glossed bare
# "GOD" (the "Lord GOD" Adonai+YHWH combination, Adonai's own H136 token
# supplying "Lord" separately) -- restoring those changes wording the BSB
# translators chose deliberately, a bigger step than fixing "THE LORD"'s
# stray capitalization was. Left alone by default; revisit once the review
# CSV shows how those 9 actually read in context.
#
# Applied unconditionally on every run (not COALESCE'd like everything
# else) -- this dict *is* the curated override for these entries, the same
# role strongs_lemma's find_text/replace_text columns play for every other
# name, so it always wins over whatever the mechanical/bootstrap passes
# below would otherwise compute. To change the divine name's spelling,
# edit this constant, not the strongs_lemma row directly -- a hand edit to
# the row would just get overwritten the next time this script runs.
_SEED_FIND_REPLACE = {
    ('3068', 'H'): ('LORD', 'Yehovah'),
}

_FIND_TEXT_RE_CACHE: dict[str, re.Pattern] = {}


def _find_text_pattern(find_text: str) -> re.Pattern:
    """Word-boundary-wrapped, cached per find_text -- a plain substring
    check/replace would also fire inside a longer word that happens to
    contain it (e.g. H3478 Israel's find_text 'Israel' matching inside
    'Israelites', mid-word-mangling it into 'Yisraelites'/'Yisrael
    (Israel)ites'). \b on a hyphen-joined compound's *first* half (e.g. a
    stray 'Kiriath' find_text) still matches, since regex treats '-' as a
    non-word boundary same as a space -- that half is meant to be fixed by
    widening find_text itself (see _CAPITALIZED_WORD_RE), not by this."""
    pattern = _FIND_TEXT_RE_CACHE.get(find_text)
    if pattern is None:
        pattern = re.compile(r'\b' + re.escape(find_text) + r'\b')
        _FIND_TEXT_RE_CACHE[find_text] = pattern
    return pattern


_ARTICLE_RE_CACHE: dict[str, re.Pattern] = {}


def _strip_leading_article(text: str, name: str) -> str:
    """Hebrew proper nouns take no definite article, so a leading
    'the'/'The'/'THE' immediately before a just-restored name is BSB's own
    article for the common-noun-shaped title the word no longer is once
    restored (e.g. 'the LORD' -> 'the Yehovah' is never right) -- stripped
    for every restored name, not just the divine one, since the same
    grammar applies to all of them."""
    pattern = _ARTICLE_RE_CACHE.get(name)
    if pattern is None:
        pattern = re.compile(r'\b(?:the|The|THE) ' + re.escape(name) + r'\b')
        _ARTICLE_RE_CACHE[name] = pattern
    return pattern.sub(name, text)


def populate_strongs_lemma(conn: sqlite3.Connection, hebrew_lexicon: Path,
                            greek_lexicon: Path) -> None:
    sep, stress = _load_syllable_chars()

    hebrew_proper = _parse_hebrew_proper_nouns(hebrew_lexicon) if hebrew_lexicon.exists() else set()
    if not hebrew_proper:
        print(f"WARNING: no proper nouns found in {hebrew_lexicon} -- skipping Hebrew pass.")
    greek_hebrew_origin = _parse_greek_hebrew_origin(greek_lexicon) if greek_lexicon.exists() else {}

    # Seeded entries (the divine name) are applied first and always win --
    # see _SEED_FIND_REPLACE's comment -- and excluded from the mechanical
    # pass below so it never even computes a value for them: H3068 is
    # itself tagged n-pr in HebrewStrong.xml, and strongs_lemma.transliteration
    # for it isn't a normal letter-by-letter transliteration but
    # translit.py's own bundled divine_name string ('Yᵉ·hó·vah', with a
    # precomposed accented 'ó' that this function's generic stress-marker
    # strip can't see) -- feeding that through _capitalize_name would race
    # the seed below and silently win if applied in the other order.
    seeded_strongs = {strongs for strongs, _lang in _SEED_FIND_REPLACE}
    for (strongs, lang), (find_text, replace_text) in _SEED_FIND_REPLACE.items():
        conn.execute(
            "UPDATE strongs_lemma SET replace_text = ?, find_text = ? WHERE strongs = ? AND lang = ?",
            (replace_text, find_text, strongs, lang)
        )
    conn.commit()

    filled_replace = 0
    for strongs in hebrew_proper - seeded_strongs:
        row = conn.execute(
            "SELECT transliteration, replace_text FROM strongs_lemma WHERE strongs = ? AND lang = 'H'",
            (strongs,)
        ).fetchone()
        if row is None or row[1] is not None:
            continue  # not in strongs_lemma (lexicon-only overlap gap), or already curated
        conn.execute(
            "UPDATE strongs_lemma SET replace_text = ? WHERE strongs = ? AND lang = 'H'",
            (_capitalize_name(row[0], sep, stress), strongs)
        )
        filled_replace += 1

    filled_greek = 0
    for greek_strongs, hebrew_strongs in greek_hebrew_origin.items():
        if hebrew_strongs not in hebrew_proper:
            continue  # Hebrew-origin common word, not a name (amen, hallelujah, ...)
        hebrew_row = conn.execute(
            "SELECT replace_text FROM strongs_lemma WHERE strongs = ? AND lang = 'H'",
            (hebrew_strongs,)
        ).fetchone()
        if hebrew_row is None or hebrew_row[0] is None:
            continue
        greek_row = conn.execute(
            "SELECT replace_text FROM strongs_lemma WHERE strongs = ? AND lang = 'G'",
            (greek_strongs,)
        ).fetchone()
        if greek_row is None or greek_row[0] is not None:
            continue  # not in strongs_lemma, or already curated
        conn.execute(
            "UPDATE strongs_lemma SET replace_text = ? WHERE strongs = ? AND lang = 'G'",
            (hebrew_row[0], greek_strongs)
        )
        filled_greek += 1
    conn.commit()
    print(f"replace_text: derived {filled_replace:,} Hebrew/Aramaic proper noun(s), "
          f"inherited {filled_greek:,} Greek name(s) of Hebrew origin from their Hebrew form.")

    filled_find = 0
    candidates = conn.execute(
        "SELECT strongs, lang FROM strongs_lemma WHERE replace_text IS NOT NULL AND find_text IS NULL"
    ).fetchall()
    for strongs, lang in candidates:
        guess = _most_common_english(conn, strongs, lang)
        if guess:
            conn.execute(
                "UPDATE strongs_lemma SET find_text = ? WHERE strongs = ? AND lang = ?",
                (guess, strongs, lang)
            )
            filled_find += 1
    conn.commit()
    print(f"find_text: bootstrapped {filled_find:,} of {len(candidates):,} name(s) from "
          f"tokens.english's own most common rendering for that Strong's number -- a first "
          f"guess, review data/restored_names_review.csv before trusting it.")


def add_restored_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tokens)")}
    if 'english_restored' not in cols:
        conn.execute("ALTER TABLE tokens ADD COLUMN english_restored TEXT")
        conn.commit()


def apply_restorations(conn: sqlite3.Connection, annotate: bool = False) -> None:
    """Full rebuild each run: clear english_restored, then re-derive it from
    the current strongs_lemma find_text/replace_text so a hand-edit there is
    always reflected after a rerun, never stuck from a stale prior pass.

    annotate=True renders 'Yehovah (LORD)' instead of bare 'Yehovah' --
    restored Hebrew/Greek names are frequently transliterations that read
    like plausible English words on their own (Yochanan, Mosheh, ...), so
    scanning a compiled module for what got caught vs. missed is otherwise
    slow going. Debugging aid, not meant to ship in a real build -- see
    --annotate."""
    conn.execute("UPDATE tokens SET english_restored = NULL")

    rules_by_strongs: dict[str, list[tuple[str, str, str]]] = {}
    for strongs, lang, find_text, replace_text in conn.execute("""
        SELECT strongs, lang, find_text, replace_text FROM strongs_lemma
        WHERE find_text IS NOT NULL AND replace_text IS NOT NULL
    """):
        rules_by_strongs.setdefault(strongs, []).append((lang, find_text, replace_text))

    changes = []
    total_candidates = 0
    for bsb_sort, language, strongs, english in conn.execute("""
        SELECT bsb_sort, language, strongs, english FROM tokens
        WHERE strongs IS NOT NULL AND english IS NOT NULL
    """):
        rules = rules_by_strongs.get(strongs)
        if not rules:
            continue
        lang_key = 'H' if language in ('H', 'A') else 'G'
        for lang, find_text, replace_text in rules:
            if lang != lang_key:
                continue
            total_candidates += 1
            pattern = _find_text_pattern(find_text)
            if not pattern.search(english):
                break  # token exists for this name but this occurrence didn't use find_text's wording
            display = f'{replace_text} ({find_text})' if annotate else replace_text
            new_english = pattern.sub(lambda m: display, english)
            # Still stripped against the bare replace_text -- the pattern's
            # trailing \b matches on the space before "(find_text)" just as
            # well as on a word boundary, and the substitution only touches
            # "the <name>", leaving the trailing annotation untouched.
            new_english = _strip_leading_article(new_english, replace_text)
            if new_english != english:
                changes.append((new_english, bsb_sort))
            break

    conn.executemany("UPDATE tokens SET english_restored = ? WHERE bsb_sort = ?", changes)
    conn.commit()
    print(f"english_restored: set on {len(changes):,} of {total_candidates:,} token(s) tagged "
          f"with a curated Strong's number ({total_candidates - len(changes):,} didn't contain "
          f"that name's find_text in their own wording -- expected for pronoun-glossed "
          f"occurrences, worth a look otherwise).")


def write_review_csv(conn: sqlite3.Connection, out_path: Path) -> None:
    rows = conn.execute("""
        SELECT english, english_restored, COUNT(*) AS cnt
        FROM tokens
        WHERE english_restored IS NOT NULL
        GROUP BY english, english_restored
        ORDER BY english
    """).fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['old_english', 'new_english', 'cnt'])
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} distinct old->new english pair(s) to {out_path} for review.")


def export_lemma_csv(conn: sqlite3.Connection, out_path: Path) -> None:
    """One row per (strongs, lang) that has a replace_text (i.e. every name
    this build actually covers, whether find_text is filled in yet or
    not) -- the review CSV's token-level output has one row per surface
    string a name happens to appear with ("Aaron", "And Aaron", "But
    Aaron", ... all separately), which buries the handful of genuinely
    wrong rows under hundreds of correct repeats of the same rule. This is
    strongs_lemma's own grain instead: one row per name, editable in a
    spreadsheet, and re-loadable with --import-lemma-csv."""
    rows = conn.execute("""
        SELECT strongs, lang, lemma, transliteration, find_text, replace_text
        FROM strongs_lemma
        WHERE replace_text IS NOT NULL
        ORDER BY lang, CAST(strongs AS INTEGER)
    """).fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['strongs', 'lang', 'lemma', 'transliteration', 'find_text', 'replace_text'])
        writer.writerows(rows)
    print(f"Wrote {len(rows):,} strongs_lemma row(s) to {out_path} for review/editing "
          f"(re-load with --import-lemma-csv).")


def import_lemma_csv(conn: sqlite3.Connection, in_path: Path) -> None:
    """Load hand-edited find_text/replace_text back from an export_lemma_csv()
    file -- unconditionally, unlike the mechanical/bootstrap passes (this
    *is* the curated override, same authority as a direct hand-edit to the
    strongs_lemma row; run this before populate_strongs_lemma() so those
    passes see the imported values as already-curated and skip them).
    lemma/transliteration columns are read but ignored: they stay
    lexicon-derived, not overwritten from a spreadsheet edit. A (strongs,
    lang) the CSV names that isn't actually in strongs_lemma is reported,
    not silently dropped -- likely a typo or a stale export."""
    with open(in_path, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    updated = 0
    missing = []
    for row in rows:
        strongs, lang = row['strongs'], row['lang']
        find_text = row.get('find_text') or None
        replace_text = row.get('replace_text') or None
        cur = conn.execute(
            "UPDATE strongs_lemma SET find_text = ?, replace_text = ? WHERE strongs = ? AND lang = ?",
            (find_text, replace_text, strongs, lang)
        )
        if cur.rowcount:
            updated += 1
        else:
            missing.append((strongs, lang))
    conn.commit()
    print(f"Imported {updated:,} of {len(rows):,} row(s) from {in_path}.")
    if missing:
        print(f"WARNING: {len(missing):,} row(s) named a (strongs, lang) not in strongs_lemma "
              f"-- typo, or a stale export from before a lexicon rebuild? {missing[:10]}"
              f"{' ...' if len(missing) > 10 else ''}")


def build_restored_names(db_path: Path, hebrew_lexicon: Path, greek_lexicon: Path,
                          review_csv: Path, annotate: bool = False, reset: bool = False,
                          import_lemma_csv_path: Path = None,
                          export_lemma_csv_path: Path = None) -> None:
    conn = sqlite3.connect(db_path)
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strongs_lemma'"
    ).fetchone():
        conn.close()
        raise SystemExit("strongs_lemma not found -- run utils/import_lemma_table.py first.")
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tokens'"
    ).fetchone():
        conn.close()
        raise SystemExit("tokens not found -- run utils/import_bsb_table.py first.")

    if reset:
        # populate_strongs_lemma()/the mechanical passes only ever fill a row
        # WHERE find_text/replace_text IS NULL, so they never overwrite a
        # value that's already there -- deliberate, so a real hand edit
        # survives a rerun. That same guard means a value auto-bootstrapped
        # under an *older, buggier* version of this script (e.g. the
        # hyphenated-compound truncation fixed above) never gets the chance
        # to be recomputed either, since it's non-null too. --reset clears
        # every row unconditionally -- including genuine hand edits, if
        # you've since made any -- so the next pass starts from a clean
        # slate under the current logic.
        cleared = conn.execute(
            "UPDATE strongs_lemma SET find_text = NULL, replace_text = NULL "
            "WHERE find_text IS NOT NULL OR replace_text IS NOT NULL"
        ).rowcount
        conn.commit()
        print(f"--reset: cleared find_text/replace_text on {cleared:,} strongs_lemma row(s).")

    if import_lemma_csv_path:
        # Before populate_strongs_lemma(), so its NULL-guarded passes see
        # these rows as already-curated and never touch them.
        import_lemma_csv(conn, import_lemma_csv_path)

    populate_strongs_lemma(conn, hebrew_lexicon, greek_lexicon)
    add_restored_column(conn)
    apply_restorations(conn, annotate=annotate)
    write_review_csv(conn, review_csv)
    if export_lemma_csv_path:
        export_lemma_csv(conn, export_lemma_csv_path)
    conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--hebrew-lexicon', type=Path, default=DEFAULT_HEBREW_LEXICON,
                         help=f"Path to HebrewStrong.xml (default: {DEFAULT_HEBREW_LEXICON})")
    parser.add_argument('--greek-lexicon', type=Path, default=DEFAULT_GREEK_LEXICON,
                         help=f"Path to strongsgreek.xml (default: {DEFAULT_GREEK_LEXICON})")
    parser.add_argument('--db', type=Path, default=DEFAULT_DB,
                         help=f"bsb_tables.db path (default: {DEFAULT_DB})")
    parser.add_argument('--review-csv', type=Path, default=DEFAULT_REVIEW_CSV,
                         help=f"Where to write the review CSV (default: {DEFAULT_REVIEW_CSV})")
    parser.add_argument('--annotate', action='store_true',
                         help="Render 'Yehovah (LORD)' instead of bare 'Yehovah' in "
                              "english_restored -- debugging aid for scanning a compiled "
                              "module to see what got caught vs. missed; not meant for a "
                              "real build (DTB formatters render english_restored as-is, "
                              "no separate strip step).")
    parser.add_argument('--reset', action='store_true',
                         help="Clear every strongs_lemma find_text/replace_text before "
                              "running (including any hand edits) so this run's logic gets "
                              "a clean slate instead of skipping rows an earlier -- possibly "
                              "buggier -- run already filled in. Use after a code fix here "
                              "changes how find_text/replace_text get derived.")
    parser.add_argument('--export-lemma-csv', type=Path, default=None,
                         help="Write strongs_lemma's find_text/replace_text (one row per name, "
                              "not per surface-string variant like --review-csv) to this path "
                              "for review/editing in a spreadsheet.")
    parser.add_argument('--import-lemma-csv', type=Path, default=None,
                         help="Load find_text/replace_text back from a CSV in "
                              "--export-lemma-csv's shape, unconditionally (this becomes the "
                              "curated override, same as a direct hand edit) -- applied before "
                              "this run's own mechanical/bootstrap passes, so they treat the "
                              "imported rows as already curated and leave them alone.")
    args = parser.parse_args()
    build_restored_names(args.db, args.hebrew_lexicon, args.greek_lexicon, args.review_csv,
                          annotate=args.annotate, reset=args.reset,
                          import_lemma_csv_path=args.import_lemma_csv,
                          export_lemma_csv_path=args.export_lemma_csv)
