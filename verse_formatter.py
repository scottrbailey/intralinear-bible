"""
verse_formatter.py

VerseFormatter: combines module metadata (abbreviation, file name, CSS, VerseRules)
with verse rendering logic.  One concrete formatter per output target × verse style.

The formatter owns the full rendering contract: the tags render_verse() emits,
the CSS that styles them, and the VerseRules regex that transforms them (MySword).
All three must stay in sync — change one, change the others.

The writer is responsible for filtering inputs before calling render_verse():
if headers/notes/xrefs are disabled, the writer passes None/[] so the formatter
never emits the corresponding tags and the CSS for them is never exercised.
"""

import re
from abc import ABC, abstractmethod
from textwrap import dedent
from collections.abc import Callable
from bible_books import ABBREV_TO_BOOK_NUM
from translit import make_transliterator


MODULE_DESCRIPTION = dedent("""\
    Berean Standard Bible with inline Hebrew and Greek transliteration.
    Source language data from WLC (OT) and SBLGNT (NT) via Clear Bible
    Alignments project (CC BY 4.0).""")


# ================================================================ base class

class VerseFormatter(ABC):
    """Defines everything about one build target: metadata, CSS, and verse rendering.

    Class-level constants (override in each subclass):
      abbreviation   — module abbreviation used as file stem and work_id (e.g. "BSBi")
      module_name    — human-readable title
      file_extension — output file extension (e.g. ".bbli", ".bbl.mybible")
      description    — freeform text for the Details table
      publish_date   — date translation was published (YYYY-MM-DD)
      css            — CSS string inserted into the module (empty if not applicable)
      verse_rules    — VerseRules transform string (MySword only; empty otherwise)
    """

    abbreviation:   str = ""
    module_name:    str = ""
    file_extension: str = ""
    description:    str = MODULE_DESCRIPTION
    publish_date:   str = "2020-12-01"
    css:            str = ""
    verse_rules:    str = ""

    def __init__(self, transliterate: Callable = None):
        self.transliterate = transliterate or make_transliterator()

    @abstractmethod
    def render_verse(self, tokens: list, header: str = None,
                     note_id_map: dict = None, xrefs: list = None,
                     xref_placement: int = 0) -> str:
        """Render a list of AlignedTokens to a format-specific string."""

    def preview_transform(self, scripture: str) -> str:
        """Apply any VerseRules-style transforms for console preview. Default: identity."""
        return scripture

    def _apply_rules(self, text: str, rules: str) -> str:
        result = text
        for line in rules.split('\n'):
            if '\t' not in line:
                continue
            pattern, replacement = line.split('\t', 1)
            replacement = re.sub(r'\$(\d+)', r'\\\1', replacement)
            result = re.sub(pattern, replacement, result)
        return result


# ============================================================ e-Sword profiles
_ESWORD_INTRALINEAR_CSS = dedent('''\
    .ilb {display:inline-flex; flex-direction:column; align-items:center; vertical-align:middle; font-size:0.85em; gap:1px; line-height:0.9em; 
        padding:4px 0; position:relative; height: 2.4em; overflow: hidden}
    ruby {color: blue; display:block}
    ruby > rt {font-size: 1.1em; color: #1ca0b1; display: block; text-align: center; opacity: 0;}
    .ilb ruby ~ * {position: absolute; z-index:9999; top:0.5em; left:0; right: 0; text-align: center; opacity: 0;}'''
)

class ESwordIntralinearFormatter(VerseFormatter):
    abbreviation   = "BSBi"
    module_name    = "Berean Standard Intralinear Bible"
    file_extension = ".bbli"
    css            = _ESWORD_INTRALINEAR_CSS

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []

        if header:
            parts.append(f'<b class="headline">{header}</b><br>')
        if xref_placement == 1:
            parts.append(self._xref_markers(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')
            else:
                parts.append(token.english)
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper)
                    # yes I know the ruby / rt tags are semantically inverted - easier to hide rt
                    lemmas.append(
                        f'<span class="ilb">'
                        f'<ruby>{xlit}<rt>{sw.text}</rt></ruby>'
                        f'<num>{sw.stem.strongs}</num>'
                        f'</span>'
                    )
                parts.append(' '.join(lemmas))
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self._xref_markers(xrefs))

        return ''.join(parts)

    @staticmethod
    def _xref_markers(xrefs: list) -> str:
        return ''.join(f' <not>R{vx["key"]}</not>' for vx in xrefs)


