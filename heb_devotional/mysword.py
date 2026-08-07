"""
heb_devotional/mysword.py

Generates a MySword Journal-format devotional module from
heb_devotional.reading_plan's output -- see reading_plan.py for how
{date: [(heading, parashah_name, refs), ...]}, {date: hdate_string}, and
{date: [annotation, ...]} are derived from parshat.json + a live Hebcal
fetch. This module only owns MySword-journal-specific rendering and
SQLite writing.

journal schema (confirmed against a real MySword reference-book module,
Morning and Evening: Daily Readings), file extension .bok.mybible
(confirmed):
    CREATE TABLE details(name TEXT, title TEXT, abbreviation TEXT,
        author TEXT, description TEXT, comments TEXT, version TEXT,
        versiondate DATETIME, publishdate TEXT, publisher TEXT,
        creator TEXT, source TEXT, language NVARCHAR(3), readonly BOOL,
        customcss TEXT, righttoleft INT default 0)
    CREATE TABLE journal(rowid INTEGER primary key autoincrement,
        id TEXT collate nocase, title TEXT collate nocase, date DATETIME,
        tags TEXT, content TEXT, relativeorder INT default 0,
        hidden INT default 0)
    CREATE UNIQUE INDEX idx_journal_id on journal(id)
    CREATE UNIQUE INDEX idx_journal_title on journal(title)
    CREATE INDEX idx_journal_date on journal(date)
No FTS shadow tables (journalFTS etc.) -- not needed for a
bundled/imported module. MySword shows a journal row's own `title`
automatically above its content (confirmed against a real build), so
page content below never repeats it as its own heading -- only render an
<h2> for something the title doesn't already say (a day page's parashah
name, which differs from that row's date-based title).

Unlike e-Sword's Devotional table (Month/Day only, no year -- see
esword.py's docstring for the leap-year collision that forces), journal
rows are keyed by arbitrary unique id/title strings and carry a real
DATETIME. Every id here bakes in the Gregorian year (e.g. "15 Oct 2026"
for a day row, "October 2026" for a month row), so the same leap-year
Fall-straddles-two-Decembers collision that required merging rows for
e-Sword simply never happens here -- every real date gets its own row.

Navigation is Index -> month page (a real calendar table, with prev/next
links to adjacent months when they exist) -> day page, each a real
'#j <id>' journal link (MySword's own link-type prefix, "j" for journal
same as "b" for bible -- see https://www.mysword.info/modules-format).
Bible references link via a '#b<book_num>.<chapter>[.<verse>]' anchor
instead of e-Sword's <ref> tag -- built from
reading_plan.resolve_refs_simple() rather than resolve_refs():
confirmed against a real build that MySword, unlike e-Sword, has no
requirement for an explicit verse range -- a bare "book chapter"
reference links just fine as '#b<book_num>.<chapter>', so this module
never needs bsb_tables.db at all (contrast e-Sword's <ref> tag, which
does).

details.customcss is loaded by MySword automatically, so unlike
e-Sword's .devi (no CustomCSS column at all -- every row there repeats
its own inline <style> block) the CSS below is declared exactly once,
including three classes for Hebcal annotation categories (major
holiday/Yom Tov, minor holiday or new moon, fast day) -- see
_annotation_class()'s docstring for how those are inferred and why
they're not yet fully confirmed.
"""

import calendar
import json
import os
import sqlite3
from datetime import date
from pathlib import Path

from .reading_plan import (
    load_reading_plan, find_cycle_window, fetch_hebcal, process_hebcal_data,
    derive_week_saturdays, derive_holiday_dates, build_day_entries,
    _book_name_to_abbrev, resolve_refs_simple,
)
from verse_formatter.base import ABBREV_TO_BOOK_NUM, _default_ref_label


