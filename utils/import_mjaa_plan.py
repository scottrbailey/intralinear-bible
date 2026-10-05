"""
import_mjaa_plan.py

Build one Hebrew year's reading-plan JSON (data/parshat-<year>.json, the
parshat.json shape heb_devotional.reading_plan.load_reading_plan() reads:
a list of {week_no, week, type: D|W|H, refs: [...], label?}) from a saved
copy of MJAA's "Bible Reading Plan" web page
(https://mjaa.org/bible-in-a-year/, File > Save Page As... HTML).

Why the web page and not the PDF: the PDF is an Adobe Illustrator export
with every glyph converted to outlines -- there's no text layer to
extract. The PDF is still the source of truth, though (it's what MJAA's
readers actually print), and the web page is a hand-pasted copy of it
with transcription damage (garbled haftarot, a dropped weekday row, a
missing holiday-readings box). So each year's import is:

    1. parse the page's week cards (one <section class="mjaa-week-card">
       per week, five Sun-Thu rows plus a Friday Torah/Haftarah row),
    2. apply that year's CORRECTIONS -- every place the page disagrees
       with the PDF, checked by hand against the PDF -- and add the
       PDF-only "*Holiday Readings" box as H rows,
    3. write the JSON.

CORRECTIONS deliberately only reconciles page -> PDF. Where the PDF
itself is wrong (5787: Ha'azinu's Torah portion printed as Deuteronomy
31:1-31:30, Shabbat Shuva getting the fast-day haftarah, Shmini Atzeret
getting Simchat Torah's haftarah), the PDF wins anyway: the point is to
match what everyone reading MJAA's printed plan sees.

Each year's cycle runs Bereshit through Shmini Atzeret. The page's first
card is the previous cycle's closing Shmini Atzeret week (a lead-in); it
and anything else before the first Bereshit card is skipped.

Usage:
    python utils/import_mjaa_plan.py PAGE.html HEBREW_YEAR [--output FILE]
"""

import argparse
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_PATH = ROOT / "data" / "parashah_translations.json"

# The page's own week titles -> the names reading_plan.json uses (Hebcal's
# bare titles, which derive_week_saturdays() cross-checks against, and the
# keys of data/parashah_translations.json). Anything not listed here only
# has its "Parshat "/"Parashat " prefix stripped and curly apostrophes
# straightened.
WEEK_NAME_FIXES = {
    "Beschalach": "Beshalach",
    "PESACH": "Pesach",
    "SHAVUOT": "Shavuot",
    "ROSH HASHANAH": "Rosh Hashana",
    "Ha'Azinu": "Ha'azinu",
    "SUKKOT": "Sukkot",
    "SHIMINI ATZERET": "Shmini Atzeret",
}

# Abbreviated or misspelled book names the page uses -> the full names
# reading_plan._book_name_to_abbrev() resolves.
BOOK_NAME_FIXES = {
    "Deut": "Deuteronomy",
    "Numb": "Numbers",
    "Num": "Numbers",
    "Songs of Songs": "Song of Songs",
}

# Per-year page -> PDF reconciliation (see module docstring). Keyed by
# normalized week name (after WEEK_NAME_FIXES):
#   days:     {(week, day_index 0-4): (ot_or_None, nt_or_None)} -- None
#             keeps the page's value
#   weekly:   {week: [refs]} -- replaces the Friday Torah/Haftarah refs
#   holidays: [(week, label, [refs])] -- the PDF's "*Holiday Readings"
#             box, attached to the week the PDF marks it in. label must
#             match Hebcal's own title (see derive_holiday_dates()).
CORRECTIONS = {
    5787: {
        "days": {
            # Page drops Monday entirely and prints Tuesday twice.
            ("Rosh Hashana", 1): ("I Chronicles 16-17", "Revelation 4"),
            # Page shows only "21" for the NT reading.
            ("Nitzavim-Vayeilech", 1): (None, "3 John 1"),
        },
        "weekly": {
            # Page splits the haftarah's first half into the Torah line.
            "Shemot": ["Exodus 1:1-6:1", "Isaiah 27:6-28:13, 29:22-23"],
            # Page has the next card's header pasted into the haftarah.
            "Eikev": ["Deuteronomy 7:12-11:25", "Isaiah 49:14-51:3"],
            "Re'eh": ["Deuteronomy 11:26-16:17", "Isaiah 54:11-55:5"],
        },
        "holidays": [
            ("Bereshit", "Simchat Torah",
             ["Deuteronomy 33:1-34:12", "Numbers 29:35-30:1", "Joshua 1:1-18"]),
            ("Tzav", "Purim", ["Exodus 17:8-16", "Esther 1-10"]),
            ("Sukkot", "Yom Kippur",
             ["Leviticus 16:1-34", "Numbers 29:7-11", "Isaiah 57:14-58:14"]),
        ],
    },
}

