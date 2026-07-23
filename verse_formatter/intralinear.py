"""
verse_formatter/intralinear.py

BSTB (intralinear) and BSXB (stacked) formatters for both e-Sword and
MySword, kept side by side deliberately: these two platforms' CSS and
render_verse() are edited together to keep them visually matched, so this
file is the one to open when tuning intralinear/stacked layout or styling
for either target.

Stacked reuses its platform's Intralinear render_verse() unchanged -- same
tags, different CSS (ruby's original-language line shown instead of hidden).
"""

from .base import (
    VerseFormatter, _ESwordXrefMixin, _MySwordXrefMixin,
    _split_trailing_punct, COLOR_TRANSLIT, COLOR_ANCIENT, COLOR_UNLINKED,
)
from textwrap import dedent

# Shared by both MySword and e-Sword.
#
# .acrostic/.ihdg/.subhdg stay plain inline spans (no display/float trick —
# display:block broke the line both before and after in e-Sword, separating
# the span from the verse number; a float:left/width:100% attempt at "same
# line, wrap only after" then behaved unpredictably against the real
# rendering engine too) — render_header() appends a literal <br/> after each
# span instead, which is guaranteed to force the wrap without touching
# whatever precedes it on the line.
_INTRALINEAR_CSS = dedent(f'''\
    .acrostic, .ihdg, .subhdg {{color:#777; font-style:italic; font-weight:bold;}}
    .acrostic {{text-align:center;}}
    .ihdg {{font-weight:normal;}}
    .subhdg {{font-style:normal;}}
    .pshdg, .inscrip, .selah {{font-style:italic;}}
    .ilb {{display:inline-block; vertical-align:middle; padding:4px 0; position:relative; font-size:0.8em; line-height:1;}}
    .ilb ruby {{display:inline-flex; flex-direction:column;}}
    .hb ruby ro {{font-size:1.2em;}}
    ruby ro {{display:block; color:{COLOR_ANCIENT}; text-align:center;}}
    ruby rt {{display:block; font-size:1.1em;}} ruby rt.unlinked {{color: {COLOR_UNLINKED};}}
''')

# ============================================================ e-Sword

_ESWORD_INTRALINEAR_CSS = (_INTRALINEAR_CSS +
    f'ruby > ro {{opacity:0}} ruby rt {{color:{COLOR_TRANSLIT};}}\n' +
    '.ilb ruby ~ * {position:absolute; z-index:9999; top:0.5em; left:0; right:0; text-align:center; opacity:0;}'
)

