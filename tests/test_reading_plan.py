"""
Tests for the Hebrew-calendar devotional reading plan: per-year plan
selection, the plan-vs-cycle week-count check (leap years), the MJAA page
importer, and the committed per-year plan data.
Run with: pytest tests/test_reading_plan.py

No network: Hebcal responses are synthesized from the plan's own week
names, so these exercise the date logic without a live fetch.
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "utils"))

from heb_devotional.reading_plan import (  # noqa: E402
    _book_name_to_abbrev, build_day_entries, check_plan_fits_cycle,
    derive_week_saturdays, load_reading_plan, plan_path_for_year,
    resolve_refs_simple,
)
import import_mjaa_plan as imp  # noqa: E402

DATA = ROOT / "data"

# Cycle windows as find_cycle_window() computes them (Sunday on/before
# Simchat Torah through the Saturday before the next cycle's start).
CYCLE_5786 = (date(2025, 10, 12), date(2026, 10, 3))    # regular year
CYCLE_5787 = (date(2026, 10, 4), date(2027, 10, 23))    # leap year


# ── check_plan_fits_cycle ───────────────────────────────────────────────────

class TestCheckPlanFitsCycle:
    def test_regular_year_51_weeks(self) -> None:
        check_plan_fits_cycle(51, *CYCLE_5786)

    def test_leap_year_55_weeks(self) -> None:
        check_plan_fits_cycle(55, *CYCLE_5787)

    def test_regular_plan_in_leap_year_raises(self) -> None:
        """Regression: a 51-week plan against a 55-week cycle used to slip
        past derive_week_saturdays() and silently mis-date every week."""
        with pytest.raises(ValueError, match="51 weeks .* has 55"):
            check_plan_fits_cycle(51, *CYCLE_5787)

    def test_leap_plan_in_regular_year_raises(self) -> None:
        with pytest.raises(ValueError, match="55 weeks .* has 51"):
            check_plan_fits_cycle(55, *CYCLE_5786)


# ── plan_path_for_year ──────────────────────────────────────────────────────

class TestPlanPathForYear:
    def test_prefers_per_year_file(self, tmp_path: Path) -> None:
        (tmp_path / "parshat.json").write_text("[]")
        (tmp_path / "parshat-5787.json").write_text("[]")
        assert plan_path_for_year(5787, tmp_path) == tmp_path / "parshat-5787.json"

    def test_falls_back_to_default(self, tmp_path: Path) -> None:
        (tmp_path / "parshat.json").write_text("[]")
        assert plan_path_for_year(5786, tmp_path) == tmp_path / "parshat.json"

    def test_repo_5787_plan_is_found(self) -> None:
        assert plan_path_for_year(5787, DATA).name == "parshat-5787.json"


# ── Committed plan data ─────────────────────────────────────────────────────

PLANS = {
    "parshat.json": (51, CYCLE_5786),
    "parshat-5787.json": (55, CYCLE_5787),
}


@pytest.mark.parametrize("filename", sorted(PLANS))
class TestPlanData:
    def test_week_count_fits_cycle(self, filename: str) -> None:
        num_weeks, cycle = PLANS[filename]
        weeks = load_reading_plan(DATA / filename)
        assert len(weeks) == num_weeks
        check_plan_fits_cycle(len(weeks), *cycle)

    def test_every_week_complete(self, filename: str) -> None:
        for wn, wk in load_reading_plan(DATA / filename).items():
            assert len(wk["D"]) == 5, f"week {wn} {wk['name']}"
            assert wk["W"], f"week {wn} {wk['name']}"

    def test_holiday_rows(self, filename: str) -> None:
        weeks = load_reading_plan(DATA / filename)
        labels = {wk["H"]["label"] for wk in weeks.values() if wk["H"]}
        assert labels == {"Simchat Torah", "Purim", "Yom Kippur"}

    def test_every_week_has_translation(self, filename: str) -> None:
        translations = json.loads((DATA / "parashah_translations.json").read_text(encoding="utf-8"))
        names = {wk["name"] for wk in load_reading_plan(DATA / filename).values()}
        assert names - set(translations) == set()

    def test_every_reference_resolves(self, filename: str) -> None:
        book_lookup = _book_name_to_abbrev()
        unresolved: list = []
        for rec in json.loads((DATA / filename).read_text(encoding="utf-8")):
            resolve_refs_simple(rec["refs"], book_lookup, unresolved)
        assert unresolved == []


# ── Leap-year date mapping, end to end (synthetic Hebcal) ───────────────────

# Hebcal titles for the holiday-named weeks, which carry suffixes the
# plan's names don't (see reading_plan.HOLIDAY_NAMED_WEEKS).
_HOLIDAY_TITLES = {
    "Pesach": "Pesach III (CH''M)",
    "Shavuot": "Shavuot II",
    "Rosh Hashana": "Rosh Hashana 5788",
    "Sukkot": "Sukkot I",
    "Shmini Atzeret": "Shmini Atzeret",
}


def _fake_hebcal(weeks: dict, first_saturday: date) -> dict:
    """One primary-reading item per plan week on consecutive Saturdays,
    then the next cycle's Bereshit -- the shape derive_week_saturdays()
    reads."""
    items = []
    for wn in sorted(weeks):
        name = weeks[wn]["name"]
        sat = first_saturday + timedelta(weeks=wn - 1)
        if name in _HOLIDAY_TITLES:
            items.append({"date": sat.isoformat(), "title": _HOLIDAY_TITLES[name],
                          "category": "holiday", "leyning": {"1": "x"}})
        else:
            items.append({"date": sat.isoformat(), "title": f"Parashat {name}",
                          "category": "parashat", "leyning": {"1": "x"}})
    after = first_saturday + timedelta(weeks=len(weeks))
    items.append({"date": after.isoformat(), "title": "Parashat Bereshit",
                  "category": "parashat", "leyning": {"1": "x"}})
    return {"items": items}


@pytest.fixture(scope="module")
def weeks() -> dict:
    """The committed 5787 (leap year) plan, grouped by week."""
    return load_reading_plan(DATA / "parshat-5787.json")


class TestLeapYearDateMapping:
    def test_saturdays_span_whole_cycle(self, weeks: dict, capsys) -> None:
        cycle_start, cycle_end = CYCLE_5787
        hebcal = _fake_hebcal(weeks, cycle_start + timedelta(days=6))
        sats = derive_week_saturdays(hebcal, "Bereshit", len(weeks), weeks=weeks)
        assert sats[1] == "2026-10-10"
        assert sats[55] == cycle_end.isoformat()
        # every week name matched Hebcal's title -- no NOTE lines
        assert "NOTE" not in capsys.readouterr().out

    def test_day_entries_cover_cycle(self, weeks: dict) -> None:
        cycle_start, cycle_end = CYCLE_5787
        sats = {wn: (cycle_start + timedelta(days=6, weeks=wn - 1)).isoformat() for wn in weeks}
        holidays = {"Simchat Torah": "2026-10-04", "Purim": "2027-03-23",
                    "Yom Kippur": "2027-10-11"}
        days = build_day_entries(weeks, sats, holidays)
        assert min(days) == cycle_start
        assert max(days) == cycle_end
        # every day of the cycle has a reading (Sun-Thu daily, Fri/Sat weekly)
        assert len(days) == (cycle_end - cycle_start).days + 1
        assert days[cycle_end][0][1] == "Shmini Atzeret"
        assert ("Purim", None, ["Exodus 17:8-16", "Esther 1-10"]) in days[date(2027, 3, 23)]


# ── MJAA page importer ──────────────────────────────────────────────────────

def _card(title: str, days: list, weekly: tuple, weekly_dow: str = "Fri") -> str:
    """One week card in the MJAA page's markup."""
    def row(dow: str, a: str, b: str) -> str:
        return (f'<tr><td class="mjaa-date-cell"><span class="mjaa-date-day">1</span>'
                f'<span class="mjaa-date-dow">{dow}</span></td>'
                f'<td class="mjaa-reading-cell"><span class="mjaa-mini-star">✡</span><span>{a}</span></td>'
                f'<td class="mjaa-reading-cell mjaa-reading-secondary">'
                f'<span class="mjaa-mini-star">✡</span><span>{b}</span></td></tr>')
    rows = "".join(row(dow, a, b) for dow, (a, b) in zip(["Sun", "Mon", "Tue", "Wed", "Thu"], days))
    rows += row(weekly_dow, *weekly)
    return (f'<section class="mjaa-week-card"><header class="mjaa-week-header">'
            f'<div class="mjaa-week-label">x</div><div class="mjaa-week-parsha">'
            f'<span>{title}</span><span class="mjaa-david">✡</span></div></header>'
            f'<table><tbody>{rows}</tbody></table></section>')


