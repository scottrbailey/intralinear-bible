"""
verse_formatter/intralinear.py

Three-tier BTB-L1/L2/L3 formatters for both e-Sword and MySword, kept side
by side deliberately: these two platforms' CSS and render_verse() are
edited together to keep them visually matched, so this file is the one to
open when tuning any of the three tiers' layout or styling for either
target.

L1 (lemma transliteration only) and L2 (lemma over full-word
transliteration) share one render_verse() per platform -- same tags,
different CSS (ruby's `ro` line shown instead of hidden) -- since L2's
markup is identical to L1's, just with `ro` populated whenever it would
add real information (see MySwordLemmaFormatter's docstring). L3
(full-word transliteration over the original script) predates the lemma
feature and keeps its own render_verse(), unchanged in substance.

MySword implemented first: e-Sword has no way to make an inline dictionary
link directly, so its `rt`/`ro` markup is paired with a hidden `<num>` tag
CSS-positioned over the visible line to fake one (e-Sword auto-links a
bare `<num>` tag's content to that Strong's number's dictionary entry) --
see the e-Sword section below for that mechanism. MySword's real inline
`<a href="s...">` links have no such workaround to get right, so they're
the simpler starting point to validate the three-tier structure against.
"""

from .base import (
    VerseFormatter, _ESwordXrefMixin, _MySwordXrefMixin,
    _split_trailing_punct
)
from textwrap import dedent

COLOR_TRANSLIT = '#475eaf'
COLOR_ANCIENT = '#479faf'
COLOR_UNLINKED = '#666666'

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
    ruby ro {{display:block; min-height:1em; color:{COLOR_ANCIENT}; text-align:center;}}
    ruby rt {{display:block; font-size:1.1em;}} ruby rt.unlinked {{color: {COLOR_UNLINKED};}}
