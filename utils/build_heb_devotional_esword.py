"""
utils/build_heb_devitional_esword.py

Generates an e-Sword Daily Devotional (.devi) module from:
  1. An intermediate reading-plan JSON (parshat.json shape: list of
     {week_no, week, type: D|W|H, refs: [...], label?})
  2. A live Hebcal fetch covering the same Hebrew-calendar cycle, used to:
       a. anchor each week to its real Shabbat date and derive Sun-Thu dates
       b. anchor each H-row (footnoted holiday) to its specific date
       c. annotate every date with any other Hebcal observance that falls
          on it (fasts, Rosh Chodesh, special Shabbatot, Mevarchim, etc.)
          including memo text and yomtov (non-work day) status
       d. supply the real Hebrew date (hdate) for every single day, via
          Hebcal's d=on parameter -- no calculation needed

Requires: pip install requests

Scripture references (parshat.json's own convention: full English book
names, Roman-numeral prefixes -- "I Samuel", "II Kings" -- not our usual
abbreviated "1Sa 1:1-2:10" shape) are resolved to e-Sword-recognized
<ref>...</ref> tags the same way our Bible modules do: parsed into a
verse_formatter.base.Reference and rendered through the same
_default_ref_label() every ESwordReverseInterlinearFormatter/etc. <ref>
tag goes through (see _ref_to_tag() below). The only new piece here is
mapping parshat.json's full book names onto our abbreviations first
(_book_name_to_abbrev()) -- everything downstream of that reuses the
existing, already-verified formatting logic rather than reinventing it.

.devi schema (confirmed against a real e-Sword sample):
    CREATE TABLE Details (Title NVARCHAR(255), Abbreviation NVARCHAR(50),
                           Information TEXT, Version INT)
    CREATE TABLE Devotional (Month INT, Day INT, Devotion TEXT)
    CREATE INDEX MonthDayIndex ON Devotional (Month, Day)
Devotional is keyed on Month/Day only -- no Year column -- so one .devi
file is generated per Gregorian-spanning Hebrew cycle (Simchat Torah to
Simchat Torah), not per Gregorian year. A leap Hebrew year runs long
enough (383-385 days) that it cannot be squeezed into 365 Month/Day
slots without collisions -- that's the reason for the per-cycle file
model rather than per-Gregorian-year.
"""

import json
import re
import sqlite3
import os
import sys
import requests
from datetime import date, timedelta
from pathlib import Path

# Run directly (`python utils/build_heb_devotional_esword.py`), Python puts
# this script's own directory (utils/) on sys.path[0], not the project
# root -- so verse_formatter (a project-root package) isn't importable
# unless the root is added explicitly. Every other utils/*.py script only
# imports stdlib + biblelib, so this hasn't come up before; this is the
# first one reaching back into the main project package. Harmless when
# already importable (e.g. run via `python -m` from the root, or from an
# interactive console started there) -- sys.path just gets a redundant
# entry.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from biblelib.book import Books
from verse_formatter.base import Reference, _default_ref_label


HEBCAL_BASE = "https://www.hebcal.com/hebcal"


def find_cycle_window(hebrew_year):
    """
    Compute the exact date range for one Simchat-Torah-to-Simchat-Torah
    reading cycle, given the Hebrew year its Bereshit falls in.

    Uses a single lightweight Hebcal call (major holidays only, no items
    list needed) to get that year's range.start/range.end -- both are
    Erev Rosh Hashana, and Simchat Torah is always exactly 23 days after
    Erev Rosh Hashana (Tishrei 23), regardless of leap/regular year.

    A cycle starts the Sunday on-or-before Simchat Torah (inclusive --
    if Simchat Torah itself falls on a Sunday, that's day 1) and the
    previous cycle ends the Saturday immediately before that Sunday.

    Returns (cycle_start, cycle_end) as date objects.
    """
    resp = requests.get(HEBCAL_BASE, params={
        "v": "1", "cfg": "json", "maj": "on", "yt": "H", "year": str(hebrew_year),
    }, timeout=30)
    resp.raise_for_status()
    r = resp.json()["range"]
    st_this = date.fromisoformat(r["start"]) + timedelta(days=23)
    st_next = date.fromisoformat(r["end"]) + timedelta(days=23)
    floor_sunday = lambda dt: dt - timedelta(days=(dt.weekday() + 1) % 7)
    cycle_start = floor_sunday(st_this)
    cycle_end = floor_sunday(st_next) - timedelta(days=1)
    return cycle_start, cycle_end

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
        Mevarchim, major holidays), each with title/category/subcat/
        yomtov/memo
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


