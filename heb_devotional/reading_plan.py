"""
heb_devotional/reading_plan.py

Shared input-side logic for the Hebrew-calendar devotional generators
(heb_devotional/esword.py, heb_devotional/mysword.py). Everything here is
output-format-agnostic: it turns an intermediate reading-plan JSON
(parshat.json shape: list of {week_no, week, type: D|W|H, refs: [...],
label?}) plus a live Hebcal fetch into
  - {date: [(heading, parashah_name, refs), ...]} (build_day_entries) --
    every reading assigned to its real calendar date
  - {date: hdate_string} / {date: [annotation, ...]} (process_hebcal_data)
  - a book-name -> abbreviation lookup and reference-string -> resolved
    (book, chapter, verse range) parsing, shared so both output formats
    produce identical, already-verified reference resolution rather than
    each reimplementing "book chapter" verse-range filling independently.

Requires: pip install requests

Scripture references (parshat.json's own convention: full English book
names, Roman-numeral prefixes -- "I Samuel", "II Kings" -- not our usual
abbreviated "1Sa 1:1-2:10" shape) are resolved via
verse_formatter.base.Reference. The only new piece here is mapping
parshat.json's full book names onto our abbreviations first
(_book_name_to_abbrev()) -- everything downstream reuses the existing,
already-verified Reference/label formatting rather than reinventing it.
"""

import json
import re
import sqlite3
import sys
import requests
from datetime import date, timedelta
from pathlib import Path

# Run directly, or import from a script one directory down (esword.py,
# mysword.py) run directly, and Python puts that script's own directory
# on sys.path[0], not the project root -- so verse_formatter (a
# project-root package) isn't importable unless the root is added
# explicitly. Harmless when already importable (e.g. run via `python -m`
# from the root) -- sys.path just gets a redundant entry.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biblelib.book import Books
from verse_formatter.base import Reference


HEBCAL_BASE = "https://www.hebcal.com/hebcal"


def find_cycle_window(hebrew_year, start="simchat_torah"):
    """
    Compute the exact date range for one Simchat-Torah-to-Simchat-Torah
    reading cycle, given the Hebrew year its Bereshit falls in.

    Uses a single lightweight Hebcal call (major holidays only, no items
    list needed) to get that year's range.start/range.end -- both are
    Erev Rosh Hashana, and Simchat Torah is always exactly 23 days after
    Erev Rosh Hashana (Tishrei 23), regardless of leap/regular year.

    cycle_start is always the Sunday on-or-before Simchat Torah
    (inclusive -- if Simchat Torah itself falls on a Sunday, that's day
    1) and cycle_end is always the Saturday immediately before the next
    cycle's own cycle_start -- this is the weekly reading plan's own
    anchor (see build_day_entries()) and is unaffected by `start`.

    `start` controls window_start, the first of the three returned
    values, meant for the caller's own Hebcal fetch window --
    "simchat_torah" (default) sets window_start = cycle_start, matching
    e-Sword's build (the weekly reading plan is all it ever shows, so
    there's nothing to gain from fetching earlier); "rosh_hashanah" sets
    window_start = this cycle's own Rosh Hashana instead (~3 weeks
    before cycle_start), for a caller -- MySword's build -- that wants
    Hebcal to actually return the Fall holidays leading into the cycle
    even though the weekly reading plan itself doesn't start until
    cycle_start (see heb_devotional.mysword's lead-in days).

    Returns (window_start, cycle_start, cycle_end) as date objects.
    """
    if start not in ("simchat_torah", "rosh_hashanah"):
        raise ValueError(f"find_cycle_window: start must be 'simchat_torah' or 'rosh_hashanah', got {start!r}")

    resp = requests.get(HEBCAL_BASE, params={
        "v": "1", "cfg": "json", "maj": "on", "yt": "H", "year": str(hebrew_year),
    }, timeout=30)
    resp.raise_for_status()
    r = resp.json()["range"]
    erev_rosh_hashanah = date.fromisoformat(r["start"])
    rosh_hashanah = erev_rosh_hashanah + timedelta(days=1)
    st_this = erev_rosh_hashanah + timedelta(days=23)
    st_next = date.fromisoformat(r["end"]) + timedelta(days=23)
    floor_sunday = lambda dt: dt - timedelta(days=(dt.weekday() + 1) % 7)
    cycle_start = floor_sunday(st_this)
    cycle_end = floor_sunday(st_next) - timedelta(days=1)
    window_start = rosh_hashanah if start == "rosh_hashanah" else cycle_start
    return window_start, cycle_start, cycle_end