class ESwordIntralinearFormatter(_ESwordXrefMixin, VerseFormatter):
    abbreviation   = "BSTB"
    module_name    = "Berean Standard Transliterated Bible"
    file_extension = ".bbli"
    css            = _ESWORD_INTRALINEAR_CSS

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []
        in_red      = False

        if header:
            parts.append(self.render_header(header))
        if xref_placement == 1:
            parts.append(self.render_crossref(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_red and not in_red:
                parts.append(self.red_letter_tags[0])
                in_red = True

            if token.is_plain_text or not token.source_words:
                parts.append(self.transform_english(token.english, token.par_class))
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')
            else:
                core, trail = _split_trailing_punct(token.english)
                parts.append(self.transform_english(core, token.par_class))
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    strongs = sw.stem.strongs
                    lang = 'gk' if sw.lang == 'G' else 'hb'
                    rt_class = ' class="unlinked"' if not strongs else ''
                    num_tag  = f'<num>{strongs}</num>' if strongs else ''
                    lemmas.append(
                        f'<span class="ilb {lang}">'
                        f'<ruby><rt{rt_class}>{xlit}</rt><ro>{sw.text}</ro></ruby>'
                        f'{num_tag}'
                        f'</span>'
                    )
                parts.append(' '.join(lemmas))
                parts.append(trail)
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

            if in_red and (next_token is None or not next_token.is_red):
                parts.append(self.red_letter_tags[1])
                in_red = False

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)


_ESWORD_STACKED_CSS = _INTRALINEAR_CSS + \
    f'ruby > ro {{opacity:1}} ruby rt {{color:{COLOR_TRANSLIT};}}' + \
    '\n.ilb ruby ~ * {position:absolute; z-index:9999; top:0.5em; left:0; right:0; text-align:center; opacity:0;}'


class ESwordStackedFormatter(ESwordIntralinearFormatter):
    abbreviation   = "BSXB"
    module_name    = "Berean Standard Translinear Bible"
    file_extension = ".bbli"
    css            = _ESWORD_STACKED_CSS

# ============================================================ MySword

_MYSWORD_INTRALINEAR_CSS = _INTRALINEAR_CSS +\
    f'ruby > ro {{opacity:0}} .ilb ruby {{color:{COLOR_UNLINKED};}} ruby rt a {{text-decoration: none; color:{COLOR_TRANSLIT};}}'

_MYSWORD_INTRALINEAR_RULES = ''

class MySwordIntralinearFormatter(_MySwordXrefMixin, VerseFormatter):
    abbreviation   = "BSTB"
    module_name    = "Berean Standard Transliterated Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_INTRALINEAR_CSS
    verse_rules    = _MYSWORD_INTRALINEAR_RULES

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        """Render tokens with <span class="ilb"><ruby> markup for lemma display."""
        note_id_map = note_id_map or {}
        xrefs = xrefs or []
        parts = []
        in_red = False
        if header:
            parts.append(self.render_header(header))
        if xref_placement == 1:
            parts.append(self.render_crossref(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_red and not in_red:
                parts.append(self.red_letter_tags[0])
                in_red = True

            if token.is_plain_text or not token.source_words:
                parts.append(self.transform_english(token.english, token.par_class))
                for note in token.notes:
                    parts.append(f"<RF q={note_id_map.get(note['noteId'], note['noteId'])}>{note['text']}<Rf>")
            else:
                core, trail = _split_trailing_punct(token.english)
                parts.append(self.transform_english(core, token.par_class))
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    strongs = sw.stem.strongs
                    lang = 'gk' if sw.lang == 'G' else 'hb'
                    # With no strongs number, `<a href="s">` would be a real but broken
                    # link, so <rt> gets plain text instead — plus 'unlinked' so it reads
                    # as "known unavailable" rather than a dead link (see _INTRALINEAR_CSS).
                    if strongs:
                        rt = f'<rt><a href="s{strongs}">{xlit}</a></rt>'
                    else:
                        rt = f'<rt>{xlit}</rt>'
                    lemmas.append(
                        f'<span class="ilb {lang}"><ruby>{rt}<ro>{sw.text}</ro></ruby></span>'
                    )
                parts.append(' '.join(lemmas))
                parts.append(trail)
                for note in token.notes:
                    parts.append(f"<RF q={note_id_map.get(note['noteId'], note['noteId'])}>{note['text']}<Rf>")

            if in_red and (next_token is None or not next_token.is_red):
                parts.append(self.red_letter_tags[1])
                in_red = False

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)

    def preview_transform(self, scripture: str) -> str:
        return self._apply_rules(scripture, self.verse_rules)

_MYSWORD_STACKED_CSS = _INTRALINEAR_CSS + \
    f'.ilb ruby {{color:{COLOR_UNLINKED};}} ruby rt a {{text-decoration: none; color:{COLOR_TRANSLIT};}}'

_MYSWORD_STACKED_RULES = ''

class MySwordStackedFormatter(MySwordIntralinearFormatter):
    """Stacked variant: same verse content, different CSS."""
    abbreviation = "BSXB"
    module_name  = "Berean Standard Translinear Bible"
    css          = _MYSWORD_STACKED_CSS
    verse_rules  = _MYSWORD_STACKED_RULES
