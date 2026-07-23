"""
verse_formatter/reverse_interlinear.py

BSRB (reverse interlinear) formatters for both e-Sword and MySword, kept
side by side deliberately: this mode is a config-driven layout (English on
top, source words stacked below in <ilb>/<lg>/<lm> rows) edited in lockstep
across both platforms, so this is the one file to open when tuning BSRB
layout or styling.
"""

import re

from .base import VerseFormatter, _ESwordXrefMixin, _MySwordXrefMixin, parse_headers, _INLINE_HEADER_CLASSES

# ==================================================== overlong lemma rows

# Reverse-interlinear rows go ragged when one English gloss aligns to many
# source words (a multi-word Hebrew number under a short English numeral) or
# to one long-transliteration word -- e-Sword's CSS wraps the overflow into
# uneven sub-rows, and MySword doesn't wrap at all, just runs the row off
# the page (bad: swipe-to-change-chapter makes that unrecoverable). Splitting
# the row up front and emitting one <ilb>/<ilbc> block per lemma-group
# sidesteps both, regardless of what the renderer's CSS does.
#
# Only the *first* group is length-matched against the English label -- the
# lemmas that fit within roughly its width. Every source word after that
# gets its own singleton group rather than being packed into further
# multi-lemma rows: re-applying the length target past the first row tried
# to keep rows evenly sized, but that's a look nobody asked for and it
# obscures which lemma is which. A plain English length would also cap the
# first group at a single lemma whenever a short gloss (e.g. a number)
# aligns to longer transliterations, so VerseFormatter.min_lemma_row_len
# floors it -- a rough character-count proxy for on-screen width, not a
# real measurement, meant to be tuned by eye against the actual target apps.
_TAG_RE = re.compile(r'<[^>]+>')


def _visible_len(html_text: str) -> int:
    """Approximate on-screen character length of already-transformed English
    (transform_english() output), ignoring markup added along the way."""
    return len(_TAG_RE.sub('', html_text))


def _group_source_words(source_words: list, xlits: list, target_len: int) -> list:
    """Build one length-matched first group of aligned (source_word,
    transliteration) pairs -- lemmas added while their cumulative
    transliteration length stays under target_len (the English label's
    visible width) -- then break every remaining source word out into its
    own singleton group. Only the first group is meant to carry the real
    English label; callers render '&nbsp;' in the same tag for the rest, so
    the label's border-bottom rule still draws under every row and the
    continuation rows read as part of the same block instead of an
    unrelated tail.
    """
    target_len = max(target_len, 1)
    pairs = list(zip(source_words, xlits))
    first = []
    length = 0
    i = 0
    while i < len(pairs):
        if first and length >= target_len:
            break
        first.append(pairs[i])
        length += len(pairs[i][1]) + 1
        i += 1
    return [first] + [[pair] for pair in pairs[i:]]

# ============================================================ e-Sword

_ESWORD_INTERLINEAR_CSS = """
ilb, ilbc {display:inline-block; vertical-align:top; margin: 0 0.1em 0.75em;}
ilbc {margin-left:0;}
ilb > *, ilbc > * {display:block; text-align:center; width:100%; max-width:100%;}
trn {border-bottom:1px solid gray; text-align:center; width:100%; border-bottom:2px solid #DDD; margin-bottom:.1em;}
lm {display:inline-block; text-align:center; padding: 0.2em; gap: 3px; font-size:.85em; line-height: 1.0em;} 
ltn {color: green; padding-bottom: 0.2em;} .red i {color: #d6807f;}
lm mb {max-width:100%;} lm > * {display:block} hb, gk {color:#065e69;} 
sb > *, mb > * {vertical-align:normal; font-size:.7em; padding:0; line-height:.8em;}
.acrostic, .ihdg, .subhdg {color:#777; font-style:italic; font-weight:bold;}
.acrostic {text-align:center;} .ihdg {font-weight:normal;} .subhdg {font-style:normal;}
.pshdg, .inscrip, .selah {font-style:italic;}
"""

