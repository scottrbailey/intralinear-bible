"""
verse_formatter/intralinear.py

Three-tier BTB-L1/L2/L3 formatters for both e-Sword and MySword, kept side
by side deliberately: these two platforms' CSS and render_verse() are
edited together to keep them visually matched, so this file is the one to
open when tuning any of the three tiers' layout or styling for either
target.

Every tier shares one shape: `ro` is always the primary line -- the one
readers actually track, always populated, always the Strong's link, higher
contrast -- and `rt` is always secondary: a lower-contrast helper line,
shown below `ro`. Only *what content* fills each role changes per tier:

    Tier   ro (primary, linked)        rt (secondary, helper)
    L1     lemma transliteration       no real content, but still a
                                        space -- see below
    L2     word's own transliteration  lemma transliteration, only when
                                        it differs from ro (else a space)
    L3     original script             word's own transliteration (always)

`rt` is never truly *absent* in any tier, even L1, where it has nothing to
say: `rt`'s `min-height` reserves a second line's worth of box height, and
`.ilb`'s `vertical-align:middle` needs that reserved height to lift `ro`
above the English baseline into its intended raised, superscript-like
position. Confirmed on-device: omitting `rt` entirely for L1 (rather than
emitting `<rt> </rt>`) dropped `ro` straight onto the baseline instead.

`ro` written before `rt` in the markup (not just visually on top via CSS)
is deliberate: default (non-reversed) `flex-direction: column` then puts
DOM order and visual order in agreement, so the source itself reads
top-to-bottom the same way the rendered page does.

Because every tier now has the same shape, all three share one
render_verse() per platform (`_ESwordBTBFormatter` / `_MySwordBTBFormatter`
below) -- each concrete tier class only supplies `_primary_content()` and
(if it has one) `_secondary_content()`. Earlier revisions of this file had
`ro`/`rt`'s roles reversed (primary always `rt`, on top only via CSS
positioning, secondary `ro`) and L1/L2 sharing render_verse() while L3
stood alone; this version replaces both of those.

MySword implemented first: e-Sword has no way to make an inline dictionary
link directly, so its markup is paired with a hidden `<num>` tag
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

COLOR_TRANSLIT = '#475eaf'   # primary line: always linked, always this color
COLOR_ANCIENT = '#2f747a'    # secondary line: helper text, lower contrast
COLOR_UNLINKED = '#666666'   # primary line when there's no Strong's number to link

# Strong's numbers whose lemma citation form is too far from -- and too
# common a mismatch against -- their inflected forms to be worth showing:
# a handful of extremely frequent Greek function words with suppletive
# paradigms (all forms collapsed under one Strong's number), where the
# lemma reads as noise rather than a helpful stem cue. Confirmed by user
# testing: G3588 (ho/he/to, the article), G1473 (ego/mou, "I"), G4771
# (sy/sou, "you"). For these, wherever a tier would otherwise show the
# lemma, it falls back to the word's own transliteration instead: L1's
# primary line (where the lemma normally lives) and L2's secondary line
# (where the lemma normally shows up only when it differs from L2's
# word-transliteration primary).
LEMMA_SUPPRESSED_STRONGS = {'G3588', 'G1473', 'G4771'}

# Shared by both MySword and e-Sword.
#
# .acrostic/.ihdg/.subhdg stay plain inline spans (no display/float trick —
# display:block broke the line both before and after in e-Sword, separating
# the span from the verse number; a float:left/width:100% attempt at "same
# line, wrap only after" then behaved unpredictably against the real
# rendering engine too) — render_header() appends a literal <br/> after each
# span instead, which is guaranteed to force the wrap without touching
# whatever precedes it on the line.
#
# ruby ro / ruby rt: `ro` is always primary (populated in every tier, in
# every word that has one) so its `min-height:1em` is just insurance; `rt`
# is the one that's sometimes a bare space (L2, see
# _MySwordBTBFormatter._secondary_content's docstring) or entirely absent
# (L1), so *its* min-height is what actually prevents the baseline-riding
# bug a collapsed box used to cause.
_INTRALINEAR_CSS = dedent(f'''\
    .acrostic, .ihdg, .subhdg {{color:#777; font-style:italic; font-weight:bold;}}
    .acrostic {{text-align:center;}}
    .ihdg {{font-weight:normal;}}
    .subhdg {{font-style:normal;}}
    .pshdg, .inscrip, .selah {{font-style:italic;}}
    .ilb {{display:inline-block; vertical-align:middle; padding:4px 0; position:relative; font-size:0.8em; line-height:1;}}
    .ilb ruby {{display:inline-flex; flex-direction:column;}}
    ruby ro {{display:block; min-height:1em; font-size:1.1em; color:{COLOR_TRANSLIT}; text-align:center;}}
    ruby ro.unlinked {{color: {COLOR_UNLINKED};}}
    ruby rt {{display:block; min-height:1em; font-size:1.1em; color:{COLOR_ANCIENT}; text-align:center;}}
''')
# .hb ruby ro's larger font-size (helps Hebrew's small vowel points stay
# legible) used to live in the shared block above -- fine back when every
# formatter's `ro` held the original script, but not since BTB-L1/L2 put a
# transliteration there instead. Added explicitly only to the CSS blocks
# below whose `ro` still holds the real original script (L3).


# ============================================================ e-Sword
#
# e-Sword's one real constraint: it has no way to make an inline dictionary
# link directly, so `ro` (always primary, see module docstring) is paired
# with a hidden `<num>` tag, CSS-positioned over the visible line, that
# e-Sword auto-links to that Strong's number's dictionary entry
# (`.ilb ruby ~ * {...; opacity:0}`). That overlay is positioned relative
# to `.ilb`'s own box and has survived real cross-platform (iOS/Android/
# desktop) testing already; it doesn't care which content sits at the top
# of that box, only that *something* reliably does, so it needed no changes
# when `ro`/`rt`'s roles were swapped.

_ESWORD_LEMMA_CSS = (_INTRALINEAR_CSS +
    '.ilb ruby ~ * {position:absolute; z-index:9999; top:0.5em; left:0; right:0; text-align:center; opacity:0;}'
)
_ESWORD_STACKED_CSS = _ESWORD_LEMMA_CSS + f'\n.hb ruby ro {{font-size:1.2em;}}'


class _ESwordBTBFormatter(_ESwordXrefMixin, VerseFormatter):
    """Shared render_verse() for all three e-Sword BTB tiers. Each concrete
    tier supplies `_primary_content()` (always required -- `ro` is never
    optional) and `_secondary_content()`.

    `_secondary_content()` returning a real string is genuinely meant to be
    read; returning `' '` (a space, not `''` and not None -- see below) for
    the "nothing to add" case reserves the same line-height as a populated
    `rt` without displaying anything, so every word centers identically
    inside `.ilb`'s vertical-align:middle box regardless of whether it has
    real secondary content. Two failure modes this guards against, both
    confirmed on real devices:
      - `''` collapses to no meaningful height, riding that one word's `ro`
        down to the English baseline while its neighbors (real two-line
        boxes) stay elevated -- a visibly jagged line.
      - None (omitting the `<rt>` tag entirely, not just its content) is
        worse still: it drops the *whole tier's* box height by a full
        line, which un-raises `ro` from its intended superscript-like
        position back onto the baseline even when every word in that tier
        is consistently one line (L1's case) -- there's no sibling to look
        jagged against, but the raised positioning depends on the reserved
        height existing at all, not on it varying.
    The default implementation below still returns None for a formatter
    that hasn't overridden it, since forgetting to implement
    `_secondary_content()` should be obviously broken rather than silently
    look right -- but no concrete tier actually returns None; even L1
    overrides it to return `' '` unconditionally.
    """
    file_extension = ".bbli"

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        raise NotImplementedError

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str | None:
        return None

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
                words = []
                for sw in token.source_words:
                    word_xlit = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    strongs   = sw.stem.strongs
                    primary   = self._primary_content(sw, word_xlit)
                    secondary = self._secondary_content(sw, word_xlit, primary)
                    lang     = 'gk' if sw.lang == 'G' else 'hb'
                    ro_class = ' class="unlinked"' if not strongs else ''
                    num_tag  = f'<num>{strongs}</num>' if strongs else ''
                    rt_tag   = f'<rt>{secondary}</rt>' if secondary is not None else ''
                    words.append(
                        f'<span class="ilb {lang}">'
                        f'<ruby><ro{ro_class}>{primary}</ro>{rt_tag}</ruby>'
                        f'{num_tag}'
                        f'</span>'
                    )
                parts.append(' '.join(words))
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


class ESwordLemmaFormatter(_ESwordBTBFormatter):
    """BTB-L1: lemma transliteration only -- links to the Strong's entry via
    a readable word ('reshit') instead of a bare number ('H7225'). No
    secondary *content* -- L1 is meant to get you to the lexicon as
    directly as possible, not to teach pronunciation -- but `_secondary_
    content()` still returns a bare space rather than None: `rt`'s
    min-height reserves a second line's worth of box height even with
    nothing in it, and `.ilb`'s vertical-align:middle needs that reserved
    height to lift `ro` above the English baseline (confirmed on-device --
    omitting `rt` entirely dropped `ro` straight onto the baseline instead
    of the intended raised, superscript-like position)."""
    abbreviation = "BTB-L1"
    module_name  = "Berean Transliterated Bible - Level 1"
    css          = _ESWORD_LEMMA_CSS

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        strongs = sw.stem.strongs
        if strongs in LEMMA_SUPPRESSED_STRONGS:
            return word_xlit
        return sw.stem.lemma_translit or word_xlit

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str:
        return ' '


class ESwordLemmaDetailFormatter(_ESwordBTBFormatter):
    """BTB-L2: the word's own transliteration, primary and always shown --
    unlike L1, the point here is to read the actual inflected form
    continuously. The lemma becomes a secondary, occasional helper note
    below, shown only when it adds something beyond what's already on the
    primary line (see LEMMA_SUPPRESSED_STRONGS for the extremely common
    Greek function words excluded from that comparison entirely)."""
    abbreviation = "BTB-L2"
    module_name  = "Berean Transliterated Bible - Level 2"
    css          = _ESWORD_LEMMA_CSS

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        return word_xlit

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str:
        strongs = sw.stem.strongs
        if strongs in LEMMA_SUPPRESSED_STRONGS:
            return ' '
        lemma_xlit = sw.stem.lemma_translit or word_xlit
        return lemma_xlit if lemma_xlit != primary else ' '


class ESwordStackedFormatter(_ESwordBTBFormatter):
    """BTB-L3: the original script, primary and linked -- the heaviest of
    the three tiers, and the most literal mapping of "tap what you're
    reading" onto the Strong's link. The word's own transliteration is
    always the secondary line, as a pronunciation aid -- unlike L2's lemma,
    it never coincides with the primary line's content, so there's no
    "matches, omit it" case here."""
    abbreviation = "BTB-L3"
    module_name  = "Berean Transliterated Bible - Level 3"
    css          = _ESWORD_STACKED_CSS

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        return sw.text

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str:
        return word_xlit


# ============================================================ MySword
#
# Same three-tier shape as e-Sword (see module docstring): `ro` always
# primary and linked, `rt` always secondary. MySword's `ro` carries a real
# `<a href="s...">` anchor when there's a Strong's number, instead of
# e-Sword's hidden-`<num>`-overlay workaround.
#
# `ro`'s "unlinked" class -- e-Sword needs it as an explicit CSS hook since
# it never has a real anchor to key off of; MySword's `ro` does sometimes
# have one (`<a>`), so it could in principle rely on "gray unless there's
# an anchor to override it" the way this used to work before `rt`/`ro`
# swapped roles. Kept explicit here anyway: one shared
# `ruby ro.unlinked {...}` rule in _INTRALINEAR_CSS now covers both
# platforms identically instead of MySword needing its own separate
# gray-by-default mechanism.

_MYSWORD_LEMMA_CSS = _INTRALINEAR_CSS + \
    f'ruby ro a {{text-decoration: none; color:{COLOR_TRANSLIT};}}'
_MYSWORD_TRANSLINEAR_CSS = _MYSWORD_LEMMA_CSS + f'\n.hb ruby ro {{font-size:1.2em;}}'

_MYSWORD_BTB_RULES = ''


class _MySwordBTBFormatter(_MySwordXrefMixin, VerseFormatter):
    """Shared render_verse() for all three MySword BTB tiers -- see
    _ESwordBTBFormatter's docstring, same contract and the same
    never-actually-None-in-practice caveat on `_secondary_content()`."""
    file_extension = ".bbl.mybible"
    verse_rules    = _MYSWORD_BTB_RULES

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        raise NotImplementedError

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str | None:
        return None

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
                words = []
                for sw in token.source_words:
                    word_xlit = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    strongs   = sw.stem.strongs
                    primary   = self._primary_content(sw, word_xlit)
                    secondary = self._secondary_content(sw, word_xlit, primary)
                    lang     = 'gk' if sw.lang == 'G' else 'hb'
                    ro_class = ' class="unlinked"' if not strongs else ''
                    # With no strongs number, `<a href="s">` would be a real
                    # but broken link, so `ro` gets plain text plus the
                    # 'unlinked' class instead, reading as "known
                    # unavailable" rather than a dead link (see
                    # _INTRALINEAR_CSS's `ruby ro.unlinked` rule).
                    if strongs:
                        ro = f'<ro{ro_class}><a href="s{strongs}">{primary}</a></ro>'
                    else:
                        ro = f'<ro{ro_class}>{primary}</ro>'
                    rt = f'<rt>{secondary}</rt>' if secondary is not None else ''
                    words.append(
                        f'<span class="ilb {lang}"><ruby>{ro}{rt}</ruby></span>'
                    )
                parts.append(' '.join(words))
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


class MySwordLemmaFormatter(_MySwordBTBFormatter):
    """BTB-L1: lemma transliteration only -- links to the Strong's entry via
    a readable word ('reshit') instead of a bare number ('H7225'). No
    secondary *content* -- L1 is meant to get you to the lexicon as
    directly as possible, not to teach pronunciation -- but `_secondary_
    content()` still returns a bare space rather than None: `rt`'s
    min-height reserves a second line's worth of box height even with
    nothing in it, and `.ilb`'s vertical-align:middle needs that reserved
    height to lift `ro` above the English baseline (confirmed on-device --
    omitting `rt` entirely dropped `ro` straight onto the baseline instead
    of the intended raised, superscript-like position)."""
    abbreviation = "BTB-L1"
    module_name  = "Berean Transliterated Bible - Level 1"
    css          = _MYSWORD_LEMMA_CSS

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        strongs = sw.stem.strongs
        if strongs in LEMMA_SUPPRESSED_STRONGS:
            return word_xlit
        return sw.stem.lemma_translit or word_xlit

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str:
        return ' '


class MySwordLemmaDetailFormatter(_MySwordBTBFormatter):
    """BTB-L2: the word's own transliteration, primary and always shown --
    unlike L1, the point here is to read the actual inflected form
    continuously. The lemma becomes a secondary, occasional helper note
    below, shown only when it adds something beyond what's already on the
    primary line (see LEMMA_SUPPRESSED_STRONGS for the extremely common
    Greek function words excluded from that comparison entirely).

    Earlier revisions of this file had this relationship backwards -- lemma
    primary/always-shown, word-transliteration secondary -- and separately
    went through a phase of hiding the secondary line whenever it matched
    the primary. Both were confirmed on real devices to cause problems:
    hiding the line anyone's tracking continuously breaks their reading
    rhythm (worse the more often lemma and word coincide, which is routine
    for Greek's short, common function words), and it invites "is this word
    missing from the source?" questions that a genuinely optional *helper*
    line doesn't. Promoting the word's own transliteration to primary (this
    revision) means the line people actually read is never in question;
    the lemma is now free to be genuinely optional without costing anyone
    their place.
    """
    abbreviation = "BTB-L2"
    module_name  = "Berean Transliterated Bible - Level 2"
    css          = _MYSWORD_LEMMA_CSS

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        return word_xlit

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str:
        strongs = sw.stem.strongs
        if strongs in LEMMA_SUPPRESSED_STRONGS:
            return ' '
        lemma_xlit = sw.stem.lemma_translit or word_xlit
        return lemma_xlit if lemma_xlit != primary else ' '


class MySwordStackedFormatter(_MySwordBTBFormatter):
    """BTB-L3: the original script, primary and linked -- the heaviest of
    the three tiers, and the most literal mapping of "tap what you're
    reading" onto the Strong's link. The word's own transliteration is
    always the secondary line, as a pronunciation aid -- unlike L2's lemma,
    it never coincides with the primary line's content, so there's no
    "matches, omit it" case here."""
    abbreviation = "BTB-L3"
    module_name  = "Berean Transliterated Bible - Level 3"
    css          = _MYSWORD_TRANSLINEAR_CSS

    @staticmethod
    def _primary_content(sw, word_xlit: str) -> str:
        return sw.text

    @staticmethod
    def _secondary_content(sw, word_xlit: str, primary: str) -> str:
        return word_xlit
