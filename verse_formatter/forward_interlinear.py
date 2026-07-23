"""
verse_formatter/forward_interlinear.py

*** ROUGH DRAFT *** -- sketch, not a settled design. Not verified against
real e-Sword/MySword rendering (this session has no device access) or real
data (only tiny samples on disk -- see docs/TABLE_COMPOSER_STATUS.md).
abbreviation/module_name below are placeholders. Treat everything here as
a starting point to test and tune, the same way min_lemma_row_len and the
<ilb>/<ilbc> split in reverse_interlinear.py started as rough guesses and
got corrected against real screenshots.

Forward interlinear: the source language reads in its own word order
(Hebrew/Greek on top), English glossed underneath -- the inverse of
reverse_interlinear.py's layout (English on top, source below). Consumes
TableComposer(direction=MappingDirection.SOURCE_TO_TARGET)'s stream (see
table_composer.py's _build_verse_source_order()): always exactly one
source_word per AlignedToken, never grouped, never plain-text-only, so
render_verse() doesn't need reverse_interlinear.py's is_plain_text /
multi-lemma-group handling at all.

Alignment problem this is trying to solve: a source word's morphology tag
stack varies a lot in height (a bare "NOUN" vs. a compound multi-tag
string that wraps to two lines), so if blocks are just top-aligned in a
row -- the normal default -- the border between the source stack and the
English gloss lands at a different height per block, and the English row
underneath staggers instead of reading as one line.

Approach: chunk tokens into explicit line-groups (_chunk_into_lines(),
budgeted by rendered-character-width same as reverse_interlinear.py's
_group_source_words()) and render each as its own *non-wrapping* flex row
(<fil>, align-items:stretch) -- stretch forces every block in that row to
the tallest block's height. Each block's own source-stack section
(<src>) then gets flex:1 1 auto + justify-content:flex-end, so it fills
that stretched height while packing its actual content to the *bottom* --
right where the border sits -- leaving any extra space invisibly above
shorter stacks. The border (and the English row right after it) comes out
level across the row regardless of morphology height differences.

Deliberately not flex-wrap on one big verse-spanning container: MySword's
reverse-interlinear <lg> row already showed flex-wrap isn't reliable
there (see reverse_interlinear.py's history -- that's why <ilb>/<ilbc>
splitting exists at all). A mega flex-wrap container relying on
per-wrapped-line stretch alignment would risk repeating that exact
failure. Explicit non-wrapping rows sidestep it, at the same cost
<ilb>/<ilbc> already accepts: lines don't adapt to the actual viewport
width the way natural inline wrapping does.

Right-to-left Hebrew display is out of scope here by design -- tokens are
emitted in ascending source_sort order regardless of language, and RTL
flow (dir="rtl", or styling the Hebrew paragraph container) is left to
the caller, same as discussed for table_composer.py's source-order query.
"""

import re

from .base import VerseFormatter, _ESwordXrefMixin, _MySwordXrefMixin

_TAG_RE = re.compile(r'<[^>]+>')


def _token_width(token) -> int:
    """Rough character-count proxy for one block's rendered width -- the
    widest of its stacked lines (source text, transliteration, English
    gloss) roughly determines the block's actual width, since they're all
    centered on top of each other. Same kind of approximation as
    reverse_interlinear.py's _visible_len()/_group_source_words() -- not a
    real measurement, tune by eye. Ignores Strong's/morph width (small,
    0.65-0.7em in the CSS below, rarely the widest line).
    """
    sw = token.source_words[0]
    xlit = sw.stem.translit or ''
    english = _TAG_RE.sub('', token.english or '')
    return max(len(sw.text), len(xlit), len(english))


def _chunk_into_lines(tokens: list, max_len: int) -> list:
    """Split tokens into line-groups whose combined _token_width() roughly
    stays under max_len -- an explicit chunk instead of natural browser
    wrapping; see module docstring for why. Greedy fill, no attempt to
    balance line lengths (unlike reverse_interlinear.py's first-group
    logic, there's no "real label vs. continuation" distinction here --
    every token is equally a real word, so plain greedy fill is enough).
    """
    max_len = max(max_len, 1)
    lines, current, length = [], [], 0
    for token in tokens:
        w = _token_width(token) + 1
        if current and length + w > max_len:
            lines.append(current)
            current, length = [], 0
        current.append(token)
        length += w
    if current:
        lines.append(current)
    return lines


# ============================================================ e-Sword