_CUSTOM_CSS = (
    ".head_info {min-width:100%; background-color:#F2F7F8;} "
    ".head_info * {display:block; width:100%; text-align:center;} "
    ".cal {width:100%; border-collapse:collapse; text-align:center;} "
    ".cal th, .cal td {border:1px solid #ccc; padding:4px;} "
    ".cal td.pad {border:none;} "
    ".cal-nav {width:100%; display:flex; justify-content:space-between;} "
    ".day-nav {width:100%; display:flex; align-items:center;} "
    ".day-nav-date {flex:1; text-align:center;} "
    ".major-holiday {font-weight:bold; background-color:#FFF9B0;} "
    ".minor-holiday {background-color:#FFE5B4;} "
    ".fast-day {background-color:#C8A27A;}"
)


def _month_id(dt: date) -> str:
    return dt.strftime('%B %Y')


def _day_id(dt: date) -> str:
    return dt.strftime('%d %b %Y')


def _annotation_class(ann: dict) -> str:
    """Hebcal annotation -> one of three CSS classes: major holiday (Yom
    Tov -- Rosh Hashana, Yom Kippur, Sukkot, Shmini Atzeret, Pesach,
    Shavuot), fast day (Tzom Gedaliah, Asara B'Tevet, etc.), or minor
    holiday/new moon (Rosh Chodesh, special Shabbatot, and anything else
    that doesn't fit the other two -- catch-all default).

    yomtov is a real Hebcal field we already capture (process_hebcal_data)
    and trust; the fast/roshchodesh category check is Hebcal's documented
    category vocabulary but hasn't been exercised against a real fetch in
    this session (no live Hebcal access here) -- double check fast days
    and Rosh Chodesh land in the right bucket the next time this runs
    against real data.
    """
    if ann["yomtov"]:
        return "major-holiday"
    if ann["category"] == "fast" or ann.get("subcat") == "fast":
        return "fast-day"
    return "minor-holiday"


_CLASS_PRIORITY = {"major-holiday": 0, "fast-day": 1, "minor-holiday": 2}


def _day_class(dt, annotations):
    """The single best class for one calendar cell, for a date with any
    Hebcal annotation(s) -- major-holiday beats fast-day beats
    minor-holiday when a date carries more than one (e.g. a special
    Shabbat that's also Rosh Chodesh). None if the date has no
    annotation at all."""
    anns = annotations.get(dt)
    if not anns:
        return None
    return min({_annotation_class(a) for a in anns}, key=_CLASS_PRIORITY.__getitem__)


def _ref_links(refs, book_lookup, unresolved):
    """Resolved Reference objects -> MySword bible-link anchors
    ('<a class="bible" href="#b<book_num>.<chapter>.<verse>[&w=1]">label</a>').
    A label-only Reference (unresolvable book/shape -- see
    reading_plan._ref_to_tag()) renders as plain text, same fallback
    verse_formatter.base._MySwordXrefMixin.transform_reference() uses for
    a Reference with no book/chapter.

    A reference with no verse at all (a bare "book chapter") links to
    that chapter's verse 1 with the '&w=1' suffix MySword's own docs
    describe: "can be optionally suffixed by &w=1 indicating whole
    chapter to display in case of popup" -- the base b.c.v address still
    needs a real verse (verse 1, the chapter's start), &w=1 is what
    actually asks MySword to show the whole chapter rather than homing
    in on just that one verse. No bsb_tables.db lookup needed either
    way. resolve_refs_simple() already splits a chapter-spanning bare
    reference into one Reference per chapter before this ever sees it
    (confirmed a bare '#b<book>.<chapter>-<chapter>' range isn't safe --
    MySword read the trailing number as a verse range on the first
    chapter instead -- so &w=1 is applied per chapter, not to a combined
    range), so a Reference reaching here with verse=None never has
    end_chapter set either. See reading_plan.resolve_refs_simple().
    """
    resolved = resolve_refs_simple(refs, book_lookup, unresolved)
    links = []
    for ref in resolved:
        if ref.book is None:
            links.append(ref.label or '')
            continue
        label = _default_ref_label(ref)
        book_num = ABBREV_TO_BOOK_NUM.get(ref.book)
        if not book_num:
            links.append(label)
            continue
        if ref.verse is not None:
            loc = f"{book_num}.{ref.chapter}.{ref.verse}"
            if ref.end_verse:
                loc += f"-{ref.end_chapter}.{ref.end_verse}" if ref.end_chapter else f"-{ref.end_verse}"
        else:
            loc = f"{book_num}.{ref.chapter}.1&w=1"
        links.append(f'<a class="bible" href="#b{loc}">{label}</a>')
    return links


