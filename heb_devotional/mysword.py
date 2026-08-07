"""
heb_devotional/mysword.py

Generates a MySword Journal-format devotional module from
heb_devotional.reading_plan's output -- see reading_plan.py for how
{date: [(heading, parashah_name, refs), ...]}, {date: hdate_string}, and
{date: [annotation, ...]} are derived from parshat.json + a live Hebcal
fetch. This module only owns MySword-journal-specific rendering and
SQLite writing.

journal schema (confirmed against a real MySword reference-book module,
Morning and Evening: Daily Readings):
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
bundled/imported module.

Unlike e-Sword's Devotional table (Month/Day only, no year -- see
esword.py's docstring for the leap-year collision that forces), journal
rows are keyed by arbitrary unique id/title strings and carry a real
DATETIME. Every id here bakes in the Gregorian year (e.g. "15 Oct 2026"
for a day row, "October 2026" for a month row), so the same leap-year
Fall-straddles-two-Decembers collision that required merging rows for
e-Sword simply never happens here -- every real date gets its own row.

Navigation is Index -> month page (a real calendar table) -> day page,
each a real '#j <id>' journal link (MySword's own link-type prefix, "j"
for journal same as "b" for bible -- see
https://www.mysword.info/modules-format). Bible references link via a
'#b<book_num>.<chapter>.<verse>' anchor instead of e-Sword's <ref> tag --
same Reference data from reading_plan.resolve_refs(), different syntax,
since MySword needs an explicit address separate from the visible label
rather than e-Sword's label-is-the-address <ref> tag.

details.customcss is loaded by MySword automatically, so unlike
e-Sword's .devi (no CustomCSS column at all -- every row there repeats
its own inline <style> block) the CSS below is declared exactly once.
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
    _book_name_to_abbrev, resolve_refs,
)
from verse_formatter.base import ABBREV_TO_BOOK_NUM, _default_ref_label


_CUSTOM_CSS = (
    ".head_info {min-width:100%; background-color:#F2F7F8;} "
    ".head_info * {display:block; width:100%; text-align:center;} "
    ".cal {width:100%; border-collapse:collapse; text-align:center;} "
    ".cal th, .cal td {border:1px solid #ccc; padding:4px;} "
    ".cal td.pad {border:none;}"
)


def _month_id(dt: date) -> str:
    return dt.strftime('%B %Y')


def _day_id(dt: date) -> str:
    return dt.strftime('%d %b %Y')


def _ref_links(refs, book_lookup, verses_conn, unresolved, missing_bounds):
    """Resolved Reference objects -> MySword bible-link anchors
    ('<a class="bible" href="#b<book_num>.<chapter>.<verse>">label</a>').
    A label-only Reference (unresolvable book/shape -- see
    reading_plan._ref_to_tag()) renders as plain text, same fallback
    verse_formatter.base._MySwordXrefMixin.transform_reference() uses for
    a Reference with no book/chapter. A resolved reference with no verse
    bounds (missing_bounds -- see reading_plan._chapter_split_refs())
    still links, just to chapter/verse 1 instead of a precise range --
    same "give a real click target either way" reasoning
    _MySwordXrefMixin already uses."""
    resolved = resolve_refs(refs, book_lookup, verses_conn, unresolved, missing_bounds)
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
        verse = ref.verse if ref.verse is not None else 1
        loc = f"{book_num}.{ref.chapter}.{verse}"
        if ref.verse is not None and ref.end_verse:
            loc += f"-{ref.end_verse}"
        links.append(f'<a class="bible" href="#b{loc}">{label}</a>')
    return links


def render_day_page(dt, sections, annotations_for_day, book_lookup, verses_conn,
                     unresolved, missing_bounds, parashah_translations, hdate_str):
    """Build one day page's content. `sections` is day_entries[dt]:
    [(heading, parashah_name, refs), ...], already scoped to this single
    real date -- unlike e-Sword's render_devotion_html(), no cross-date
    merging is ever needed here (see this module's docstring)."""
    parts = [
        f'<p>Go To <a class="dict" href="#j {_month_id(dt)}">{dt.strftime("%B %Y")}</a>'
        f' / <a class="dict" href="#j Index">Index</a></p>'
    ]
    weekday = dt.strftime('%A')
    hdate_line = " - ".join(p for p in (weekday, hdate_str) if p)

    for heading, parashah_name, refs in sections:
        parts.append('<div class="head_info">')
        parts.append(f'<p>{hdate_line}</p>')
        parts.append(f'<h2>{heading}</h2>')
        translation = parashah_translations.get(parashah_name) if parashah_name else None
        if translation:
            parts.append(f'<p><i>{translation}</i></p>')
        parts.append('</div>')
        links = _ref_links(refs, book_lookup, verses_conn, unresolved, missing_bounds)
        parts.append("<ol>" + "".join(f"<li>{link}</li>" for link in links) + "</ol>")

    if annotations_for_day:
        parts.append('<div class="observances">')
        for ann in annotations_for_day:
            label = ann["title"]
            if ann["yomtov"]:
                label = f"<b>{label} (Yom Tov — no work)</b>"
            parts.append(f"<p>{label}")
            if ann["memo"]:
                parts.append(f"<br><i>{ann['memo']}</i>")
            parts.append("</p>")
        parts.append("</div>")

    return "".join(parts)


def render_month_page(year, month, day_entries):
    """Build one month's calendar-table content: a Sunday-first grid
    linking each covered day to its own page, blank (unlinked) cells for
    days this cycle has no reading for -- the cycle's first/last partial
    week, since a Simchat-Torah-to-Simchat-Torah cycle rarely starts or
    ends on a calendar month boundary -- plus a link back to Index."""
    month_name = calendar.month_name[month]
    parts = [
        '<p><a class="dict" href="#j Index">Index</a></p>',
        f'<h2>{month_name} {year}</h2>',
        '<table class="cal"><tr>',
    ]
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
            if dt in day_entries:
                parts.append(f'<td><a class="dict" href="#j {_day_id(dt)}">{day}</a></td>')
            else:
                parts.append(f'<td>{day}</td>')
        parts.append('</tr>')
    parts.append('</table>')
    return "".join(parts)


def render_index_page(month_ids):
    """Build the Index page: an ordered list of month links in
    chronological cycle order -- not necessarily Jan-Dec, since the
    cycle starts near Simchat Torah, whichever Gregorian month that
    falls in for the given hebrew_year."""
    parts = ['<h2>Index</h2>', '<ol>']
    for mid in month_ids:
        parts.append(f'<li><a class="dict" href="#j {mid}">{mid}</a></li>')
    parts.append('</ol>')
    return "".join(parts)


def generate_journal(reading_plan_path, hebrew_year, output_path,
                      title, abbreviation, description, author="",
                      first_week_name="Bereshit", table_db=None,
                      parashah_translations_path=None):
    """
    Mirrors esword.generate_devi()'s inputs -- see that docstring for
    hebrew_year/table_db/parashah_translations_path. Builds a MySword
    Journal-format reference book instead of an e-Sword .devi: one
    'Index' row, one row per real-date-qualified month ("October 2026"),
    and one row per real reading date -- see this module's docstring for
    why no Month/Day merging is needed here the way it is for e-Sword.
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
        print(f"WARNING: {table_db} not found -- bare 'book chapter' references will "
              f"link to chapter/verse 1 instead of a precise range. Build it with "
              f"utils/import_bsb_table.py first.")

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

    for y, m in month_keys:
        mid = _month_id(date(y, m, 1))
        insert_row(mid, mid, render_month_page(y, m, day_entries))

    missing_hdate = []
    for dt in sorted(day_entries):
        hd = hdates.get(dt)
        if hd is None:
            missing_hdate.append(dt)
        html = render_day_page(dt, day_entries[dt], annotations.get(dt, []), book_lookup,
                                verses_conn, unresolved_refs, missing_bounds,
                                parashah_translations, hd)
        did = _day_id(dt)
        insert_row(did, did, html, row_date=dt.isoformat())

    conn.commit()
    conn.close()
    if verses_conn is not None:
        verses_conn.close()

    if missing_hdate:
        print(f"WARNING: {len(missing_hdate)} day(s) had no hdate from Hebcal "
              f"(check the derived cycle window covers the full range): {missing_hdate[:5]}...")

    if unresolved_refs:
        print(f"WARNING: {len(unresolved_refs)} reference(s) could not be resolved to a "
              f"book abbreviation and were left as plain text (no link): {unresolved_refs}")

    if missing_bounds:
        print(f"WARNING: {len(missing_bounds)} reference(s) had no verse count available "
              f"and link to chapter/verse 1 instead of a precise range: {missing_bounds}")

    return len(day_entries)


if __name__ == "__main__":
    # @TODO: swap to input parameter; confirm the real file extension
    # MySword expects for a Journal-format reference book before
    # distributing this (placeholder below, unverified).
    hebrew_year = 5786
    base_dir = Path(__file__).parent.parent
    output_path = base_dir / "output" / f"mjaa-{hebrew_year}.journal.mybible"
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