''')
# .hb ruby ro's larger font-size (helps Hebrew's small vowel points stay
# legible) used to live in the shared block above -- fine when every
# formatter's `ro` held the original script (or a hidden copy of it), but
# no longer, now that BTB-L1/L2 put a transliteration (or a bare space
# placeholder) there instead. Added explicitly only to the CSS blocks
# below whose `ro` still holds the real original script.

# ============================================================ e-Sword

_ESWORD_INTRALINEAR_CSS = (_INTRALINEAR_CSS +
    f'.hb ruby ro {{font-size:1.2em;}} ruby > ro {{opacity:0}} ruby rt {{color:{COLOR_TRANSLIT};}}\n' +
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
    f'.hb ruby ro {{font-size:1.2em;}} ruby > ro {{opacity:1}} ruby rt {{color:{COLOR_TRANSLIT};}}' + \
    '\n.ilb ruby ~ * {position:absolute; z-index:9999; top:0.5em; left:0; right:0; text-align:center; opacity:0;}'


class ESwordStackedFormatter(ESwordIntralinearFormatter):
    abbreviation   = "BSXB"
    module_name    = "Berean Standard Translinear Bible"
    file_extension = ".bbli"
    css            = _ESWORD_STACKED_CSS

# ============================================================ MySword
#
# Three tiers, not two: L1/L2 share one render_verse (lemma transliteration
# in `rt`, this word's own full transliteration in `ro`), L3 keeps the
# original rt=full-word-transliteration/ro=original-script pairing this
# pair used to share before the lemma feature existed.
#
# L1/L2 diverge on what `ro` actually holds, though, not just its CSS --
# see _ro_content()'s docstring on MySwordLemmaFormatter -- so that one
# piece of behavior is pulled into its own overridable method rather than
# duplicating render_verse for a single-line difference.

_MYSWORD_LEMMA_CSS = _INTRALINEAR_CSS +\
    f'ruby > ro {{opacity:0}} .ilb ruby {{color:{COLOR_UNLINKED};}} ruby rt a {{text-decoration: none; color:{COLOR_TRANSLIT};}}'

_MYSWORD_LEMMA_RULES = ''

class MySwordLemmaFormatter(_MySwordXrefMixin, VerseFormatter):
    """BTB-L1: lemma transliteration only -- links to the Strong's entry via
    a readable word ('reshit') instead of a bare number ('H7225'). Shares
    render_verse with MySwordLemmaDetailFormatter (BTB-L2); the CSS
    difference between them is `ruby > ro` hidden here, shown there -- see
    _ro_content() for why the two also need different *content* in `ro`,
    not just different visibility.
    """
    abbreviation   = "BTB-L1"
    module_name    = "Berean Transliterated Bible - Level 1"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_LEMMA_CSS
    verse_rules    = _MYSWORD_LEMMA_RULES

    @staticmethod
    def _ro_content(word_xlit: str, lemma_xlit: str) -> str:
        """L1's `ro` is always a single space, never the real full-word
        transliteration -- confirmed against a real MySword build that
        `ruby`'s `display:inline-flex; flex-direction:column` sizes the
        whole box to its *widest* child, including an invisible one
        (`opacity:0` keeps the box in flow, just not rendered). Hebrew's
        full-word transliteration is routinely longer than the bare lemma
        once prefixes/case endings are involved, so putting the real text
        in a permanently-hidden `ro` was forcing every word's box wide
        enough for text nobody ever sees, leaving a visible gap around the
        short visible lemma. A space has negligible width regardless of
        what the real word would have been, and there's no reason to carry
        the real (and sometimes long) text into the DOM at all when it can
        never be shown.
        """
        return ' '

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
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
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f"<RF q=N{seq}>{note['text']}<Rf> ")
            else:
                core, trail = _split_trailing_punct(token.english)
                parts.append(self.transform_english(core, token.par_class))
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    word_xlit  = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    lemma_xlit = sw.stem.lemma_translit or word_xlit
                    strongs = sw.stem.strongs
                    lang = 'gk' if sw.lang == 'G' else 'hb'
                    # With no strongs number, `<a href="s">` would be a real but broken
                    # link, so <rt> gets plain text instead — plus 'unlinked' so it reads
                    # as "known unavailable" rather than a dead link (see _INTRALINEAR_CSS).
                    if strongs:
                        rt = f'<rt><a href="s{strongs}">{lemma_xlit}</a></rt>'
                    else:
                        rt = f'<rt>{lemma_xlit}</rt>'
                    lemmas.append(
                        f'<span class="ilb {lang}"><ruby>{rt}<ro>{self._ro_content(word_xlit, lemma_xlit)}</ro></ruby></span>'
                    )
                parts.append(' '.join(lemmas))
                parts.append(trail)
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f"<RF q=N{seq}>{note['text']}<Rf> ")

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


_MYSWORD_LEMMA_DETAIL_CSS = _INTRALINEAR_CSS +\
    f'ruby > ro {{opacity:1}} .ilb ruby {{color:{COLOR_UNLINKED};}} ruby rt a {{text-decoration: none; color:{COLOR_TRANSLIT};}}'

class MySwordLemmaDetailFormatter(MySwordLemmaFormatter):
    """BTB-L2: lemma transliteration over full-word transliteration -- same
    render_verse as BTB-L1, `ro` shown via CSS instead of hidden. Overrides
    _ro_content(): unlike L1 (always a space, see that method's docstring
    on MySwordLemmaFormatter), L2's `ro` is genuinely meant to be read, so
    it holds the real full-word transliteration whenever that adds
    information beyond the lemma alone.

    A plain '' for the no-extra-information case (same word, same
    transliteration -- common in Greek, where the citation form and an
    inflected form's transliteration often coincide) turned out to be
    wrong, not just uninformative: an empty `ro` collapses to no
    meaningful height, so `.ilb`'s vertical-align:middle centers a
    one-line box for that word while every neighboring word (real
    two-line box) still centers a two-line one -- `rt` drops to the
    English baseline for that word alone, producing a visibly jagged line
    where some words ride high and others sit low depending on whether
    that specific word's lemma happened to match. A space preserves the
    same line-height as a populated `ro` without displaying anything,
    keeping every word's vertical position identical regardless of
    content.
    """
    abbreviation = "BTB-L2"
    module_name  = "Berean Transliterated Bible - Level 2"
    css          = _MYSWORD_LEMMA_DETAIL_CSS

    @staticmethod
    def _ro_content(word_xlit: str, lemma_xlit: str) -> str:
        return word_xlit if word_xlit != lemma_xlit else ' '


_MYSWORD_TRANSLINEAR_CSS = _INTRALINEAR_CSS + \
    f'.hb ruby ro {{font-size:1.2em;}} .ilb ruby {{color:{COLOR_UNLINKED};}} ruby rt a {{text-decoration: none; color:{COLOR_TRANSLIT};}}'

_MYSWORD_TRANSLINEAR_RULES = ''

class MySwordStackedFormatter(_MySwordXrefMixin, VerseFormatter):
    """BTB-L3: full-word transliteration over the original script -- the
    heaviest of the three tiers. Unchanged in substance from before this
    redesign (previously BSXB); now stands alone with its own render_verse
    rather than inheriting it from the (now lemma-focused) L1 formatter."""
    abbreviation   = "BTB-L3"
    module_name    = "Berean Transliterated Bible - Level 3"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_TRANSLINEAR_CSS
    verse_rules    = _MYSWORD_TRANSLINEAR_RULES

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
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
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f"<RF q=N{seq}>{note['text']}<Rf> ")
            else:
                core, trail = _split_trailing_punct(token.english)
                parts.append(self.transform_english(core, token.par_class))
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    strongs = sw.stem.strongs
                    lang = 'gk' if sw.lang == 'G' else 'hb'
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
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f"<RF q=N{seq}>{note['text']}<Rf> ")

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