# Reuses ESwordReverseInterlinearFormatter's tag vocabulary (hb/gk, ltn,
# sb>num, mb>tvm) rather than inventing a new one: forward interlinear is
# that formatter's closest structural cousin (same lemma-stack content:
# source text, transliteration, Strong's, morphology), and <num>/<tvm> are
# e-Sword's real dictionary-linking tags, not just CSS-styled spans -- reusing
# them gives forward interlinear the same tappable Strong's/RMAC links
# reverse interlinear has, for free.
_ESWORD_FORWARD_INTERLINEAR_CSS = """
fil {display:flex; align-items:stretch; margin-bottom:0.75em;}
fib {display:flex; flex-direction:column; margin:0 0.15em; font-size:0.85em;}
src {flex:1 1 auto; display:flex; flex-direction:column; justify-content:flex-end;
     align-items:center; border-bottom:2px solid #ddd; padding-bottom:0.2em;}
src hb, src gk {font-size:1.2em; color:#065e69;}
src ltn {color:green; font-size:0.9em;}
src mb {display:flex; flex-direction:row; flex-wrap:wrap; gap:3px; justify-content:center;}
sb, mb {font-size:0.65em; padding:0;}
eng {text-align:center; padding-top:0.2em;}
"""


class ESwordForwardInterlinearFormatter(_ESwordXrefMixin, VerseFormatter):
    abbreviation   = "BSFI"   # placeholder -- pick a real one before shipping;
                              # same collision check that renamed reverse
                              # interlinear BSBri+ -> BSRB applies here too
    module_name    = "BSB Forward Interlinear Bible"
    file_extension = ".bbli"
    css            = _ESWORD_FORWARD_INTERLINEAR_CSS
    bracket_replacement = ('<i>', '</i>')
    max_line_len:  int = 40   # rough per-line character budget -- see
                              # _chunk_into_lines(); tune by eye against
                              # real rendering, same spirit as
                              # VerseFormatter.min_lemma_row_len

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []

        if header:
            parts.append(self.render_header(header))
        if xref_placement == 1:
            parts.append(self.render_crossref(xrefs))

        for line in _chunk_into_lines(tokens, self.max_line_len):
            parts.append('<fil>')
            for token in line:
                sw    = token.source_words[0]
                xlit  = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                morph = sw.stem.morph or sw.stem.token_class
                morph_tags = ''.join(f'<tvm>{m}</tvm>' for m in morph.split('|'))
                lang_class = 'gk' if sw.lang == 'G' else 'hb'
                english = self.transform_english(token.english, token.par_class)
                parts.append(
                    f'<fib><src><{lang_class}>{sw.text}</{lang_class}><ltn>{xlit}</ltn>'
                    f'<sb><num>{sw.stem.strongs}</num></sb><mb>{morph_tags}</mb></src>'
                    f'<eng>{english}</eng></fib>'
                )
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')
            parts.append('</fil>')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)


# ============================================================ MySword

# Reuses MySwordReverseInterlinearFormatter's tag vocabulary (ro/rt, native
# <W.../WT...> Strong's/morphology links) for the same reason as e-Sword above.
_MYSWORD_FORWARD_INTERLINEAR_CSS = """
fil {display:flex; align-items:stretch; margin-bottom:0.75em;}
fib {display:flex; flex-direction:column; margin:0 0.15em; font-size:0.85em;}
src {flex:1 1 auto; display:flex; flex-direction:column; justify-content:flex-end;
     align-items:center; border-bottom:2px solid #ddd; padding-bottom:0.2em;}
src ro {font-size:1.2em; color:#065e69;}
src rt {color:#7a10ad; font-size:0.9em;}
src mg {display:flex; flex-direction:row; flex-wrap:wrap; gap:3px; justify-content:center;}
.strong, .morph {font-size:0.65em; color:#666;}
eng {text-align:center; padding-top:0.2em;}
"""

_MYSWORD_FORWARD_INTERLINEAR_RULES = ""  # GBF tags handled natively by MySword


class MySwordForwardInterlinearFormatter(_MySwordXrefMixin, VerseFormatter):
    abbreviation   = "BSFI"   # placeholder, see ESwordForwardInterlinearFormatter
    module_name    = "BSB Forward Interlinear Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_FORWARD_INTERLINEAR_CSS
    verse_rules    = _MYSWORD_FORWARD_INTERLINEAR_RULES
    bracket_replacement = ('<i>', '</i>')
    max_line_len:  int = 40

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []

        if header:
            parts.append(self.render_header(header))
        if xref_placement == 1:
            parts.append(self.render_crossref(xrefs))

        for line in _chunk_into_lines(tokens, self.max_line_len):
            parts.append('<fil>')
            for token in line:
                sw      = token.source_words[0]
                xlit    = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                strongs = sw.stem.strongs
                strong_tag = f'<W{strongs}>' if strongs else '<span class="strong">&nbsp;</span>'
                morph = sw.stem.morph or sw.stem.token_class
                morph_tags = ''.join(f'<WT{m}>' for m in morph.split('|'))
                english = self.transform_english(token.english, token.par_class)
                parts.append(
                    f'<fib><src><ro>{sw.text}</ro><rt>{xlit}</rt>{strong_tag}'
                    f'<mg>{morph_tags}</mg></src><eng>{english}</eng></fib>'
                )
                for note in token.notes:
                    parts.append(f"<RF q={note_id_map.get(note['noteId'], note['noteId'])}>{note['text']}<Rf>")
            parts.append('</fil>')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)

    def preview_transform(self, scripture: str) -> str:
        return self._apply_rules(scripture, self.verse_rules)