def render_day_page(dt, sections, annotations_for_day, book_lookup,
                     unresolved, parashah_translations, hdate_str,
                     prev_dt=None, next_dt=None):
    """Build one day page's content. `sections` is day_entries[dt]:
    [(heading, parashah_name, refs), ...], already scoped to this single
    real date -- unlike e-Sword's render_devotion_html(), no cross-date
    merging is ever needed here (see this module's docstring).

    Two nav lines: Index / month (structural, Index first since it's the
    top level) and << weekday - hdate >> (temporal, prev/next day).
    prev_dt/next_dt are the adjacent dates that actually have an entry in
    day_entries, not necessarily calendar yesterday/tomorrow -- the
    reading plan doesn't cover every single day of the cycle (see
    esword.py's docstring on the 51-week template vs. a leap year's ~55
    weeks), so skipping to the next *covered* date avoids ever linking to
    a row that doesn't exist."""
    parts = [
        f'<p><a class="dict" href="#j Index">Index</a>'
        f' / <a class="dict" href="#j {_month_id(dt)}">{dt.strftime("%B %Y")}</a></p>'
    ]
    weekday = dt.strftime('%A')
    hdate_line = " - ".join(p for p in (weekday, hdate_str) if p)
    prev_link = f'<a class="dict" href="#j {_day_id(prev_dt)}">&laquo;</a>' if prev_dt else ''
    next_link = f'<a class="dict" href="#j {_day_id(next_dt)}">&raquo;</a>' if next_dt else ''
    parts.append(
        f'<p class="day-nav"><span>{prev_link}</span>'
        f'<span class="day-nav-date">{hdate_line}</span><span>{next_link}</span></p>'
    )

    for heading, parashah_name, refs in sections:
        parts.append('<div class="head_info">')
        parts.append(f'<h2>{heading}</h2>')
        translation = parashah_translations.get(parashah_name) if parashah_name else None
        if translation:
            parts.append(f'<p><i>{translation}</i></p>')
        parts.append('</div>')
        links = _ref_links(refs, book_lookup, unresolved)
        parts.append("<ol>" + "".join(f"<li>{link}</li>" for link in links) + "</ol>")

    if annotations_for_day:
        parts.append('<div class="observances">')
        for ann in annotations_for_day:
            cls = _annotation_class(ann)
            label = ann["title"]
            if ann["yomtov"]:
                label += " (Yom Tov — no work)"
            parts.append(f'<p class="{cls}">{label}')
            if ann["memo"]:
                parts.append(f"<br><i>{ann['memo']}</i>")
            parts.append("</p>")
        parts.append("</div>")

    return "".join(parts)