_ESWORD_STACKED_CSS = dedent('''\
    .ilb {display:inline-flex; flex-direction:column; align-items:center; vertical-align:middle; font-size:0.85em; gap:1px; line-height:0.9em; 
        padding:4px 0; position:relative; height: 2.4em; overflow: hidden}
    ruby {color: blue; display:block}
    ruby > rt {font-size: 1.1em; color: #1ca0b1; display: block; text-align: center; opacity: 1;}
    .ilb ruby ~ * {position: absolute; z-index:9999; top:0.5em; left:0; right: 0; text-align: center; opacity: 0;}'''
)

class ESwordStackedFormatter(ESwordIntralinearFormatter):
    abbreviation   = "BSBis"
    module_name    = "Berean Standard Intralinear Bible  (Stacked)"
    file_extension = ".bbli"
    css            = _ESWORD_STACKED_CSS



_ESWORD_INTERLINEAR_CSS = (
    'qi{display:inline-flex;flex-direction:column;align-items:center;'
    'vertical-align:top;margin:0 3px}'
    'e{white-space:nowrap}'
    'qi>span{display:flex;flex-direction:row;gap:0px}'
    'lem {display:inline-flex;flex-direction:column;align-items:center;vertical-align:top;'
    'font-size:.9em;margin-top:2px;padding-top:2px;gap:2px;line-height:1 !important;}'
    'lem sup{display:block;vertical-align:baseline;margin:0;padding:0;line-height:1}'
    '.xlit{color:#2244aa}'
    'tvm{color:#666}'
)

class ESwordReverseInterlinearFormatter(VerseFormatter):
    abbreviation   = "BSBri"
    module_name    = "BSB Reverse Interlinear Bible"
    file_extension = ".bbli"
    css            = _ESWORD_INTERLINEAR_CSS

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        """Each aligned token becomes a <qi> column: English on top, source words below.

        Leading punctuation (plain token with skip_space_after) is accumulated in
        `pending` and prepended to the next aligned token's <e>.
        Trailing punctuation glued to an aligned token is absorbed into its <e>.
        """
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []
        skip        = set()
        pending     = ''

        if header:
            parts.append(f'<b class="headline">{header}</b><br>')
        if xref_placement == 1:
            parts.append(self._xref_markers(xrefs))

        for i, token in enumerate(tokens):
            if i in skip:
                continue

            is_plain = token.is_plain_text or not token.source_words

            if is_plain:
                if token.skip_space_after:
                    pending += token.english
                else:
                    text    = pending + token.english
                    pending = ''
                    parts.append(f'<q><e>{text}</e></q>')
            else:
                english = pending + token.english
                pending = ''

                j        = i + 1
                cur_skip = token.skip_space_after
                while cur_skip and j < len(tokens):
                    next_tok = tokens[j]
                    if next_tok.is_plain_text or not next_tok.source_words:
                        english += next_tok.english
                        skip.add(j)
                        cur_skip = next_tok.skip_space_after
                        j += 1
                    else:
                        break

                segments = []
                for sw in token.source_words:
                    xlit    = self.transliterate(sw.text, sw.lang, sw.is_proper)
                    strongs = sw.stem.strongs
                    if sw.lang == 'G':
                        segments.append(
                            f'<lem><gs>{sw.text}</gs>'
                            f'<xlit>{xlit}</xlit>'
                            f'<num>{strongs}</num>'
                            f'<tvm>{sw.stem.morph}</tvm></lem>'
                        )
                    else:
                        segments.append(
                            f'<lem><heb>{sw.text}</heb>'
                            f'<xlit>{xlit}</xlit>'
                            f'<tvm>{sw.stem.morph}</tvm></lem>'
                        )

                parts.append(
                    f'<qi><e>{english}</e>'
                    f'<span>{"".join(segments)}</span></qi>'
                )
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

        if xref_placement == 2:
            parts.append(self._xref_markers(xrefs))

        return ''.join(parts)

    @staticmethod
    def _xref_markers(xrefs: list) -> str:
        return ''.join(f' <not>R{vx["key"]}</not>' for vx in xrefs)

# ============================================================ MySword profiles

_XREF_REF_RE = re.compile(r'^(\S+)\s+(\d+):(\d+)(?:-(\d+))?$')


def _mysword_rx_tags(text: str) -> str:
    """Convert 'Joh 1:1-5; Heb 11:1-3' style refs to '<RX b.c.v-v>label' pairs.

    RX is a bare milestone tag with no visible label of its own, so each tag
    is followed by its own ref text — a note popup containing only RX tags
    and nothing else renders as blank.
    """
    tags = []
    for ref in text.split(';'):
        ref = ref.strip()
        m = _XREF_REF_RE.match(ref)
        if not m:
            continue
        abbrev, chapter, verse, verse_end = m.groups()
        book_num = ABBREV_TO_BOOK_NUM.get(abbrev)
        if not book_num:
            continue
        loc = f"{book_num}.{chapter}.{verse}"
        if verse_end:
            loc += f"-{verse_end}"
        tags.append(f"<RX{loc}>{ref}")
    return '; '.join(tags)