def fetch_hebcal(start:date, end:date, hebrew_year:int):
    """
    Live Hebcal fetch covering one Simchat-Torah-to-Simchat-Torah cycle.
    start/end are ISO date strings; hebrew_year is the Hebcal 'year='
    value (only used as a hint -- start/end define the actual window).

    Params of note:
      maj=on, min=off, mod=off  -- major holidays on, minor/modern off
      mf=on                     -- minor fasts (Tzom Gedaliah, etc.) on
      nx=on                     -- Rosh Chodesh on
      ss=on                     -- special Shabbatot on
      s=on                      -- weekly parashat on (needed for week anchoring)
      d=on                      -- a dated entry for every single day,
                                    each carrying its own real hdate
    """
    params = {
        "v": "1", "cfg": "json",
        "maj": "on", "min": "off", "mod": "off",
        "nx": "on", "ss": "on", "mf": "on",
        "s": "on", "d": "on",
        "c": "on", "M": "off",
        "yt": "H", "year": str(hebrew_year), "month": "x",
        "start": start.isoformat(), "end": end.isoformat(),
    }
    resp = requests.get(HEBCAL_BASE, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def d(s):
    """Parse an ISO date string."""
    return date.fromisoformat(s)


def load_reading_plan(path):
    """Load the intermediate reading-plan JSON and group it by week_no."""
    with open(path) as f:
        records = json.load(f)
    weeks = {}
    for r in records:
        wn = r["week_no"]
        weeks.setdefault(wn, {"name": r["week"], "D": [], "W": None, "H": None})
        if r["type"] == "D":
            weeks[wn]["D"].append(r["refs"])
        elif r["type"] == "W":
            weeks[wn]["W"] = r["refs"]
        elif r["type"] == "H":
            weeks[wn]["H"] = {"label": r["label"], "refs": r["refs"]}
    return weeks


def process_hebcal_data(hebcal_json):
    """
    Split a Hebcal fetch (live or loaded from a saved file) into:
      - annotations: {date: [annotation, ...]} for every non-parashat,
        non-hebdate item (fasts, Rosh Chodesh, special Shabbatot,
        Mevarchim, major holidays), each with title/title_orig/category/
        subcat/yomtov/memo -- title_orig (ASCII apostrophes, same
        "prefer title_orig" convention as _bare_title()) is what
        heb_devotional.mysword._annotation_class() matches its titled
        exceptions against, since title itself may carry typographic
        Unicode apostrophes
      - hdates: {date: hdate_string}, sourced from every item that has
        one (with d=on, every single day has a "hebdate"-category entry,
        so this covers the full range with no gaps)
    """
    annotations = {}
    hdates = {}
    for item in hebcal_json["items"]:
        dt = d(item["date"])
        if item.get("hdate"):
            hdates[dt] = item["hdate"]
        if item.get("category") in ("parashat", "hebdate"):
            continue
        annotations.setdefault(dt, []).append({
            "title": item["title"],
            "title_orig": item.get("title_orig", item["title"]),
            "category": item.get("category"),
            "subcat": item.get("subcat"),
            "yomtov": item.get("yomtov", False),
            "memo": item.get("memo", ""),
        })
    return annotations, hdates


def _book_name_to_abbrev() -> dict:
    """Full English book name -> our display abbreviation ('Joh', '1Sa',
    'Sol', ...), sourced by joining biblelib's own English names against
    data/books.db's display_abbrev -- books.db doesn't store full names
    itself (see utils/build_books_table.py's "biblelib is the canonical
    source" rationale), so this reconstructs the join rather than adding
    a column only this script would use.

    Returns {full_name: (display_abbrev, osis_id)} -- both codes, since
    callers need display_abbrev for the <ref> tag itself (matches
    bsb_xrefs.json's own "Joh", "Exo", "Rom" convention -- e-Sword's
    <ref> tag doesn't recognize OSIS-style ids like "Exod") but osis_id
    to query bsb_tables.db's verses table, which is keyed on OSIS ids,
    not our display abbreviation (see utils/import_bsb_table.py's
    FULL_NAME_TO_OSIS / verses.book population).
    """
    db_path = Path(__file__).parent.parent / "data" / "books.db"
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT usx_code, osis_id, display_abbrev FROM books").fetchall()
    conn.close()

    biblelib_books = Books()
    mapping = {}
    for usx_code, osis_id, abbrev in rows:
        try:
            mapping[biblelib_books[usx_code].name] = (abbrev, osis_id)
        except KeyError:
            continue
    return mapping


def _last_verse(conn: sqlite3.Connection, osis_book: str, chapter: int):
    """Last verse number of one chapter, from bsb_tables.db's chapters
    table (one row per book/chapter, precomputed once at import time --
    see utils/import_bsb_table.py's post-processing step -- rather than
    re-aggregating verses on every lookup here). Returns None if the DB
    has no row for that book/chapter (unknown book id, chapter out of
    range, or an older bsb_tables.db built before the chapters table
    existed)."""
    row = conn.execute(
        "SELECT verse_count FROM chapters WHERE book = ? AND chapter = ?",
        (osis_book, chapter),
    ).fetchone()
    return row[0] if row else None


# parshat.json spells numbered books with a Roman-numeral prefix ("I Samuel",
# "II Kings"); biblelib's own names (and our abbreviation lookup, keyed on
# them) use Arabic numerals ("1 Samuel", "2 Kings") -- normalize before
# lookup rather than special-casing every numbered book in the map.
_ROMAN_TO_ARABIC = {'I': '1', 'II': '2', 'III': '3'}
_ROMAN_PREFIX_RE = re.compile(r'^(I{1,3})\s+')


def _normalize_book_name(name: str) -> str:
    m = _ROMAN_PREFIX_RE.match(name)
    return _ROMAN_TO_ARABIC[m.group(1)] + name[m.end(1):] if m else name


# Same shape as verse_formatter.base._XREF_REF_RE, with one difference:
# book is '.+?' (lazy, any characters) instead of '\S+', since parshat.json
# uses full, sometimes multi-word book names ("I Samuel", "Song of Songs")
# rather than our usual single-token abbreviations. The laziness matters --
# it lets the engine backtrack past embedded spaces in the book name to
# find the actual chapter-number boundary, rather than stopping at the
# first space.
_DEVOTIONAL_REF_RE = re.compile(
    r'^(?P<book>.+?)\s+(?P<chap>\d+)'
    r'(?:'
        r':(?P<verse>\d+)(?:-(?:(?P<end_chap>\d+):)?(?P<end_verse>\d+))?'
        r'|'
        r'-(?P<chap_end>\d+)'
    r')?$'
)


def _ref_to_tag(text: str, book_lookup: dict, unresolved: list, resolve_piece, depth: int = 0) -> list:
    """One raw parshat.json reference string -> a list of one or more
    verse_formatter.base.Reference objects. Handles book-name resolution
    and the two shapes that recurse instead of matching
    _DEVOTIONAL_REF_RE directly:
      - No digits at all ("II John; III John") -- one or more bare book
        names, semicolon-joined. Every real occurrence of this is a
        one-chapter book (2/3 John, Jude, Philemon, Obadiah), so a bare
        book name unambiguously means "the whole book" -> chapter 1.
      - Contains a comma ("Jeremiah 2:4-28, 3:4") -- a compound reference;
        every segment after the first drops the book name (implied from
        the first segment), so it's re-attached before parsing each part.

    Once a piece is down to a single resolved (book, chapter[, verse[,
    end_chap[, end_verse]]]), what to actually DO with it -- e-Sword's
    chapter-by-chapter split with a bsb_tables.db verse-count lookup, or
    MySword's single untouched Reference (see this module's docstring for
    why the two formats need different handling here) -- is deliberately
    not this function's decision: `resolve_piece(abbrev, osis_id, chapter,
    verse, end_chap, end_verse, orig_text) -> list[Reference]` is supplied
    by the caller (see resolve_refs() / resolve_refs_simple()) so this
    parsing logic -- regex, book lookup, semicolon/comma recursion -- is
    shared instead of duplicated per format.

    Unresolvable pieces (unmapped book name, unrecognized shape) fall
    back to a label-only Reference (book/chapter/verse all None, per the
    Reference dataclass's own documented convention for "couldn't be
    parsed at all") and get appended to `unresolved` for the caller to
    warn about, rather than silently producing a broken link or crashing
    the whole build over one bad reference.
    """
    text = text.strip()

    if not any(c.isdigit() for c in text):
        parts = [p.strip() for p in text.split(';') if p.strip()]
        return [ref for p in parts
                for ref in _ref_to_tag(f"{p} 1", book_lookup, unresolved, resolve_piece, depth + 1)]

    if ',' in text and depth == 0:
        segments = [s.strip() for s in text.split(',')]
        first_match = _DEVOTIONAL_REF_RE.match(segments[0])
        book_name = first_match.group('book') if first_match else None
        return [
            ref
            for i, seg in enumerate(segments)
            for ref in _ref_to_tag(seg if i == 0 or book_name is None else f"{book_name} {seg}",
                                    book_lookup, unresolved, resolve_piece, depth + 1)
        ]

    m = _DEVOTIONAL_REF_RE.match(text)
    if not m:
        unresolved.append(text)
        return [Reference(label=text)]

    resolved = book_lookup.get(_normalize_book_name(m.group('book')))
    if resolved is None:
        unresolved.append(text)
        return [Reference(label=text)]
    abbrev, osis_id = resolved

    chapter    = int(m.group('chap'))
    verse      = int(m.group('verse')) if m.group('verse') else None
    end_chap   = m.group('end_chap') or m.group('chap_end')
    end_chap   = int(end_chap) if end_chap else None
    end_verse  = int(m.group('end_verse')) if m.group('end_verse') else None

    return resolve_piece(abbrev, osis_id, chapter, verse, end_chap, end_verse, text)


def _chapter_split_refs(abbrev: str, osis_id: str, chapter: int, verse, end_chap, end_verse,
                         verses_conn, missing_bounds: list, orig_text: str) -> list:
    """A list holding one Reference for the chapter if the source
    reference doesn't cross a chapter boundary, or one Reference per
    chapter from `chapter` through `end_chap` if it does -- see
    _ref_to_tag()'s docstring for why both the splitting (each chapter
    its own list entry) and the verse-bounding are needed at all.

    Single-chapter, verse already fully given (including a lone verse
    with no explicit end, e.g. "Jeremiah 3:4" -- chapter=3, verse=4,
    end_verse=None) is returned exactly as given, no lookup: only the
    *absence* of a starting verse (the bare "book chapter[-chapter]"
    case) or an actual multi-chapter split triggers a bsb_tables.db
    lookup, never a single already-verse-bounded reference.

    For a real multi-chapter split, only the first chapter's start and
    the last chapter's end can come from the source text (whatever verse/
    end_verse it gave) -- every chapter in between, and either end when
    the source gave no verse at all, always runs its own full 1..last.
    A chapter whose end verse count couldn't be looked up (missing_bounds)
    still gets a Reference back with verse=None -- book/chapter are still
    valid, there's just no verse-range to display or link precisely; each
    formatter decides how to render that (e-Sword's <ref> tag just won't
    resolve without a range, MySword can still link to chapter/verse 1).
    """
    last_chap = end_chap if end_chap is not None else chapter

    if verse is not None and last_chap == chapter:
        return [Reference(book=abbrev, chapter=chapter, verse=verse, end_verse=end_verse)]

    refs = []
    for ch in range(chapter, last_chap + 1):
        start = verse if ch == chapter and verse is not None else 1
        if ch == last_chap and end_verse is not None:
            end = end_verse
        elif verses_conn is not None:
            end = _last_verse(verses_conn, osis_id, ch)
        else:
            end = None
        if end is None:
            missing_bounds.append(orig_text if last_chap == chapter else f"{orig_text} (chapter {ch})")
        refs.append(Reference(book=abbrev, chapter=ch, verse=(start if end is not None else None), end_verse=end))
    return refs


def resolve_refs(refs, book_lookup, verses_conn, unresolved, missing_bounds):
    """Resolve a list of parshat.json reference strings into a flat list
    of Reference objects, e-Sword-flavored: one list entry per chapter
    (not per source reference string, so the caller can put each on its
    own <li> -- see heb_devotional.esword.render_devotion_html()), with
    every chapter's verse range filled in from bsb_tables.db via
    _chapter_split_refs() -- required because e-Sword's <ref> tag doesn't
    resolve a bare "book chapter" reference (confirmed against the real
    app, not documented anywhere). See heb_devotional.mysword's own
    resolve_refs_simple() for the same source text without any of that:
    MySword's own link syntax has no such requirement."""
    def resolve_piece(abbrev, osis_id, chapter, verse, end_chap, end_verse, orig_text):
        return _chapter_split_refs(abbrev, osis_id, chapter, verse, end_chap, end_verse,
                                    verses_conn, missing_bounds, orig_text)
    return [ref for r in refs
            for ref in _ref_to_tag(r, book_lookup, unresolved, resolve_piece)]


def resolve_refs_simple(refs, book_lookup, unresolved):
    """Resolve a list of parshat.json reference strings into a flat list
    of Reference objects, MySword-flavored: no bsb_tables.db verse-count
    lookup at all -- see resolve_refs()'s docstring for why e-Sword needs
    one and MySword doesn't (a verse-bounded reference, single-chapter or
    cross-chapter, is returned exactly as parsed: chapter/verse/
    end_chapter/end_verse straight through, one Reference).

    A reference with NO verse at all still splits into one Reference per
    chapter when it spans more than one ("book chapter-chapter") --
    confirmed against a real build that MySword's own '#b<book>.<chapter>'
    addressing doesn't support a bare chapter RANGE: '#b1.15-16' opened
    as Genesis 15:1-16, i.e. the app read the trailing '-16' as a verse
    range on chapter 15, not "chapter 15 through 16". So a hyphen right
    after a bare chapter position isn't safe to ever emit -- each chapter
    gets its own Reference (and its own <li>, same "resume where you left
    off" reasoning e-Sword's own chapter-splitting has -- see
    _chapter_split_refs()), just without a verse-count lookup, since a
    bare '#b<book>.<chapter>' (no dash at all) is a perfectly fine link
    on its own.
    """
    def resolve_piece(abbrev, osis_id, chapter, verse, end_chap, end_verse, orig_text):
        if verse is not None or end_chap is None:
            return [Reference(book=abbrev, chapter=chapter, verse=verse,
                               end_chapter=end_chap, end_verse=end_verse)]
        return [Reference(book=abbrev, chapter=ch) for ch in range(chapter, end_chap + 1)]
    return [ref for r in refs
            for ref in _ref_to_tag(r, book_lookup, unresolved, resolve_piece)]


def build_day_entries(weeks, week_saturday, holiday_date):
    """
    Assign real calendar dates to every reading:
      - D rows -> Sun..Thu immediately preceding that week's Saturday
      - W row  -> duplicated onto both Friday and Saturday
      - H row  -> merged onto its own specific date (which may collide
                  with a D or W date -- multiple sections stack in the
                  same day's HTML, this is expected and intentional)
    week_saturday: {week_no: 'YYYY-MM-DD'} -- the Shabbat date for each
      week. For the five holiday-named weeks (Passover, Shavuot, Rosh
      Hashanah, Sukkot, Shmini Atzeret) this must be whichever Hebcal
      entry's torah/haftarah actually matches that week's W-row content
      for the year in question -- it is NOT a fixed title, since which
      Chol HaMoed day (etc.) falls on a Saturday shifts year to year.
    holiday_date: {label: 'YYYY-MM-DD'} -- the specific date each H-row
      holiday (Simchat Torah, Purim, Yom Kippur) falls on that cycle.
    Returns {date: [(heading, parashah_name, refs), ...]} in display
    order. parashah_name is the bare week name (wk["name"]) for D/W rows
    -- a lookup key into data/parashah_translations.json, separate from
    `heading` since the W row's heading has "(Torah/Haftarah)" appended
    -- or None for an H row, whose label (e.g. "Simchat Torah", "Yom
    Kippur") is a holiday name, not one of the 51 parashah names that
    file covers.
    """
    day_entries = {}
    for wn in sorted(weeks):
        wk = weeks[wn]
        sat = d(week_saturday[wn])
        sun = sat - timedelta(days=6)
        for i, refs in enumerate(wk["D"]):
            dt = sun + timedelta(days=i)
            day_entries.setdefault(dt, []).append((wk["name"], wk["name"], refs))
        fri = sat - timedelta(days=1)
        for dt in (fri, sat):
            day_entries.setdefault(dt, []).append(
                (f"{wk['name']} (Torah/Haftarah)", wk["name"], wk["W"]))
        if wk["H"]:
            hdate_key = d(holiday_date[wk["H"]["label"]])
            day_entries.setdefault(hdate_key, []).append((wk["H"]["label"], None, wk["H"]["refs"]))
    return day_entries


PRIMARY_READING_CATEGORIES = ("parashat", "holiday")


def _bare_title(item):
    """Strip Hebcal's 'Parashat '/'Parshat ' prefix and prefer title_orig
    (ASCII apostrophes) over title (which may use typographic ones)."""
    t = item.get("title_orig", item["title"])
    for prefix in ("Parashat ", "Parshat "):
        if t.startswith(prefix):
            return t[len(prefix):]
    return t


# Weeks whose Hebcal title carries a year number or roman-numeral day
# suffix that varies year to year (e.g. "Rosh Hashana 5787", "Pesach III
# (CH''M)") -- these need prefix matching in the verification pass below,
# not exact matching. reading_plan.json's week names for these five were
# renamed to match Hebcal's own terms: "Passover" -> "Pesach",
# "Rosh Hashanah" -> "Rosh Hashana". "Shmini Atzeret" needs no special
# handling -- uniquely among the five, Hebcal's own title for it carries
# no suffix at all, so it matches exactly like a normal parsha week.
HOLIDAY_NAMED_WEEKS = {"Rosh Hashana", "Sukkot", "Shavuot", "Pesach", "Shmini Atzeret"}


def derive_week_saturdays(hebcal_json, first_week_name, num_weeks, weeks=None):
    """
    Walk every Saturday in the fetched window, in order, picking each
    Saturday's PRIMARY reading (the parashat entry if one exists that
    week, otherwise whichever holiday entry actually carries a torah
    reading -- Rosh Hashana/Sukkot/Shavuot/Pesach/Shmini Atzeret; entries
    like Shabbat Chazon or Rosh Chodesh that merely annotate a parasha
    week are skipped here since they don't replace that week's own
    reading).

    Starts at the first Saturday whose bare title (with any 'Parashat '
    prefix stripped) matches first_week_name (normally "Bereshit") and
    returns exactly num_weeks consecutive Saturdays from there as
    {week_no: 'YYYY-MM-DD'} (1-indexed).

    Raises ValueError if fewer than num_weeks Saturdays with a primary
    reading exist before the window runs out or a second occurrence of
    first_week_name is hit first -- that mismatch means this year's
    combined/split parsha pattern doesn't match reading_plan.json's
    week count (e.g. a leap year), and week_saturday needs a JSON built
    for that year type rather than being silently mis-zipped.

    If weeks (the {week_no: {"name": ..., ...}} dict from
    load_reading_plan) is passed, each derived Saturday's actual Hebcal
    title is cross-checked against that week's expected name as a sanity
    check -- mismatches are printed as notices, not raised, since the
    core algorithm is positional and doesn't depend on this matching to
    produce dates; it's just an early-warning signal something's off.
    """
    by_date = {}
    for item in hebcal_json["items"]:
        if item.get("category") not in PRIMARY_READING_CATEGORIES:
            continue
        if not item.get("leyning"):
            continue  # e.g. Erev Sukkot, Erev Pesach have no reading of their own
        dt = d(item["date"])
        if dt.weekday() != 5:  # Saturday
            continue
        # Prefer parashat if a Saturday has both a parashat and a
        # holiday entry (e.g. Parashat Miketz during Chanukah)
        if dt not in by_date or item["category"] == "parashat":
            by_date[dt] = item

    saturdays = sorted(by_date)
    start_idx = None
    for i, dt in enumerate(saturdays):
        if _bare_title(by_date[dt]) == first_week_name:
            start_idx = i
            break
    if start_idx is None:
        raise ValueError(f"Could not find a Saturday titled {first_week_name!r} in the fetched window")

    result = {}
    for offset in range(num_weeks):
        idx = start_idx + offset
        if idx >= len(saturdays):
            raise ValueError(
                f"Ran out of Saturdays after {offset} of {num_weeks} weeks -- "
                f"widen the fetch window or check this year's week count matches reading_plan.json"
            )
        dt = saturdays[idx]
        if offset > 0 and _bare_title(by_date[dt]) == first_week_name:
            raise ValueError(
                f"Hit a second {first_week_name!r} after only {offset} of {num_weeks} weeks -- "
                f"this year's parsha combination pattern doesn't match reading_plan.json's week count"
            )
        week_no = offset + 1
        result[week_no] = dt.isoformat()

        if weeks is not None and week_no in weeks:
            expected = weeks[week_no]["name"]
            actual = _bare_title(by_date[dt])
            ok = actual.startswith(expected) if expected in HOLIDAY_NAMED_WEEKS else actual == expected
            if not ok:
                print(f"NOTE: week {week_no} expected {expected!r} but Hebcal shows "
                      f"{actual!r} on {dt.isoformat()} -- dates were still derived positionally, "
                      f"but this week's name doesn't match; double check reading_plan.json")

    return result


def derive_holiday_dates(hebcal_json, labels, min_date=None):
    """
    Look up the specific date each H-row label (e.g. 'Simchat Torah',
    'Purim', 'Yom Kippur') falls on within the fetched window, straight
    from Hebcal's own titles -- no hardcoding, and no separate fetch:
    this searches the same full-cycle hebcal_json already pulled for
    the daily/weekly readings and annotations. Picks the first
    chronological match per label (items are walked in the order Hebcal
    returned them, which is date order).

    min_date: skip any item dated before this -- needed when the
    caller's fetch window starts earlier than cycle_start (e.g.
    heb_devotional.mysword's Rosh-Hashanah lead-in fetch, see
    find_cycle_window()'s `start` parameter): most H-row holidays recur
    yearly, so a week deep in the 51-week cycle (Yom Kippur, week 50 --
    nearly a full year after cycle_start) can share its title with an
    earlier, pre-cycle occurrence of the same holiday that the widened
    fetch now also returns. Without min_date, "first chronological
    match" would silently grab that wrong, too-early date instead.
    Simchat Torah itself (week 1's own H-row) is unaffected either way
    -- its real occurrence is always on or after cycle_start by
    construction (find_cycle_window() derives cycle_start FROM it).
    """
    result = {}
    for item in hebcal_json["items"]:
        if min_date is not None and d(item["date"]) < min_date:
            continue
        if item["title"] in labels and item["title"] not in result:
            result[item["title"]] = item["date"]
    missing = set(labels) - set(result)
    if missing:
        raise ValueError(f"Could not find dates for: {missing}")
    return result
