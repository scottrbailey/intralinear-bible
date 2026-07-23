"""
verse_formatter package

VerseFormatter: combines module metadata (abbreviation, file name, CSS, VerseRules)
with verse rendering logic. One concrete formatter per output target x verse mode.

Organized by what you're actually editing at once, not by platform:
  base.py                -- VerseFormatter ABC, shared cross-ref/header/text
                             helpers, e-Sword/MySword xref+red-letter mixins.
                             Rarely touched.
  intralinear.py          -- BSTB/BSXB: e-Sword and MySword formatters + CSS,
                             side by side (edited together to keep them matched).
  reverse_interlinear.py  -- BSRB: e-Sword and MySword formatters + CSS,
                             same reasoning.

This __init__ re-exports the full public surface so callers keep using
`from verse_formatter import X` exactly as before the package split.
"""

from .base import (
    VerseFormatter,
    Reference,
    parse_reference,
    parse_headers,
    MODULE_DESCRIPTION,
    COLOR_TRANSLIT,
    COLOR_ANCIENT,
    COLOR_UNLINKED,
    ABBREV_TO_BOOK_NUM,
)
from .intralinear import (
    ESwordIntralinearFormatter,
    ESwordStackedFormatter,
    MySwordIntralinearFormatter,
    MySwordStackedFormatter,
)
from .reverse_interlinear import (
    ESwordReverseInterlinearFormatter,
    MySwordReverseInterlinearFormatter,
)

__all__ = [
    'VerseFormatter',
    'Reference',
    'parse_reference',
    'parse_headers',
    'MODULE_DESCRIPTION',
    'COLOR_TRANSLIT',
    'COLOR_ANCIENT',
    'COLOR_UNLINKED',
    'ABBREV_TO_BOOK_NUM',
    'ESwordIntralinearFormatter',
    'ESwordStackedFormatter',
    'MySwordIntralinearFormatter',
    'MySwordStackedFormatter',
    'ESwordReverseInterlinearFormatter',
    'MySwordReverseInterlinearFormatter',
]