_CARD_RE = re.compile(r'<section class="mjaa-week-card">(.*?)</section>', re.S)
_PARSHA_RE = re.compile(r'class="mjaa-week-parsha">\s*<span>(.*?)</span>', re.S)
_ROW_RE = re.compile(r'<tr>(.*?)</tr>', re.S)
_DOW_RE = re.compile(r'class="mjaa-date-dow">(.*?)<', re.S)
_CELL_RE = re.compile(r'<td class="mjaa-reading-cell[^"]*">(.*?)</td>', re.S)
_SPAN_RE = re.compile(r'<span>(.*?)</span>', re.S)
_LABEL_RE = re.compile(r'^(?:Torah(?: Portion)?|Haftarah)\s*:\s*')


def _text(fragment: str) -> str:
    """Joined text of a reading cell's plain <span>s (skipping the
    decorative star span, which carries a class attribute)."""
    return " ".join(html.unescape(s).strip() for s in _SPAN_RE.findall(fragment)).strip()


def normalize_week_name(raw: str) -> str:
    """'Parshat Re’eh' -> "Re'eh", 'SHIMINI ATZERET' -> 'Shmini Atzeret'."""
    name = raw.replace("’", "'").strip()
    for prefix in ("Parshat ", "Parashat "):
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
    return WEEK_NAME_FIXES.get(name, name)


def normalize_ref(raw: str) -> str:
    """One reference: straighten dashes, expand abbreviated book names."""
    ref = re.sub(r'\s*[–—]\s*', '-', raw.strip())
    for bad, good in BOOK_NAME_FIXES.items():
        if re.match(rf'{re.escape(bad)}\s+\d', ref):
            ref = good + ref[len(bad):]
            break
    return ref


def split_refs(line: str) -> list:
    """A Torah/Haftarah line -> list of reference strings. Semicolons
    separate references; a segment with no book name of its own
    ('Isaiah 6:1-7:6; 9:5-6') continues the previous one and is re-joined
    with a comma -- parshat.json's own convention for a compound
    reference, see reading_plan._ref_to_tag()."""
    refs = []
    for seg in (s.strip() for s in _LABEL_RE.sub('', line.strip()).split(';')):
        if not seg:
            continue
        if seg[0].isdigit() and refs:
            refs[-1] = f"{refs[-1]}, {normalize_ref(seg)}"
        else:
            refs.append(normalize_ref(seg))
    return refs


def parse_page(page_html: str) -> list:
    """Every week card on the page, in order, as
    {"week": name, "days": [(ot, nt), ...], "weekly": [refs]}. "days"
    holds each non-Friday row as printed (normally five); "weekly" is the
    Friday row's Torah + Haftarah refs."""
    weeks = []
    for card in _CARD_RE.findall(page_html):
        m = _PARSHA_RE.search(card)
        if not m:
            continue
        week = {"week": normalize_week_name(html.unescape(m.group(1))), "days": [], "weekly": []}
        for row in _ROW_RE.findall(card):
            dow = _DOW_RE.search(row)
            cells = [_text(c) for c in _CELL_RE.findall(row)]
            if not dow or not cells:
                continue
            # The Torah/Haftarah row is normally "Fri", but the page
            # sometimes labels it "Sat" (5787's Matot-Masei).
            if dow.group(1).strip() in ("Fri", "Sat"):
                week["weekly"] = [r for c in cells if c for r in split_refs(c)]
            else:
                ot = normalize_ref(cells[0]) if cells[0] else ""
                nt = normalize_ref(cells[1]) if len(cells) > 1 and cells[1] else ""
                week["days"].append((ot, nt))
        weeks.append(week)
    return weeks


