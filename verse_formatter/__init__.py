"""
verse_formatter package

VerseFormatter: combines module metadata (abbreviation, file name, CSS, VerseRules)
with verse rendering logic. One concrete formatter per output target x verse mode.

Organized by what you're actually editing at once, not by platform:
  base.py                -- VerseFormatter ABC, shared cross-ref/header/text
                             helpers, e-Sword/MySword xref+red-letter mixins.
                             Rarely touched.
  intralinear.py          -- BTB-L1/L2/L3 and their DTB (Drash Transliterated
                             Bible, restored-names) counterparts: e-Sword and
                             MySword formatters + CSS, side by side (edited
                             together to keep them matched).
  reverse_interlinear.py  -- BSRB: e-Sword and MySword formatters + CSS,
                             same reasoning.
  forward_interlinear.py  -- ROUGH DRAFT, layout/CSS not settled yet (see
                             its module docstring) -- e-Sword and MySword
                             formatters for TableComposer's
                             SOURCE_TO_TARGET direction.

This __init__ re-exports the full public surface so callers keep using
`from verse_formatter import X` exactly as before the package split.
"""

from .base import (
    VerseFormatter,
    Reference,
    parse_reference,
    parse_headers,
    MODULE_DESCRIPTION,
    ABBREV_TO_BOOK_NUM,
)
from .intralinear import (
    ESwordLemmaFormatter,
    ESwordLemmaDetailFormatter,
    ESwordScriptFormatter,
    MySwordLemmaFormatter,
    MySwordLemmaDetailFormatter,
    MySwordScriptFormatter,
    ESwordDrashLemmaFormatter,
    ESwordDrashLemmaDetailFormatter,
    ESwordDrashScriptFormatter,
    MySwordDrashLemmaFormatter,
    MySwordDrashLemmaDetailFormatter,
    MySwordDrashScriptFormatter,
)
from .reverse_interlinear import (
    ESwordReverseInterlinearFormatter,
    MySwordReverseInterlinearFormatter,
)
from .forward_interlinear import (
    ESwordForwardInterlinearFormatter,
    MySwordForwardInterlinearFormatter,
)

__all__ = [
    'VerseFormatter',
    'Reference',
    'parse_reference',
    'parse_headers',
    'MODULE_DESCRIPTION',
    'ABBREV_TO_BOOK_NUM',
    'ESwordLemmaFormatter',
    'ESwordLemmaDetailFormatter',
    'ESwordScriptFormatter',
    'MySwordLemmaFormatter',
    'MySwordLemmaDetailFormatter',
    'MySwordScriptFormatter',
    'ESwordDrashLemmaFormatter',
    'ESwordDrashLemmaDetailFormatter',
    'ESwordDrashScriptFormatter',
    'MySwordDrashLemmaFormatter',
    'MySwordDrashLemmaDetailFormatter',
    'MySwordDrashScriptFormatter',
    'ESwordReverseInterlinearFormatter',
    'MySwordReverseInterlinearFormatter',
    'ESwordForwardInterlinearFormatter',
    'MySwordForwardInterlinearFormatter',
]