def _mysword_xref_markers(xrefs: list) -> str:
    """One <RF q=R{key}>...<Rf> per xref group, nesting each group's <RX> tags."""
    parts = []
    for vx in xrefs:
        rx_tags = _mysword_rx_tags(vx['text'])
        if rx_tags:
            parts.append(f"<RF q=R{vx['key']}>{rx_tags}<Rf>")
    return ''.join(parts)


_MYSWORD_INTRALINEAR_CSS = dedent("""\
	.ilb ruby {display: inline-flex; flex-direction: column; align-items:center; vertical-align:middle; gap: 1px; padding:2px 0; position:relative; font-size:0.8em;}
    ruby > ro {display:block; color:#1ca0b1; text-align: center; opacity: 0;}
    ruby > rt {display:block; font-size: 1.1em; color: blue;}
    ruby a {text-decoration: none;}
""")

_MYSWORD_INTRALINEAR_RULES = ''

class MySwordIntralinearFormatter(VerseFormatter):
    abbreviation   = "BSTB"
    module_name    = "Berean Standard Transliterated Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_INTRALINEAR_CSS
    verse_rules    = _MYSWORD_INTRALINEAR_RULES

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        """Render tokens with <span class="ilb"><ruby> markup for lemma display."""
        xrefs = xrefs or []
        parts = []
        if header:
            parts.append(f"<TS>{header}<Ts>")
        if xref_placement == 1:
            parts.append(self._xref_markers(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                for note in token.notes:
                    parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")
            else:
                parts.append(token.english)
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper)
                    lemmas.append(
                        f'<span class="ilb"><ruby><rt><a href="s{sw.stem.strongs}">{xlit}</a></rt>'
                        f'<ro>{sw.text}</ro></ruby></span>'
                    )
                parts.append(' '.join(lemmas))
                for note in token.notes:
                    parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self._xref_markers(xrefs))

        return ''.join(parts)

    def preview_transform(self, scripture: str) -> str:
        return self._apply_rules(scripture, self.verse_rules)

    @staticmethod
    def _xref_markers(xrefs: list) -> str:
        return _mysword_xref_markers(xrefs)

_MYSWORD_STACKED_CSS = dedent("""\
	.ilb ruby {display: inline-flex; flex-direction: column; align-items:center; vertical-align:middle; gap: 1px; 
	    padding:2px 0; position:relative; font-size:0.8em; line-height: 1;}
    ruby > ro {display:block; color:#1ca0b1; text-align: center; opacity: 1;}
    ruby > rt {display:block; font-size: 1.1em; color: blue;}
    ruby a {text-decoration: none;}
""")

_MYSWORD_STACKED_RULES = ''

class MySwordStackedFormatter(MySwordIntralinearFormatter):
    """Stacked variant: same verse content, different CSS."""
    abbreviation = "BSXB+"
    module_name  = "Berean Standard Translinear Bible"
    css          = _MYSWORD_STACKED_CSS
    verse_rules  = _MYSWORD_STACKED_RULES

_MYSWORD_INTERLINEAR_CSS = """
sup { font-size: 70%; }
.xlit a { color: blue; text-decoration: none; }
"""

_MYSWORD_INTERLINEAR_RULES = ""  # GBF tags handled natively by MySword

class MySwordReverseInterlinearFormatter(VerseFormatter):
    abbreviation   = "BSBri"
    module_name    = "BSB Reverse Interlinear Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_INTERLINEAR_CSS
    verse_rules    = _MYSWORD_INTERLINEAR_RULES

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        """Render tokens with GBF tags for MySword interlinear display."""
        xrefs = xrefs or []
        parts = []
        if header:
            parts.append(f"<TS>{header}<Ts>")
        if xref_placement == 1:
            parts.append(self._xref_markers(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                for note in token.notes:
                    parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")
            else:
                segments = []
                for sw in token.source_words:
                    xlit    = self.transliterate(sw.text, sw.lang, sw.is_proper)
                    strongs = sw.stem.strongs
                    tag     = 'G' if sw.lang == 'G' else 'H'
                    end     = 'g' if sw.lang == 'G' else 'h'
                    segments.append(f"<{tag}>{sw.text}<W{strongs}><X>{xlit}<x><{end}>")
                parts.append(
                    f"<Q>{''.join(segments)}<E>{token.english}<e><q>"
                )
                for note in token.notes:
                    parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self._xref_markers(xrefs))

        return ''.join(parts)

    @staticmethod
    def _xref_markers(xrefs: list) -> str:
        return _mysword_xref_markers(xrefs)