def _ref_to_tag(text: str, book_lookup: dict, verses_conn, unresolved: list,
                 missing_bounds: list, depth: int = 0) -> list:
    """One raw parshat.json reference string -> a list of one or more
    '<ref>...</ref>' tags (kept as separate list entries rather than one
    joined string so the caller can put each on its own <li> -- see
    render_devotion_html()), formatted exactly like our Bible modules' own
    e-Sword references
    (verse_formatter.base.Reference + _default_ref_label() -- same
    function ESwordReverseInterlinearFormatter etc. use). Unresolvable
    pieces (unmapped book name, unrecognized shape) fall back to the raw
    text wrapped as-is and get appended to `unresolved` for the caller to
    warn about, rather than silently producing a broken link or crashing
    the whole build over one bad reference.

    Two shapes recurse instead of matching _DEVOTIONAL_REF_RE directly:
      - No digits at all ("II John; III John") -- one or more bare book
        names, semicolon-joined. Every real occurrence of this is a
        one-chapter book (2/3 John, Jude, Philemon, Obadiah), so a bare
        book name unambiguously means "the whole book" -> chapter 1.
      - Contains a comma ("Jeremiah 2:4-28, 3:4") -- a compound reference;
        every segment after the first drops the book name (implied from
        the first segment), so it's re-attached before parsing each part.

    e-Sword's <ref> tag doesn't resolve a bare "book chapter" or
    "book chapter-chapter" reference -- confirmed against the real app,
    not documented anywhere -- it needs an explicit verse range. A
    reference spanning more than one chapter is also split into one
    <ref> per chapter rather than one combined range -- e-Sword loads a
    <ref>'s whole target into the reading window at once, so a single
    multi-chapter tag means a massive combined view; per-chapter tags
    keep each one small and let a partially-read day resume from
    wherever it left off. Both cases pull real verse counts from
    bsb_tables.db's chapters table via _last_verse() -- see
    _chapter_split_refs() -- whenever the source text doesn't already
    give one for a given chapter (verses_conn -- opened once by the
    caller; None skips lookups and leaves those chapters bare, e.g. when
    bsb_tables.db hasn't been built). A lookup that comes back empty
    (unknown book id, or bsb_tables.db has no data for that book/chapter)
    is not treated the same as an unresolved reference -- the book/shape
    parsed fine, there's just no verse count available -- so it's
    collected into missing_bounds instead of guessing.
    """
    text = text.strip()

    if not any(c.isdigit() for c in text):
        parts = [p.strip() for p in text.split(';') if p.strip()]
        return [tag for p in parts
                for tag in _ref_to_tag(f"{p} 1", book_lookup, verses_conn, unresolved,
                                        missing_bounds, depth + 1)]

    if ',' in text and depth == 0:
        segments = [s.strip() for s in text.split(',')]
        first_match = _DEVOTIONAL_REF_RE.match(segments[0])
        book_name = first_match.group('book') if first_match else None
        return [
            tag
            for i, seg in enumerate(segments)
            for tag in _ref_to_tag(seg if i == 0 or book_name is None else f"{book_name} {seg}",
                                    book_lookup, verses_conn, unresolved, missing_bounds, depth + 1)
        ]

    m = _DEVOTIONAL_REF_RE.match(text)
    if not m:
        unresolved.append(text)
        return [f'<ref>{text}</ref>']

    resolved = book_lookup.get(_normalize_book_name(m.group('book')))
    if resolved is None:
        unresolved.append(text)
        return [f'<ref>{text}</ref>']
    abbrev, osis_id = resolved

    chapter    = int(m.group('chap'))
    verse      = int(m.group('verse')) if m.group('verse') else None
    end_chap   = m.group('end_chap') or m.group('chap_end')
    end_chap   = int(end_chap) if end_chap else None
    end_verse  = int(m.group('end_verse')) if m.group('end_verse') else None

    return _chapter_split_refs(abbrev, osis_id, chapter, verse, end_chap, end_verse,
                                verses_conn, missing_bounds, text)


def _chapter_split_refs(abbrev: str, osis_id: str, chapter: int, verse, end_chap, end_verse,
                         verses_conn, missing_bounds: list, orig_text: str) -> list:
    """A list holding one chapter's own <ref> tag if the reference doesn't
    cross a chapter boundary, or one <ref> per chapter from `chapter`
    through `end_chap` if it does -- see _ref_to_tag()'s docstring for why
    both the splitting (each chapter its own list entry, its own <li> once
    rendered) and the verse-bounding are needed at all.

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
    """
    last_chap = end_chap if end_chap is not None else chapter

    if verse is not None and last_chap == chapter:
        ref = Reference(book=abbrev, chapter=chapter, verse=verse, end_verse=end_verse)
        return [f'<ref>{_default_ref_label(ref)}</ref>']

    tags = []
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
        ref = Reference(book=abbrev, chapter=ch, verse=(start if end is not None else None), end_verse=end)
        tags.append(f'<ref>{_default_ref_label(ref)}</ref>')
    return tags


