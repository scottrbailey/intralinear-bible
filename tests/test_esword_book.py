"""
Tests for the e-Sword Book-format devotional's stylesheet.
Run with: pytest tests/test_esword_book.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from heb_devotional.esword_book import _CSS  # noqa: E402

_SNAP_ON = 'html, body {scroll-snap-type:y mandatory;}'
_IOS_OFF = '@supports (-webkit-touch-callout: none) {html, body {scroll-snap-type:none;}}'


def test_scroll_snap_disabled_on_ios() -> None:
    """Regression: on iOS, mandatory scroll-snap re-snapped every
    calendar<->day anchor jump back to the last hand-scrolled section, so
    the links looked dead until e-Sword was restarted. The iOS-only
    override must exist and come after the rule it overrides."""
    assert _SNAP_ON in _CSS
    assert _IOS_OFF in _CSS
    assert _CSS.index(_IOS_OFF) > _CSS.index(_SNAP_ON)
