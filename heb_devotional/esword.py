"""
heb_devotional/esword.py

Generates an e-Sword Daily Devotional (.devi) module from
heb_devotional.reading_plan's output: {date: [(heading, parashah_name,
refs), ...]}, {date: hdate_string}, and {date: [annotation, ...]} --
see reading_plan.py for how those are derived from parshat.json + a
live Hebcal fetch. This module only owns the .devi-specific rendering
(render_devotion_html) and SQLite writing (generate_devi).

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
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

from .reading_plan import (
    load_reading_plan, find_cycle_window, fetch_hebcal, process_hebcal_data,
    derive_week_saturdays, derive_holiday_dates, build_day_entries,
    _book_name_to_abbrev, resolve_refs,
)
from verse_formatter.base import _default_ref_label


def _ref_tags(refs, book_lookup, verses_conn, unresolved, missing_bounds):
    """Resolved Reference objects -> e-Sword '<ref>...</ref>' tags -- a
    label-only Reference (unresolvable book/shape, see
    reading_plan._ref_to_tag()) wraps its raw text as-is, same fallback
    behavior the old inline '<ref>{text}</ref>' had before this became a
    shared, format-agnostic Reference list."""
    resolved = resolve_refs(refs, book_lookup, verses_conn, unresolved, missing_bounds)
    return [f'<ref>{ref.label}</ref>' if ref.book is None else f'<ref>{_default_ref_label(ref)}</ref>'
            for ref in resolved]


def render_devotion_html(sections, annotations_for_day, book_lookup, verses_conn,
                          unresolved, missing_bounds, parashah_translations, hdates):
    """Build the full Devotion HTML for one Month/Day slot.

    `sections` is a list of (dt, heading, parashah_name, refs) -- each
    entry carries its own originating calendar date rather than one date
    shared by the whole slot, because Devotional is keyed on Month/Day
    only (no year, see generate_devi()'s docstring): a leap Hebrew year's
    Fall months can land the same Month/Day in two different Gregorian
    years (e.g. Oct 15 2026 and Oct 15 2027), and those colliding dates
    are merged into one slot's sections here, each needing its own
    weekday/hdate line rather than a single line for the whole slot.
    hdates: {date: hdate_string}, looked up per section instead of being
    passed in as a single scalar.
    """
    css = '<style>.head_info {min-width:100%; background-color:#F2F7F8;} .head_info * {display:block; width:100%; text-align:center;}</style>'
    parts = [css]
    # Only worth calling out the Gregorian year when this slot actually
    # merges more than one real date (the Oct-15-2026-and-2027 case) --
    # the ordinary one-date-per-slot day shouldn't grow an extra heading.
    multi_year = len({s[0] for s in sections}) > 1

    for dt, heading, parashah_name, refs in sections:
        weekday = dt.strftime('%A')
        hdate_str = hdates.get(dt)
        hdate_line = " - ".join(p for p in (weekday, hdate_str) if p)
        parts.append('<div class="head_info">')
        parts.append(f'<p>{hdate_line}</p>')
        if multi_year:
            parts.append(f'<h2>{dt.year}</h2>')
        parts.append(f'<h2>{heading}</h2>')
        translation = parashah_translations.get(parashah_name) if parashah_name else None
        if translation:
            parts.append(f'<p><i>{translation}</i></p>')
        parts.append('</div>')
        tags = _ref_tags(refs, book_lookup, verses_conn, unresolved, missing_bounds)
        parts.append("<ol>" + "".join(f"<li>{tag}</li>" for tag in tags) + "</ol>")

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
      reading_plan._ref_to_tag()'s docstring for why that's required at
      all. Missing (not yet built) is handled gracefully: those
      references are just left chapter-only and collected into the
      missing_bounds warning, not a hard failure -- same spirit as a
      missing hdate.
    parashah_translations_path: path to parashah_translations.json
      (default data/parashah_translations.json) -- {week name: English
      translation}, one entry per name in reading_plan_path's own "week"
      field. Rendered as its own line under a D/W row's <h2> heading (see
      render_devotion_html()); H rows (holiday labels like "Yom Kippur")
      aren't looked up here -- see reading_plan.build_day_entries()'s
      docstring for why.
    """
    weeks = load_reading_plan(reading_plan_path)
    num_weeks = len(weeks)
    h_labels = {wk["H"]["label"] for wk in weeks.values() if wk["H"]}

    window_start, cycle_start, cycle_end = find_cycle_window(hebrew_year)
    hebcal_json = fetch_hebcal(window_start, cycle_end, hebrew_year)

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

    # Devotional is keyed on Month/Day only (no year -- see this
    # function's docstring), so two real dates that land on the same
    # Month/Day in different Gregorian years (a leap Hebrew year's Fall
    # months straddle two Decembers) must merge into one row here rather
    # than each silently inserting its own same-keyed row. Group first,
    # then render once per Month/Day with every colliding date's sections
    # combined -- render_devotion_html() carries each section's own dt
    # through so it can show that date's own weekday/hdate (and the
    # Gregorian year, when a slot actually has more than one).
    month_day_groups = defaultdict(list)
    for dt in sorted(day_entries):
        month_day_groups[(dt.month, dt.day)].append(dt)

    missing_hdate = []
    for (month, day), dts in sorted(month_day_groups.items()):
        sections = []
        combined_annotations = []
        for dt in dts:
            if dt not in hdates:
                missing_hdate.append(dt)
            sections.extend((dt, heading, parashah_name, refs)
                             for heading, parashah_name, refs in day_entries[dt])
            combined_annotations.extend(annotations.get(dt, []))
        html = render_devotion_html(sections, combined_annotations, book_lookup,
                                     verses_conn, unresolved_refs, missing_bounds,
                                     parashah_translations, hdates)
        cur.execute(
            "INSERT INTO Devotional (Month, Day, Devotion) VALUES (?,?,?)",
            (month, day, html),
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
    import argparse
    parser = argparse.ArgumentParser(description="Generate the MJAA e-Sword Daily Devotional (.devi) module.")
    parser.add_argument("hebrew_year", type=int, nargs="?", default=5786,
                         help="Hebrew year the cycle's Bereshit falls in (default: 5786)")
    hebrew_year = parser.parse_args().hebrew_year

    base_dir = Path(__file__).parent.parent
    output_path = base_dir / "output" / f"mjaa-{hebrew_year}.devi"
    count = generate_devi(
        reading_plan_path=base_dir / "data" / "parshat.json",
        hebrew_year=hebrew_year,
        output_path=output_path,
        title=f"MJAA Messianic Reading Plan {hebrew_year}",
        abbreviation=f"MJAA-{hebrew_year}",
        information=(
            "<p>Messianic Jewish Alliance of America \"Read the Bible in a Year\" plan, "
            f"{hebrew_year} cycle. Weekly Torah/Haftarah portions plus daily OT/NT readings, "
            "keyed to Simchat Torah through Simchat Torah. Also annotates fasts, Rosh Chodesh, "
            "special Shabbatot, and Yom Tov status from Hebcal, and shows the Hebrew date.</p>"
        ),
    )
    print(f"Wrote {count} Devotional rows to output/{output_path.name}")