def ref_wrap(refs, book_lookup, verses_conn, unresolved, missing_bounds):
    """Format a list of reference strings into a flat list of individual
    e-Sword <ref> tags -- one list entry per chapter, not per source
    reference string, so the caller can put each on its own <li> (see
    render_devotion_html()). See _ref_to_tag() for the actual book-name
    resolution, verse-bounding, and formatting."""
    return [tag for r in refs
            for tag in _ref_to_tag(r, book_lookup, verses_conn, unresolved, missing_bounds)]


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


def derive_holiday_dates(hebcal_json, labels):
    """
    Look up the specific date each H-row label (e.g. 'Simchat Torah',
    'Purim', 'Yom Kippur') falls on within the fetched window, straight
    from Hebcal's own titles -- no hardcoding, and no separate fetch:
    this searches the same full-cycle hebcal_json already pulled for
    the daily/weekly readings and annotations.
    """
    result = {}
    for item in hebcal_json["items"]:
        if item["title"] in labels and item["title"] not in result:
            result[item["title"]] = item["date"]
    missing = set(labels) - set(result)
    if missing:
        raise ValueError(f"Could not find dates for: {missing}")
    return result


def render_devotion_html(sections, annotations_for_day, book_lookup, verses_conn,
                          unresolved, missing_bounds, parashah_translations,
                          hdate_str=None, weekday=None):
    """Build the full Devotion HTML for one calendar day."""
    css = '<style>.head_info {min-width:100%; background-color:#F2F7F8;} .head_info * {display:block; width:100%; text-align:center;}</style>'
    parts = [css]
    hdate_line = " - ".join(p for p in (weekday, hdate_str) if p) if (hdate_str or weekday) else None

    for heading, parashah_name, refs in sections:
        parts.append('<div class="head_info">')
        if hdate_line:
            parts.append(f'<p>{hdate_line}</p>')
        parts.append(f'<h2>{heading}</h2>')
        translation = parashah_translations.get(parashah_name) if parashah_name else None
        if translation:
            parts.append(f'<p><i>{translation}</i></p>')
        parts.append('</div>')
        tags = ref_wrap(refs, book_lookup, verses_conn, unresolved, missing_bounds)
        parts.append("<ol>" + "".join(f"<li>{tag}</li>" for tag in tags) + "</ol>")

    if annotations_for_day:
        parts.append('<div class="observances">')
        for ann in annotations_for_day:
            label = ann["title"]
            if ann["yomtov"]:
                label = f"<b>{label} (Yom Tov \u2014 no work)</b>"
            parts.append(f"<p>{label}")
            if ann["memo"]:
                parts.append(f"<br><i>{ann['memo']}</i>")
            parts.append("</p>")
        parts.append("</div>")

    return "".join(parts)