def render_month_page(year, month, day_entries, annotations, prev_id=None, next_id=None):
    """Build one month's calendar-table content: a Sunday-first grid
    linking each covered day to its own page, blank (unlinked) cells for
    days this cycle has no reading for -- the cycle's first/last partial
    week, since a Simchat-Torah-to-Simchat-Torah cycle rarely starts or
    ends on a calendar month boundary -- plus links back to Index and to
    the adjacent month, when one actually exists in this cycle -- the
    cycle's first and last months have no prev/next, so that link is
    left out, but its <span> stays (empty) so the other link doesn't
    collapse into its slot: with justify-content:space-between, a lone
    flex child sits at the LEFT edge regardless of which one it is, so
    an next-only first month would otherwise render its "next" link on
    the left where "prev" belongs, still pointing right -- confusing.

    A day cell gets the same major-holiday/minor-holiday/fast-day class
    render_day_page()'s own annotations use (see _day_class()) whenever
    that date carries a Hebcal annotation, whether or not it also has a
    reading -- a date can have an annotation with no day_entries row at
    all (e.g. an annotation landing in the reading plan's uncovered
    tail, see esword.py's docstring on the 51-week template vs. a leap
    year's ~55 weeks), so the class check is independent of the
    linked/unlinked check right below it."""
    parts = ['<p><a class="dict" href="#j Index">Index</a></p>']

    prev_link = f'<a class="dict" href="#j {prev_id}">&laquo; {prev_id}</a>' if prev_id else ''
    next_link = f'<a class="dict" href="#j {next_id}">{next_id} &raquo;</a>' if next_id else ''
    parts.append(f'<p class="cal-nav"><span>{prev_link}</span><span>{next_link}</span></p>')

    parts.append('<table class="cal"><tr>')
    parts += [f'<th>{d}</th>' for d in ('Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat')]
    parts.append('</tr>')

    cal = calendar.Calendar(firstweekday=6)  # weeks start Sunday
    for week in cal.monthdayscalendar(year, month):
        parts.append('<tr>')
        for day in week:
            if day == 0:
                parts.append('<td class="pad"></td>')
                continue
            dt = date(year, month, day)
            cls = _day_class(dt, annotations)
            cls_attr = f' class="{cls}"' if cls else ''
            if dt in day_entries:
                parts.append(f'<td{cls_attr}><a class="dict" href="#j {_day_id(dt)}">{day}</a></td>')
            else:
                parts.append(f'<td{cls_attr}>{day}</td>')
        parts.append('</tr>')
    parts.append('</table>')
    return "".join(parts)


def render_index_page(month_ids):
    """Build the Index page: an ordered list of month links in
    chronological cycle order -- not necessarily Jan-Dec, since the
    cycle starts near Simchat Torah, whichever Gregorian month that
    falls in for the given hebrew_year."""
    parts = ['<ol>']
    for mid in month_ids:
        parts.append(f'<li><a class="dict" href="#j {mid}">{mid}</a></li>')
    parts.append('</ol>')
    return "".join(parts)