def build_plan(page_weeks: list, corrections: dict, first_week_name: str = "Bereshit") -> list:
    """Page weeks (from parse_page) + one year's CORRECTIONS entry -> the
    reading-plan record list. Drops every card before the first
    first_week_name card (the previous cycle's lead-in week).

    Raises ValueError if, after corrections, any week doesn't have
    exactly five complete daily readings and a Torah/Haftarah row, or if
    a correction names a week the page doesn't have -- both mean the page
    changed shape and the corrections need another look against the PDF.
    """
    names = [w["week"] for w in page_weeks]
    if first_week_name not in names:
        raise ValueError(f"No {first_week_name!r} week found on the page")
    cycle = page_weeks[names.index(first_week_name):]
    cycle_names = {w["week"] for w in cycle}

    referenced = ({wk for wk, _ in corrections.get("days", {})}
                  | set(corrections.get("weekly", {}))
                  | {wk for wk, _, _ in corrections.get("holidays", [])})
    unknown = referenced - cycle_names
    if unknown:
        raise ValueError(f"Corrections name weeks not on the page: {sorted(unknown)}")

    records = []
    for week_no, wk in enumerate(cycle, start=1):
        name = wk["week"]
        days = list(wk["days"])
        for (c_week, idx), (ot, nt) in corrections.get("days", {}).items():
            if c_week != name:
                continue
            while len(days) <= idx:
                days.append(("", ""))
            days[idx] = (ot if ot is not None else days[idx][0],
                         nt if nt is not None else days[idx][1])
        weekly = corrections.get("weekly", {}).get(name, wk["weekly"])

        if len(days) != 5 or not all(ot and nt for ot, nt in days) or not weekly:
            raise ValueError(f"Week {week_no} ({name}) is incomplete after corrections: "
                             f"days={days!r} weekly={weekly!r}")

        for ot, nt in days:
            records.append({"week_no": week_no, "week": name, "type": "D", "refs": [ot, nt]})
        records.append({"week_no": week_no, "week": name, "type": "W", "refs": weekly})
        for h_week, label, refs in corrections.get("holidays", []):
            if h_week == name:
                records.append({"week_no": week_no, "week": name, "type": "H",
                                "label": label, "refs": refs})
    return records


def main(page_path: Path, hebrew_year: int, output_path: Path) -> None:
    """Import one year's saved MJAA page to output_path."""
    if hebrew_year not in CORRECTIONS:
        raise SystemExit(
            f"No CORRECTIONS entry for {hebrew_year}. Check the page against that year's PDF "
            f"and add one (an empty dict if the page is clean) before importing.")
    records = build_plan(parse_page(page_path.read_text(encoding="utf-8")), CORRECTIONS[hebrew_year])

    with open(TRANSLATIONS_PATH, encoding="utf-8") as f:
        translations = json.load(f)
    missing = sorted({r["week"] for r in records} - set(translations))
    if missing:
        print(f"WARNING: no {TRANSLATIONS_PATH.name} entry for: {missing}")

    output_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    num_weeks = records[-1]["week_no"]
    print(f"Wrote {num_weeks} weeks ({len(records)} rows) to {output_path}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Import MJAA's reading-plan web page to JSON.")
    parser.add_argument('page', type=Path, help="Saved copy of MJAA's Bible Reading Plan page (.html)")
    parser.add_argument('hebrew_year', type=int, help="Hebrew year the cycle's Bereshit falls in, e.g. 5787")
    parser.add_argument('--output', type=Path, default=None,
                        help="Output JSON path (default: data/parshat-<year>.json)")
    args = parser.parse_args()
    main(args.page, args.hebrew_year, args.output or ROOT / "data" / f"parshat-{args.hebrew_year}.json")