class ESwordReverseInterlinearFormatter(_ESwordXrefMixin, VerseFormatter):
    abbreviation   = "BSRB"
    module_name    = "BSB Reverse Interlinear Bible"
    file_extension = ".bbli"
    css            = _ESWORD_INTERLINEAR_CSS
    bracket_replacement = ('<i>', '</i>')

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
            parts.append(self.render_header(header))
        if xref_placement == 1:
            parts.append(self.render_crossref(xrefs))

        for i, token in enumerate(tokens):
            if i in skip:
                continue

            is_plain = token.is_plain_text or not token.source_words

            if is_plain:
                english_text = self.transform_english(token.english, token.par_class, token.is_red)
                if token.skip_space_after:
                    pending += english_text
                else:
                    text    = pending + english_text
                    pending = ''
                    parts.append(f'<ilb><eng>{text}</eng><lg></lg></ilb>')
            else:
                english = pending + self.transform_english(token.english, token.par_class, token.is_red)
                pending = ''

                j        = i + 1
                cur_skip = token.skip_space_after
                while cur_skip and j < len(tokens):
                    next_tok = tokens[j]
                    if next_tok.is_plain_text or not next_tok.source_words:
                        english += self.transform_english(next_tok.english, next_tok.par_class, next_tok.is_red)
                        skip.add(j)
                        cur_skip = next_tok.skip_space_after
                        j += 1
                    else:
                        break

                xlits = [self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                         for sw in token.source_words]
                groups = _group_source_words(token.source_words, xlits,
                                               max(_visible_len(english), self.min_lemma_row_len))

                for gi, group in enumerate(groups):
                    segments = []
                    for sw, xlit in group:
                        morph = sw.stem.morph or sw.stem.token_class
                        morph_tags = ''.join([f'<tvm>{mph}</tvm>' for mph in morph.split('|')])
                        lang_class = 'gk' if sw.lang == 'G' else 'hb'
                        segments.append(
                            f'<lm><{lang_class}>{sw.text}</{lang_class}><ltn>{xlit}</ltn>'
                            f'<sb><num>{sw.stem.strongs}</num></sb><mb>{morph_tags}</mb></lm>'
                        )
                    label = english if gi == 0 else '&nbsp;'
                    tag   = 'ilb' if gi == 0 else 'ilbc'
                    parts.append(f'<{tag}><trn>{label}</trn><lg>{"".join(segments)}</lg></{tag}>')
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)

# ============================================================ MySword

_MYSWORD_INTERLINEAR_CSS = """
ilb, ilbc {display:inline-flex; flex-direction:column; align-items:stretch; vertical-align:top; margin:0.2em 0.2em 0.75em;}
ilbc {margin-left:0;}
ilb t, ilbc t {width: 100%; text-align:center; border-bottom: 2px solid #eee;}
ilb > lg, ilbc > lg {display:inline-flex; flex-direction:row; justify-content:center; gap:4px;}
lm {display:inline-flex; flex-direction:column; align-items:stretch; width:100%;}
lm > * {text-align: center}
ilb ro, ilbc ro {color:#065e69;} ilb rt, ilbc rt {color:#7a10ad;} ilb i, ilbc i {color: #444;}
.wjc i {color: #8f4b4b;}
.strong, .morph {font-size:0.7em}
.acrostic, .ihdg, .subhdg {color:#777; font-style:italic; font-weight:bold;}
.acrostic {text-align:center;} .ihdg {font-weight:normal;} .subhdg {font-style:normal;}
.pshdg, .inscrip, .selah {font-style:italic;}
ilb mg, ilbc mg {display:inline-flex; flex-direction:row; flex-wrap:wrap; gap:3px; justify-content:center}
"""

_MYSWORD_INTERLINEAR_RULES = ""  # GBF tags handled natively by MySword