def generate_devi(reading_plan_path, hebrew_year, output_path,
                   title, abbreviation, information, first_week_name="Bereshit",
                   table_db=None, parashah_translations_path=None):
    """
    hebrew_year: the Hebrew year this cycle's Bereshit falls in -- that's
      the only manual input needed. The fetch window, each week's
      Shabbat date, and each H-row holiday's specific date are all
      derived from Hebcal.
    table_db: path to bsb_tables.db (default data/bsb_tables.db), used to
      fill in real verse ranges for bare "book chapter" references -- see
      _ref_to_tag()'s docstring for why that's required at all. Missing
      (not yet built) is handled gracefully: those references are just
      left chapter-only and collected into the missing_bounds warning,
      not a hard failure -- same spirit as a missing hdate.
    parashah_translations_path: path to parashah_translations.json
      (default data/parashah_translations.json) -- {week name: English
      translation}, one entry per name in reading_plan_path's own "week"
      field. Rendered as its own line under a D/W row's <h3> heading (see
      render_devotion_html()); H rows (holiday labels like "Yom Kippur")
      aren't looked up here -- see build_day_entries()'s docstring for why.
    """
    weeks = load_reading_plan(reading_plan_path)
    num_weeks = len(weeks)
    h_labels = {wk["H"]["label"] for wk in weeks.values() if wk["H"]}

    cycle_start, cycle_end = find_cycle_window(hebrew_year)
    hebcal_json = fetch_hebcal(cycle_start, cycle_end, hebrew_year)

    week_saturday = derive_week_saturdays(hebcal_json, first_week_name, num_weeks, weeks=weeks)
    holiday_date = derive_holiday_dates(hebcal_json, h_labels)

    annotations, hdates = process_hebcal_data(hebcal_json)
    day_entries = build_day_entries(weeks, week_saturday, holiday_date)
    book_lookup = _book_name_to_abbrev()
    unresolved_refs = []
    missing_bounds = []

    parashah_translations_path = Path(parashah_translations_path) if parashah_translations_path \
        else Path(__file__).parent.parent / "data" / "parashah_translations.json"
    with open(parashah_translations_path, encoding="utf-8") as f:
        parashah_translations = json.load(f)
    missing_translations = sorted({wk["name"] for wk in weeks.values()} - set(parashah_translations))
    if missing_translations:
        print(f"WARNING: {len(missing_translations)} week name(s) have no entry in "
              f"{parashah_translations_path.name}: {missing_translations}")

    table_db = Path(table_db) if table_db else Path(__file__).parent.parent / "data" / "bsb_tables.db"
    verses_conn = sqlite3.connect(table_db) if table_db.exists() else None
    if verses_conn is None:
        print(f"WARNING: {table_db} not found -- bare 'book chapter' references will be "
              f"left without a verse range, which e-Sword's <ref> tag doesn't resolve. "
              f"Build it with utils/import_bsb_table.py first.")

    if os.path.exists(output_path):
        os.remove(output_path)
    conn = sqlite3.connect(output_path)
    cur = conn.cursor()
    cur.execute('CREATE TABLE Details (Title NVARCHAR(255), Abbreviation NVARCHAR(50), Information TEXT, Version INT)')
    cur.execute('CREATE TABLE Devotional (Month INT, Day INT, Devotion TEXT)')
    cur.execute('CREATE INDEX MonthDayIndex ON Devotional (Month, Day)')

    cur.execute(
        "INSERT INTO Details (Title, Abbreviation, Information, Version) VALUES (?,?,?,?)",
        (title, abbreviation, information, 4),
    )

    missing_hdate = []
    for dt in sorted(day_entries):
        hd = hdates.get(dt)
        if hd is None:
            missing_hdate.append(dt)
        html = render_devotion_html(day_entries[dt], annotations.get(dt, []), book_lookup,
                                     verses_conn, unresolved_refs, missing_bounds,
                                     parashah_translations, hd, weekday=dt.strftime('%A'))
        cur.execute(
            "INSERT INTO Devotional (Month, Day, Devotion) VALUES (?,?,?)",
            (dt.month, dt.day, html),
        )

    conn.commit()
    conn.close()
    if verses_conn is not None:
        verses_conn.close()

    if missing_hdate:
        print(f"WARNING: {len(missing_hdate)} day(s) had no hdate from Hebcal "
              f"(check the derived cycle window covers the full range): {missing_hdate[:5]}...")

    if unresolved_refs:
        print(f"WARNING: {len(unresolved_refs)} reference(s) could not be resolved to a "
              f"book abbreviation and were left as raw text (broken <ref> links): {unresolved_refs}")

    if missing_bounds:
        print(f"WARNING: {len(missing_bounds)} reference(s) had no verse count available "
              f"and were left chapter-only, which e-Sword's <ref> tag won't resolve: {missing_bounds}")

    return len(day_entries)


if __name__ == "__main__":
    # @TODO: swap to input parameter
    hebrew_year = 5786
    base_dir = Path(__file__).parent.parent
    output_path = base_dir / "output" / f"mjaa-{hebrew_year}.devi"
    count = generate_devi(
        reading_plan_path=base_dir / "data" / "parshat.json",
        hebrew_year=hebrew_year,
        output_path=output_path,
        title=f"MJAA Messianic Reading Plan {hebrew_year}",
        abbreviation=f"MJAA {hebrew_year}",
        information=(
            "<p>Messianic Jewish Alliance of America \"Read the Bible in a Year\" plan, "
            f"{hebrew_year} cycle. Weekly Torah/Haftarah portions plus daily OT/NT readings, "
            "keyed to Simchat Torah through Simchat Torah. Also annotates fasts, Rosh Chodesh, "
            "special Shabbatot, and Yom Tov status from Hebcal, and shows the Hebrew date.</p>"
        ),
    )
    print(f"Wrote {count} Devotional rows to output/{output_path.name}")