_DAYS = [(f"Joshua {i}", f"Matthew {i}") for i in range(1, 6)]


class TestImporter:
    def test_normalize_week_name(self) -> None:
        assert imp.normalize_week_name("Parshat Re’eh") == "Re'eh"
        assert imp.normalize_week_name("SHIMINI ATZERET") == "Shmini Atzeret"
        assert imp.normalize_week_name("Parshat Beschalach") == "Beshalach"
        assert imp.normalize_week_name("Parashat Noach") == "Noach"

    def test_normalize_ref(self) -> None:
        assert imp.normalize_ref("Joshua 1–3") == "Joshua 1-3"
        assert imp.normalize_ref("Genesis 1:1—6:8") == "Genesis 1:1-6:8"
        assert imp.normalize_ref("Deut 14:22-16:17") == "Deuteronomy 14:22-16:17"
        assert imp.normalize_ref("Numb 28:26–31") == "Numbers 28:26-31"
        assert imp.normalize_ref("Songs of Songs 5–8") == "Song of Songs 5-8"
        # a real book name that merely starts with an alias stays put
        assert imp.normalize_ref("Numbers 1:1-4:20") == "Numbers 1:1-4:20"

    def test_split_refs(self) -> None:
        assert imp.split_refs("Torah Portion: Exodus 33:12-34:26; Numbers 28:19–25") == \
            ["Exodus 33:12-34:26", "Numbers 28:19-25"]
        # book-less segment continues the previous reference
        assert imp.split_refs("Haftarah: Isaiah 6:1-7:6; 9:5–6") == ["Isaiah 6:1-7:6, 9:5-6"]

    def test_parse_and_build_skips_lead_in(self) -> None:
        page = (_card("Parashat Shmini Atzeret", _DAYS, ("Torah: Deut 14:22-16:17", "Haftarah: I Kings 8:54-66"))
                + _card("Parshat Bereshit", _DAYS, ("Torah Portion: Genesis 1:1-6:8", "Haftarah: Isaiah 42:5-43:10"))
                + _card("Parshat Noach", _DAYS, ("Torah: Genesis 6:9-11:32", "Haftarah: Isaiah 54:1-55:5"),
                        weekly_dow="Sat"))
        records = imp.build_plan(imp.parse_page(page), {})
        assert [r["week"] for r in records if r["type"] == "W"] == ["Bereshit", "Noach"]
        assert records[0] == {"week_no": 1, "week": "Bereshit", "type": "D",
                              "refs": ["Joshua 1", "Matthew 1"]}
        noach_w = [r for r in records if r["week"] == "Noach" and r["type"] == "W"][0]
        assert noach_w["refs"] == ["Genesis 6:9-11:32", "Isaiah 54:1-55:5"]

    def test_corrections_applied(self) -> None:
        broken = [("Joshua 1", "Matthew 1"), ("Joshua 2", ""), *_DAYS[2:]]
        page = _card("Parshat Bereshit", broken, ("Torah: Genesis 1:1-6:8", "Haftarah: garbled h Sh fti"))
        corrections = {
            "days": {("Bereshit", 1): (None, "Matthew 2")},
            "weekly": {"Bereshit": ["Genesis 1:1-6:8", "Isaiah 42:5-43:10"]},
            "holidays": [("Bereshit", "Simchat Torah", ["Deuteronomy 33:1-34:12"])],
        }
        records = imp.build_plan(imp.parse_page(page), corrections)
        assert records[1]["refs"] == ["Joshua 2", "Matthew 2"]
        assert records[5]["refs"] == ["Genesis 1:1-6:8", "Isaiah 42:5-43:10"]
        assert records[6] == {"week_no": 1, "week": "Bereshit", "type": "H",
                              "label": "Simchat Torah", "refs": ["Deuteronomy 33:1-34:12"]}

    def test_incomplete_week_raises(self) -> None:
        broken = [("Joshua 1", "Matthew 1"), ("Joshua 2", ""), *_DAYS[2:]]
        page = _card("Parshat Bereshit", broken, ("Torah: Genesis 1:1-6:8", "Haftarah: Isaiah 42:5-43:10"))
        with pytest.raises(ValueError, match="incomplete"):
            imp.build_plan(imp.parse_page(page), {})

    def test_correction_for_unknown_week_raises(self) -> None:
        page = _card("Parshat Bereshit", _DAYS, ("Torah: Genesis 1:1-6:8", "Haftarah: Isaiah 42:5-43:10"))
        with pytest.raises(ValueError, match="not on the page"):
            imp.build_plan(imp.parse_page(page), {"weekly": {"Noach": ["Genesis 6:9-11:32"]}})