class MySwordReverseInterlinearFormatter(_MySwordXrefMixin, VerseFormatter):
    abbreviation   = "BSRB"
    module_name    = "BSB Reverse Interlinear Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_INTERLINEAR_CSS
    verse_rules    = _MYSWORD_INTERLINEAR_RULES
    bracket_replacement = ('<i>', '</i>')

    def render_header(self, raw: str) -> str:
        """Unlike the other MySword formatters (which dump every header class
        into <TS>, undifferentiated -- see _MySwordXrefMixin), the interlinear
        keeps acrostic/ihdg/subhdg as inline spans+<br/> instead (same markup
        the base class's default policy uses for e-Sword): those read as part
        of the verse's own running text here, not a section title, so folding
        them into MySword's native title bar alongside real headings would
        lose that. hdg/suphdg still go through <TS> -- that's the only way
        MySword shows a heading at all, unlike e-Sword's always-on pericopes.
        """
        parts = []
        for cls, text in parse_headers(raw):
            if cls in _INLINE_HEADER_CLASSES:
                parts.append(f'<span class="{cls}">{text}</span><br/>')
            else:
                parts.append(f"<TS>{text}<Ts>")
        return ''.join(parts)

    def render_verse(self, tokens, header=None, note_id_map=None,
                     xrefs=None, xref_placement=0) -> str:
        """Render tokens with GBF tags for MySword interlinear display."""
        note_id_map = note_id_map or {}
        xrefs = xrefs or []
        parts = []
        if header:
            parts.append(self.render_header(header))
        if xref_placement == 1:
            parts.append(self.render_crossref(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(self.transform_english(token.english, token.par_class, token.is_red))
                for note in token.notes:
                    parts.append(f"<RF q={note_id_map.get(note['noteId'], note['noteId'])}>{note['text']}<Rf>")
            else:
                english = self.transform_english(token.english, token.par_class, token.is_red)
                xlits = [self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                         for sw in token.source_words]
                groups = _group_source_words(token.source_words, xlits,
                                               max(_visible_len(english), self.min_lemma_row_len))

                for gi, group in enumerate(groups):
                    segments = []
                    for sw, xlit in group:
                        strongs = sw.stem.strongs
                        # Some source words genuinely have no Strong's number (the
                        # direct object marker, prefixed prepositions/conjunctions
                        # as their own token, etc.) -- a bare <W> tag with no
                        # number doesn't reserve a row the way a real <WH.../WG...>
                        # link does, so sibling <lm>s in the same row go out of
                        # vertical alignment. A truly empty <span> doesn't fix
                        # this either: every direct child of <lm> is a flex item
                        # (lm's own display:inline-flex; flex-direction:column),
                        # and flex items size to their content -- an empty span
                        # has zero content, so it collapses to zero height
                        # regardless of font-size. A non-breaking space gives it
                        # real content to size against, so it reserves the row.
                        strong_tag = f'<W{strongs}>' if strongs else '<span class="strong">&nbsp;</span>'
                        # sw.stem.morph is the resolved RMAC code (bsb_tables.tokens.morph);
                        # falls back to the raw BSB Parsing string when unresolved -- still
                        # displayed for the reader, just not a dictionary-linkable code, so
                        # MySword's own lookup silently fails to match it rather than showing
                        # nothing at all.
                        morph = sw.stem.morph or sw.stem.token_class
                        morph_tags = ''.join([f'<WT{mph}>' for mph in morph.split('|')])

                        segments.append(f"<lm><ro>{sw.text}</ro><rt>{xlit}</rt>{strong_tag}<mg>{morph_tags}</mg></lm>")
                    label = english if gi == 0 else '&nbsp;'
                    tag   = 'ilb' if gi == 0 else 'ilbc'
                    parts.append(f"<{tag}><t>{label}</t><lg>{''.join(segments)}</lg></{tag}>")
                for note in token.notes:
                    parts.append(f"<RF q={note_id_map.get(note['noteId'], note['noteId'])}>{note['text']}<Rf>")

            # A Psalm superscription (pshdg) runs across several tokens (see
            # table_composer.py's forward par_class tracking) -- add a break
            # once at the end of that run, not after every token in it, so
            # the poem body starts on its own line instead of running on.
            if token.par_class == 'pshdg' and (next_token is None or next_token.par_class != 'pshdg'):
                parts.append('<br/>')

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)
