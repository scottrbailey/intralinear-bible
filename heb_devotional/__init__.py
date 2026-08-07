"""
Hebrew-calendar reading-plan devotional generation.

reading_plan.py holds everything format-agnostic: loading parshat.json,
fetching Hebcal, anchoring weeks/holidays to real dates, resolving
references to book abbreviations, and filling in verse ranges. It has no
notion of e-Sword's .devi format or MySword's journal format -- those
live in their own sibling modules (esword.py, mysword.py) and both
import from reading_plan.py rather than duplicating any of this.
"""