def generate_journal(reading_plan_path, hebrew_year, output_path,
                      title, abbreviation, description, author="",
                      first_week_name="Bereshit", parashah_translations_path=None):
    """
    Mirrors esword.generate_devi()'s inputs -- see that docstring for
    hebrew_year/parashah_translations_path (no table_db here: this
    format needs no bsb_tables.db lookup at all, see this module's
    docstring). Builds a MySword Journal-format reference book instead
    of an e-Sword .devi: one 'Index' row, one row per real-date-qualified
    month ("October 2026"), and one row per real reading date -- see
    this module's docstring for why no Month/Day merging is needed here
    the way it is for e-Sword.
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

    parashah_translations_path = Path(parashah_translations_path) if parashah_translations_path \
        else Path(__file__).parent.parent / "data" / "parashah_translations.json"
    with open(parashah_translations_path, encoding="utf-8") as f:
        parashah_translations = json.load(f)
    missing_translations = sorted({wk["name"] for wk in weeks.values()} - set(parashah_translations))
    if missing_translations:
        print(f"WARNING: {len(missing_translations)} week name(s) have no entry in "
              f"{parashah_translations_path.name}: {missing_translations}")

    if os.path.exists(output_path):
        os.remove(output_path)
    conn = sqlite3.connect(output_path)
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE details(name TEXT, title TEXT, abbreviation TEXT, author TEXT, "
        "description TEXT, comments TEXT, version TEXT, versiondate DATETIME, "
        "publishdate TEXT, publisher TEXT, creator TEXT, source TEXT, language NVARCHAR(3), "
        "readonly BOOL, customcss TEXT, righttoleft INT default 0)"
    )
    cur.execute(
        "CREATE TABLE journal(rowid INTEGER primary key autoincrement, id TEXT collate nocase, "
        "title TEXT collate nocase, date DATETIME, tags TEXT, content TEXT, "
        "relativeorder INT default 0, hidden INT default 0)"
    )
    cur.execute("CREATE UNIQUE INDEX idx_journal_id on journal(id)")
    cur.execute("CREATE UNIQUE INDEX idx_journal_title on journal(title)")
    cur.execute("CREATE INDEX idx_journal_date on journal(date)")

    cur.execute(
        "INSERT INTO details (name, title, abbreviation, author, description, version, "
        "language, readonly, customcss, righttoleft) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (abbreviation, title, abbreviation, author, description, "1.0", "eng", 1, _CUSTOM_CSS, 0),
    )

    order = 0

    def insert_row(row_id, row_title, content, row_date=None):
        nonlocal order
        order += 1
        cur.execute(
            "INSERT INTO journal (id, title, date, content, relativeorder, hidden) "
            "VALUES (?,?,?,?,?,0)",
            (row_id, row_title, row_date, content, order),
        )

    month_keys = sorted({(dt.year, dt.month) for dt in day_entries})
    month_ids = [_month_id(date(y, m, 1)) for y, m in month_keys]

    insert_row("Index", "Index", render_index_page(month_ids))

    for i, (y, m) in enumerate(month_keys):
        mid = month_ids[i]
        prev_id = month_ids[i - 1] if i > 0 else None
        next_id = month_ids[i + 1] if i < len(month_ids) - 1 else None
        insert_row(mid, mid, render_month_page(y, m, day_entries, annotations, prev_id, next_id))

    missing_hdate = []
    sorted_dates = sorted(day_entries)
    for i, dt in enumerate(sorted_dates):
        hd = hdates.get(dt)
        if hd is None:
            missing_hdate.append(dt)
        prev_dt = sorted_dates[i - 1] if i > 0 else None
        next_dt = sorted_dates[i + 1] if i < len(sorted_dates) - 1 else None
        html = render_day_page(dt, day_entries[dt], annotations.get(dt, []), book_lookup,
                                unresolved_refs, parashah_translations, hd, prev_dt, next_dt)
        did = _day_id(dt)
        insert_row(did, did, html, row_date=dt.isoformat())

    conn.commit()
    conn.close()

    if missing_hdate:
        print(f"WARNING: {len(missing_hdate)} day(s) had no hdate from Hebcal "
              f"(check the derived cycle window covers the full range): {missing_hdate[:5]}...")

    if unresolved_refs:
        print(f"WARNING: {len(unresolved_refs)} reference(s) could not be resolved to a "
              f"book abbreviation and were left as plain text (no link): {unresolved_refs}")

    return len(day_entries)


if __name__ == "__main__":
    # @TODO: swap to input parameter
    hebrew_year = 5786
    base_dir = Path(__file__).parent.parent
    output_path = base_dir / "output" / f"mjaa-{hebrew_year}.bok.mybible"
    count = generate_journal(
        reading_plan_path=base_dir / "data" / "parshat.json",
        hebrew_year=hebrew_year,
        output_path=output_path,
        title=f"MJAA Messianic Reading Plan {hebrew_year}",
        abbreviation=f"MJAA {hebrew_year}",
        description=(
            "Messianic Jewish Alliance of America \"Read the Bible in a Year\" plan, "
            f"{hebrew_year} cycle. Weekly Torah/Haftarah portions plus daily OT/NT readings, "
            "keyed to Simchat Torah through Simchat Torah."
        ),
    )
    print(f"Wrote {count} journal day entries to output/{output_path.name}")
