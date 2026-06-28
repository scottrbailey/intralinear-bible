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

    def __init__(self, transliterate: callable = None):
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

# ============================================================ e-Sword CSS

_ESWORD_INTRALINEAR_CSS = (
    '.stk{display:inline-flex;flex-direction:column;align-items:center;'
    'vertical-align:super;font-size:0.65em;color:blue;line-height:0.9}'
    'span.stk a{opacity:0 !important;}'
)

_ESWORD_STACKED_CSS = (
    '.stk {display:inline-flex; flex-direction:column; align-items:center;vertical-align:super; font-size:0.75em; gap:4px;'
    'color:#999 !important; line-height:1.3 !important; padding:4px 0; position:relative; height: 2.4em; overflow: hidden}\n'
    '.stk.xlit{color: blue}\n'
    '.stk >.heb{font-size:0.9em;}\n'
    '.stk >.grk {font-size:0.85em;}\n'
    '.stk.heb ~ *, .stk.grk ~ * {position:absolute; z-index:9999; top:0.5em; bottom:0.5em; left:0; right:0; text-align:center; opacity:0;}'
)

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

# ============================================================ e-Sword profiles

class ESwordIntralinearFormatter(VerseFormatter):
    abbreviation   = "BSBi"
    module_name    = "Berean Standard Bible Intralinear"
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
                    lemmas.append(
                        f'<span class="stk">'
                        f'{xlit} '
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

class ESwordStackedFormatter(VerseFormatter):
    abbreviation   = "BSBis"
    module_name    = "Berean Standard Bible Intralinear Stacked"
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
                    lang_cls = 'grk' if sw.lang == 'G' else 'heb'
                    lemmas.append(
                        f'<span class="stk">'
                        f'<span class="xlit">{xlit}</span> '
                        f'<span class="{lang_cls}">{sw.text}</span>'
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

# ============================================================ MySword CSS / VerseRules

_MYSWORD_INTRALINEAR_CSS = """
sup { font-size: 75%; }
.xlit a { color: blue; text-decoration: none; }
.ref { font-size: 0.65em; color: #333; background-color: #e8e8e8;
       border-radius: 3px; padding: 0 2px; text-decoration: none; }
"""

# Tab between pattern and replacement is required by MySword.
_MYSWORD_INTRALINEAR_RULES = (
    '<lemma sn="([^ "]+)" o="([^"]*?)">([^<]*)</lemma>\t'
    '<sup class="xlit"><a href="s$1">$3</a></sup>\n'
)

_MYSWORD_STACKED_CSS = """
ruby { display: inline-flex; flex-direction: column-reverse; align-items: center;
  color: #888; gap: 0px; font-size: 80%; vertical-align: middle; margin: 0 3px;
  padding: 3px 0; line-height: 0.9}
ruby > rt { font-size: 1.0em; }
ruby > rt a { color: blue; text-decoration: none; }
.ref {font-size: 0.65em; color: #333; background-color: #e8e8e8;
       border-radius: 3px; padding: 0 2px; text-decoration: none; }
"""

_MYSWORD_STACKED_RULES = (
    '<lemma sn="([^ "]+)" o="([^"]*?)">([^<]*)</lemma>\t'
    '<ruby>$2<rt><a href="s$1">$3</a></rt></ruby>'
)

_MYSWORD_INTERLINEAR_CSS = """
sup { font-size: 70%; }
.xlit a { color: blue; text-decoration: none; }
"""

_MYSWORD_INTERLINEAR_RULES = ""  # GBF tags handled natively by MySword

# ============================================================ MySword profiles

class MySwordIntralinearFormatter(VerseFormatter):
    abbreviation   = "BSBi"
    module_name    = "BSB Intralinear Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_INTRALINEAR_CSS
    verse_rules    = _MYSWORD_INTRALINEAR_RULES

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        """Render tokens with <lemma sn="..."> tags; MySword VerseRules transforms them."""
        parts = []
        if header:
            parts.append(f"<TS>{header}<Ts>")

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
                        f'<lemma sn="{sw.stem.strongs}" o="{sw.text}">{xlit}</lemma>'
                    )
                parts.append(' '.join(lemmas))
                for note in token.notes:
                    parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        return ''.join(parts)

    def preview_transform(self, scripture: str) -> str:
        return self._apply_rules(scripture, self.verse_rules)


class MySwordStackedFormatter(MySwordIntralinearFormatter):
    """Stacked variant: same <lemma> verse content, different CSS/VerseRules."""
    abbreviation = "BSBis"
    module_name  = "BSB Intralinear Bible (Stacked)"
    css          = _MYSWORD_STACKED_CSS
    verse_rules  = _MYSWORD_STACKED_RULES


class MySwordReverseInterlinearFormatter(VerseFormatter):
    abbreviation   = "BSBri"
    module_name    = "BSB Reverse Interlinear Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_INTERLINEAR_CSS
    verse_rules    = _MYSWORD_INTERLINEAR_RULES

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        """Render tokens with GBF tags for MySword interlinear display."""
        parts = []
        if header:
            parts.append(f"<TS>{header}<Ts>")

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

        return ''.join(parts)
