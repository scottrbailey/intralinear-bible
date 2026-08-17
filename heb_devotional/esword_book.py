"""
heb_devotional/esword_book.py

Generates an e-Sword generic "Book"-type module from
heb_devotional.reading_plan's output -- an alternative to esword.py's
Daily Devotional (.devi). Same underlying content, different container:
cross-row linking between .devi's Devotional rows (or between MySword
journal rows) turned out not to be reliably supported by e-Sword's own
generic-book viewer, so this format sidesteps the problem instead of
depending on it -- one Reference row per Gregorian month covered by the
cycle, with a Sunday-first calendar at the top of that row's own Content
and each day's reading in its own subsection further down the SAME row,
linked by ordinary same-document '#anchor' pairs (which only ever need to
resolve within one row's own HTML, unlike a cross-row link). Navigating
from month to month is left entirely to e-Sword's own built-in
chapter-list/prev-next-chapter UI, keyed off this table's `Chapter`
column -- see generate_book()'s docstring for why each Chapter value is
built the way it is.

Book schema (as supplied -- NOT yet confirmed on-device the way esword.py's
.devi schema was; the file extension below is a best-effort guess pending
that confirmation too):
    CREATE TABLE Details (Title NVARCHAR(100), Abbreviation NVARCHAR(50),
                           Information TEXT, Version INT)
    CREATE TABLE Reference (Chapter NVARCHAR(100), Content TEXT)
    CREATE INDEX ChapterIndex ON Reference (Chapter)
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
    _book_name_to_abbrev,
)
from .esword import _ref_tags
from .mysword import _annotation_class, _day_class, _hebrew_dom, _add_lead_in_days


_CSS = (
    '<style>'
    '.head-info {min-width:100%; background-color:#F2F7F8; padding:4px; margin:4px 0;} '
    '.head-info * {display:block; width:100%; text-align:center;} '
    '.cal {width:100%; table-layout:fixed; border-collapse:collapse; text-align:center;} '
    '.cal th, .cal td {border:1px solid #ccc; padding:4px; position:relative;} '
    '.cal td.pad {border:none;} '
    '.hday {position:absolute; top:1px; right:2px; font-size:0.6em; opacity:0.6; line-height:1;} '
    '.topcal {font-size:0.85em;} '
    '.yom-tov {background-color:#FFEB3B; padding:4px;} '
    '.major-holiday {background-color:#FFF9B0; padding:4px;} '
    '.minor-holiday {background-color:#FFE5B4; padding:4px;} '
    '.fast-day {background-color:#C8A27A; padding:4px;}'
    '</style>'
)


def render_calendar(year, month, day_entries, annotations, hdates):
    """Sunday-first calendar table, id="cal" so each day section below can
    link straight back up to it. A day with an entry links down to its own
    '#d<day>' section (see render_day_section()); a day with no entry (the
    reading plan's uncovered stretches -- see esword.py's docstring on the
    51-week template vs. a leap year's ~55 weeks) is plain text, same as
    mysword.render_month_page()'s unlinked cells. Annotation styling
    (_day_class) and the Hebrew day-of-month corner label (_hebrew_dom) are
    reused as-is from mysword.py rather than reimplemented."""
    parts = ['<table id="cal" class="cal"><tr>']
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
            hday = _hebrew_dom(hdates.get(dt))
            hday_html = f'<span class="hday">{hday}</span>' if hday else ''
            if dt in day_entries:
                parts.append(f'<td{cls_attr}>{hday_html}<a href="#d{day}">{day}</a></td>')
            else:
                parts.append(f'<td{cls_attr}>{hday_html}{day}</td>')
        parts.append('</tr>')
    parts.append('</table>')
    return "".join(parts)


def render_day_section(dt, sections, annotations_for_day, book_lookup, verses_conn,
                        unresolved, missing_bounds, parashah_translations, hdate_str):
    """One day's content, wrapped in id="d<day>" so the calendar (and this
    section's own back-link) can jump to/from it by same-document anchor.
    `sections` is day_entries[dt] -- already scoped to this one real date,
    so unlike esword.render_devotion_html() (which merges colliding
    Month/Day slots across leap-year Gregorian years) there's never more
    than one date's material here; every Reference row is its own real
    Gregorian month, so that collision can't happen in this format."""
    parts = [f'<div id="d{dt.day}">']
    parts.append('<p><a class="topcal" href="#cal">&uarr; Calendar</a></p>')

    weekday = dt.strftime('%A')
    hdate_line = " - ".join(p for p in (weekday, hdate_str) if p)
    parts.append(f'<div class="head-info"><p>{hdate_line}</p></div>')

    for heading, parashah_name, refs in sections:
        parts.append(f'<h2>{heading}</h2>')
        translation = parashah_translations.get(parashah_name) if parashah_name else None
        if translation:
            parts.append(f'<p><i>{translation}</i></p>')
        tags = _ref_tags(refs, book_lookup, verses_conn, unresolved, missing_bounds)
        if tags:
            parts.append("<ol>" + "".join(f"<li>{tag}</li>" for tag in tags) + "</ol>")

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

    parts.append('</div>')
    return "".join(parts)


def render_month_chapter(year, month, day_entries, annotations, hdates, book_lookup,
                          verses_conn, unresolved, missing_bounds, parashah_translations):
    """One Reference row's full Content: CSS (no CustomCSS column in this
    schema, so -- same reasoning as esword.py's .devi -- it's repeated
    inline per row instead of declared once), the month's calendar, then
    every covered day in that month as its own back-linked subsection."""
    parts = [_CSS, render_calendar(year, month, day_entries, annotations, hdates)]
    month_dates = sorted(dt for dt in day_entries if dt.year == year and dt.month == month)
    for dt in month_dates:
        parts.append(render_day_section(
            dt, day_entries[dt], annotations.get(dt, []), book_lookup, verses_conn,
            unresolved, missing_bounds, parashah_translations, hdates.get(dt),
        ))
    return "".join(parts)


def generate_book(reading_plan_path, hebrew_year, output_path,
                   title, abbreviation, information, first_week_name="Bereshit",
                   table_db=None, parashah_translations_path=None):
    """
    Mirrors esword.generate_devi()'s inputs -- see that docstring for
    table_db/parashah_translations_path. Fetch window and lead-in days
    mirror mysword.generate_journal() instead (start="rosh_hashanah" +
    _add_lead_in_days()) so the Fall holidays (Rosh Hashanah, Yom Kippur,
    Sukkot), which precede the reading plan's own Bereshit week, still get
    a calendar month and a day section rather than being invisible.

    One Reference row per Gregorian month the cycle touches. `Chapter` is
    built as "<year>-<month> <Month Name> <year>" (e.g. "2026-10 October
    2026") rather than just "October 2026": e-Sword's own chapter-list/
    prev-next navigation may sort or key off `Chapter` (it carries an
    index, same shape as a Dictionary's alphabetized Topic column) rather
    than insertion order, and a Simchat-Torah-to-Simchat-Torah cycle spans
    a Gregorian year boundary -- plain month names ("September" <
    "October" alphabetically, wrong order for reading Sept THEN Oct of a
    LATER cycle year) would sort wrong. The zero-padded "YYYY-MM" prefix
    sorts correctly as plain text either way, and rows are also inserted
    in chronological order regardless.
    """
    weeks = load_reading_plan(reading_plan_path)
    num_weeks = len(weeks)
    h_labels = {wk["H"]["label"] for wk in weeks.values() if wk["H"]}

    rosh_hashanah, cycle_start, cycle_end = find_cycle_window(hebrew_year, start="rosh_hashanah")
    hebcal_json = fetch_hebcal(rosh_hashanah, cycle_end, hebrew_year)

    week_saturday = derive_week_saturdays(hebcal_json, first_week_name, num_weeks, weeks=weeks)
    holiday_date = derive_holiday_dates(hebcal_json, h_labels, min_date=cycle_start)

    annotations, hdates = process_hebcal_data(hebcal_json)
    day_entries = build_day_entries(weeks, week_saturday, holiday_date)
    _add_lead_in_days(day_entries, rosh_hashanah)
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
    cur.execute('CREATE TABLE Details (Title NVARCHAR(100), Abbreviation NVARCHAR(50), Information TEXT, Version INT)')
    cur.execute('CREATE TABLE Reference (Chapter NVARCHAR(100), Content TEXT)')
    cur.execute('CREATE INDEX ChapterIndex ON Reference (Chapter)')

    cur.execute(
        "INSERT INTO Details (Title, Abbreviation, Information, Version) VALUES (?,?,?,?)",
        (title, abbreviation, information, 4),
    )

    month_keys = sorted({(dt.year, dt.month) for dt in day_entries})
    for y, m in month_keys:
        content = render_month_chapter(y, m, day_entries, annotations, hdates, book_lookup,
                                        verses_conn, unresolved_refs, missing_bounds,
                                        parashah_translations)
        chapter_key = f"{y:04d}-{m:02d} {date(y, m, 1).strftime('%B %Y')}"
        cur.execute("INSERT INTO Reference (Chapter, Content) VALUES (?,?)", (chapter_key, content))

    conn.commit()
    conn.close()
    if verses_conn is not None:
        verses_conn.close()

    missing_hdate = [dt for dt in day_entries if dt not in hdates]
    if missing_hdate:
        print(f"WARNING: {len(missing_hdate)} day(s) had no hdate from Hebcal "
              f"(check the derived cycle window covers the full range): {sorted(missing_hdate)[:5]}...")

    if unresolved_refs:
        print(f"WARNING: {len(unresolved_refs)} reference(s) could not be resolved to a "
              f"book abbreviation and were left as raw text (broken <ref> links): {unresolved_refs}")

    if missing_bounds:
        print(f"WARNING: {len(missing_bounds)} reference(s) had no verse count available "
              f"and were left chapter-only, which e-Sword's <ref> tag won't resolve: {missing_bounds}")

    return len(day_entries)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate the MJAA e-Sword Book-format devotional module.")
    parser.add_argument("hebrew_year", type=int, nargs="?", default=5786,
                         help="Hebrew year the cycle's Bereshit falls in (default: 5786)")
    hebrew_year = parser.parse_args().hebrew_year

    base_dir = Path(__file__).parent.parent
    # .topx is a best-effort guess at e-Sword's generic-book extension,
    # NOT yet confirmed on-device -- rename (and tell us the real one) if
    # e-Sword doesn't pick this file up as a Book/Reference module.
    output_path = base_dir / "output" / f"mjaa-{hebrew_year}.topx"
    count = generate_book(
        reading_plan_path=base_dir / "data" / "parshat.json",
        hebrew_year=hebrew_year,
        output_path=output_path,
        title=f"MJAA Messianic Reading Plan {hebrew_year}",
        abbreviation=f"MJAA-{hebrew_year}",
        information=(
            "<p>Messianic Jewish Alliance of America \"Read the Bible in a Year\" plan, "
            f"{hebrew_year} cycle. Weekly Torah/Haftarah portions plus daily OT/NT readings, "
            "keyed to Simchat Torah through Simchat Torah. Each chapter is one Gregorian month, "
            "with a calendar of that month at the top and each day's reading linked below it. "
            "Also annotates fasts, Rosh Chodesh, special Shabbatot, and Yom Tov status from "
            "Hebcal, and shows the Hebrew date.</p>"
        ),
    )
    print(f"Wrote {count} day(s) to output/{output_path.name}")
