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

import html
import re
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from textwrap import dedent
from collections.abc import Callable
from translit import make_transliterator

# data/books.db (see utils/build_books_table.py) — our display abbreviation
# ('Joh', '1Co', ...) -> usfm_number, for MySword's <RX b.c.v> cross-ref tags.
_BOOKS_DB = Path(__file__).resolve().parent / "data" / "books.db"
with sqlite3.connect(_BOOKS_DB) as _conn:
    ABBREV_TO_BOOK_NUM = {r[0]: r[1] for r in
                           _conn.execute("SELECT display_abbrev, usfm_number FROM books")}


MODULE_DESCRIPTION = dedent("""\
    Berean Standard Bible with inline Hebrew and Greek transliteration.
    Source language data from WLC (OT) and SBLGNT (NT) via Clear Bible
    Alignments project (CC BY 4.0).""")


# ============================================================ cross-references

@dataclass
class Reference:
    """One Bible reference target, format-agnostic.

    verse is None for a range with no single verse target — a whole-chapter
    range ("Genesis 4-9") or a book span ("Jos-Mal"); book/chapter are still
    populated in that case (chapter defaults to 1 for a book span) so a
    formatter can choose to link to that chapter's start while still
    *displaying* the full range via label. book/chapter are None only when
    the text couldn't be parsed as a reference at all.
    """
    book: str | None = None
    chapter: int | None = None
    verse: int | None = None
    end_chapter: int | None = None
    end_verse: int | None = None
    label: str | None = None


# Matches our own abbreviated ref strings (as produced by
# utils/import_bsb_table.py's crossref conversion, and by bsb_xrefs.json):
#   "Joh 1:1-5"        book chapter:verse-verse
#   "1Ch 15:29-16:3"   book chapter:verse-chapter:verse (crosses chapters)
#   "Gen 4-9"          book chapter-chapter (whole-chapter range, no verse)
_XREF_REF_RE = re.compile(
    r'^(?P<book>\S+)\s+(?P<chap>\d+)'
    r'(?:'
        r':(?P<verse>\d+)(?:-(?:(?P<end_chap>\d+):)?(?P<end_verse>\d+))?'
        r'|'
        r'-(?P<chap_end>\d+)'
    r')?$'
)
# "Jos-Mal" — a book-only span, no chapter/verse at all.
_XREF_BOOK_SPAN_RE = re.compile(r'^(?P<book>\S+)-(?P<book2>\S+)$')


def parse_reference(text: str) -> Reference:
    """One ';'-split piece of xref text -> a Reference. Format-agnostic."""
    text = text.strip()
    m = _XREF_REF_RE.match(text)
    if m:
        book = m.group('book')
        chapter = int(m.group('chap'))
        verse = m.group('verse')
        if verse is not None:
            end_chap  = m.group('end_chap')
            end_verse = m.group('end_verse')
            return Reference(
                book=book, chapter=chapter, verse=int(verse),
                end_chapter=int(end_chap) if end_chap else None,
                end_verse=int(end_verse) if end_verse else None,
            )
        # chapter-only, with or without a chapter range — no single verse
        chap_end = m.group('chap_end')
        return Reference(book=book, chapter=chapter,
                          end_chapter=int(chap_end) if chap_end else None, label=text)

    m = _XREF_BOOK_SPAN_RE.match(text)
    if m:
        return Reference(book=m.group('book'), chapter=1, label=text)

    return Reference(label=text)


def _default_ref_label(ref: Reference) -> str:
    """Build "book chapter:verse[-verse]" text for a Reference with no label."""
    if ref.book is None or ref.chapter is None:
        return ''
    if ref.verse is None:
        text = f"{ref.book} {ref.chapter}"
        return f"{text}-{ref.end_chapter}" if ref.end_chapter else text
    text = f"{ref.book} {ref.chapter}:{ref.verse}"
    if ref.end_verse:
        text += f"-{ref.end_chapter}:{ref.end_verse}" if ref.end_chapter else f"-{ref.end_verse}"
    return text


# ============================================================ supplied words

# The BSB text marks two distinct kinds of translator-supplied words, not
# one — confirmed independent (both can appear in the same cell, e.g.
# "[and] it {will} become") and never nested in one another.
#
# [Square brackets] (18,688 rows) skew toward substantive, broadly-supplied
# content: articles ("the"), conjunctions ("and", "or", "but"), pronouns
# ("it", "his", "them"), referents/proper nouns ("Jesus", "Son", "man") —
# words with no source-language counterpart at all, freely added for English
# readability.
#
# {Curly braces} (1,270 rows) skew overwhelmingly toward English auxiliary/
# modal/copula verbs — "do/does/did", "will/shall/would/should/may/can",
# "is/are/was/were/am/be", "let" — plus a smaller set of phrasal-verb/idiom
# particles ("away", "down", "up", "back", "over", "again", "together",
# "out", "about", "with", "from"). These read as grammatically implied by
# the source verb's own tense/mood/aspect marking (Hebrew/Greek encode that
# in the verb form itself; English has to spell it out with a separate
# word) rather than freely-added content — a narrower, different category
# from bracket-marked words, so it gets its own independent control
# (brace_replacement) rather than reusing bracket_replacement.
#
# Both strip by default but are preserved verbatim in the data itself so
# formats that want them (KJV-italics-style, or a future forward
# interlinear) can keep or restyle them instead.
_SUPPLIED_WORD_RE = re.compile(r'\[([^\[\]]*)\]')
_IMPLIED_WORD_RE  = re.compile(r'\{([^{}]*)\}')


# ================================================================= word order

# AlignedToken.english carries trailing punctuation/quote marks glued
# directly onto the word (e.g. "the earth."), with no separate field for
# them — fine for reverse interlinear, where English and the source-word
# annotation stack in separate rows, but wrong for intralinear's inline
# layout: rendered as-is, the punctuation lands between the English word
# and its transliteration/source-word <span>, leaving the annotation
# hanging after it instead of capping the whole word+annotation unit.
# Based on the closing-punctuation set already used for import-time cleanup
# (see utils/import_bsb_table.py's _SPACE_BEFORE_PUNCT_RE), minus ']' — a
# trailing ']' is a supplied-word bracket's own closer (transform_english()'s
# bracket_replacement needs the opening '[' and closing ']' to still be a
# balanced pair when it runs), not punctuation to relocate; splitting it off
# here left transform_english() looking at an unbalanced "[Jesus" with no
# closing bracket at all, so it silently stopped matching.
_TRAILING_PUNCT_RE = re.compile(r'([,.;:!?)’”]+)$')


def _split_trailing_punct(text: str) -> tuple:
    """Split off a token's trailing punctuation/quote marks so the caller can
    render them after the source-word annotation instead of before it."""
    m = _TRAILING_PUNCT_RE.search(text)
    if not m:
        return text, ''
    return text[:m.start()].rstrip(), m.group(1)


# ============================================================ Par-column classes

# Par-column paragraph classes that apply to a *run* of tokens' English text
# (not a standalone label the way header classes are) — 'pshdg' (Psalm
# superscriptions, e.g. "A Psalm of David, when he fled from his son
# Absalom"), 'inscrip' (quoted inscriptions, e.g. Exodus 28:36's "HOLY TO
# THE LORD"), 'selah' (liturgical refrains). Collapsed to one shared italic
# treatment rather than styled individually — they're all "this is set
# apart from the surrounding narrative" in the same way, and giving each
# its own rule would start down the same road as fully styling Par (indent
# levels, lists, tabs) that was deliberately set aside as a bigger, separate
# project (see TABLE_COMPOSER_STATUS.md).
#
# Red-letter (words of Christ) is a related but separate concern, tracked
# on AlignedToken.is_red rather than folded into par_class — its boundaries
# work differently (a fresh <span class=|red|> per red phrase, not once per
# paragraph like the classes above, and it fuses into indent-level class
# names like 'indentred1'; see table_composer.py's _extract_is_red()) and,
# given the OT/NT red-letter completeness question, it's opt-in per build
# via the writer's red_letter option rather than on by default.
_ITALIC_PAR_CLASSES = {'pshdg', 'inscrip', 'selah'}


# ================================================================== headings

# Raw Hdg cell shape: one or more "<p class=|CLASS|>text" segments, optionally
# followed by <br> and more text, back to back with no separator between
# segments. A plain string with no wrapper (e.g. AlignmentComposer's
# bsb_annotations.json headers, already clean text) is treated as a single
# 'hdg'-classed segment. 'pshdg' segments are dropped — confirmed to be
# Par-column content misfiled into Hdg (see docs/BSB_TABLES_SOURCE_ERRORS.md
# investigation); segments with no real text (e.g. 'list2') are dropped too.
_HEADER_SEGMENT_RE = re.compile(r'<p class=\|(\w+)\|>')
_BR_RE = re.compile(r'<br\s*/?>', re.I)


def parse_headers(raw: str) -> list:
    """Raw Hdg cell -> list of (class, text) segments, cleaned and filtered.

    Header classes seen in bsb_tables.tsv: hdg (normal section heading),
    subhdg (nested heading), ihdg (Song of Solomon speaker labels — treated
    the same as any other class; it's up to the caller/subclass to style it
    differently if desired), acrostic (Psalm 119 stanza letters), suphdg
    (compound multi-segment Psalms "BOOK N" headers).
    """
    if not raw:
        return []
    matches = list(_HEADER_SEGMENT_RE.finditer(raw))
    if not matches:
        text = _clean_header_text(raw)
        return [('hdg', text)] if text else []

    segments = []
    for i, m in enumerate(matches):
        cls   = m.group(1)
        start = m.end()
        end   = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        text  = _clean_header_text(raw[start:end])
        if not text or cls == 'pshdg':
            continue
        segments.append((cls, text))
    return segments


def _clean_header_text(text: str) -> str:
    text = _BR_RE.sub('', text)
    return html.unescape(text).strip()


# Header classes MySword/e-Sword's own built-in pericope display doesn't
# cover — see VerseFormatter.render_header(). 'hdg' and 'suphdg' are real
# section headings, which those native pericopes already show.
_INLINE_HEADER_CLASSES = {'acrostic', 'ihdg', 'subhdg'}

# Shared by both MySword and e-Sword.
#
# .acrostic/.ihdg/.subhdg stay plain inline spans (no display/float trick —
# display:block broke the line both before and after in e-Sword, separating
# the span from the verse number; a float:left/width:100% attempt at "same
# line, wrap only after" then behaved unpredictably against the real
# rendering engine too) — render_header() appends a literal <br/> after each
# span instead, which is guaranteed to force the wrap without touching
# whatever precedes it on the line.
_INTRALINEAR_CSS = dedent('''\
    .acrostic, .ihdg, .subhdg {color:#777; font-style:italic; font-weight:bold;}
    .acrostic {text-align:center;}
    .ihdg {font-weight:normal;}
    .subhdg {font-style:normal;}
    .pshdg, .inscrip, .selah {font-style:italic;}
    .ilb {display:inline-block; vertical-align:middle; padding:4px 0; position:relative; font-size:0.8em; line-height:1;}
    .ilb ruby {display:inline-flex; flex-direction:column;}
    ruby > ro {display:block; color:#1ca0b1; text-align:center;}
    ruby > rt {display:block; font-size:1.1em; color: blue;}
    ruby > rt.unlinked {color: #7a8fa6;}
''')

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
      bracket_replacement — controls [bracket]-marked supplied-word handling
                     in transform_english(): ('', '') strips them (default),
                     None leaves them untouched, any other (prefix, suffix)
                     pair wraps the bracketed word instead (e.g. ('<i>', '</i>')).
      brace_replacement — same, for {brace}-marked implied words (a distinct,
                     narrower category — see verse_formatter.py's module-level
                     comment above _IMPLIED_WORD_RE); independent of
                     bracket_replacement, same default and value shapes.
      red_letter_tags — (prefix, suffix) wrapping red-letter (words of
                     Christ) text in transform_english() when is_red is set.
                     Default is a CSS-based <span>; e-Sword/MySword override
                     this with their own native red-letter markup instead
                     (<red>...</red>, <FR>...<Fr>) so the reader's own
                     display-setting toggle controls visibility, rather than
                     baking a fixed color into the module's CSS.
    """

    abbreviation:   str = ""
    module_name:    str = ""
    file_extension: str = ""
    description:    str = MODULE_DESCRIPTION
    publish_date:   str = "2020-12-01"
    css:            str = ""
    verse_rules:    str = ""
    bracket_replacement: tuple = ('', '')
    brace_replacement:   tuple = ('', '')
    red_letter_tags:     tuple = ('<span class="red">', '</span>')

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

    # -------------------------------------------------------- supplied words

    def transform_english(self, text: str, par_class: str = None, is_red: bool = False) -> str:
        """Apply this format's handling of [bracket]- and {brace}-marked
        supplied words — independently controlled, see bracket_replacement/
        brace_replacement — plus, when par_class names one of
        _ITALIC_PAR_CLASSES (TableComposer only; always None from
        AlignmentComposer), wraps the result in a same-named <span> styled
        by _INTRALINEAR_CSS. is_red (also TableComposer-only, and only ever
        True when the writer's red_letter option is on — see
        SQLiteBibleWriter) wraps the result in red_letter_tags, nested
        inside the par_class span when both apply.
        """
        if not text:
            return text
        if self.bracket_replacement is not None:
            prefix, suffix = self.bracket_replacement
            text = _SUPPLIED_WORD_RE.sub(rf'{prefix}\1{suffix}', text)
        if self.brace_replacement is not None:
            prefix, suffix = self.brace_replacement
            text = _IMPLIED_WORD_RE.sub(rf'{prefix}\1{suffix}', text)
        if par_class in _ITALIC_PAR_CLASSES:
            text = f'<span class="{par_class}">{text}</span>'
        if is_red:
            prefix, suffix = self.red_letter_tags
            text = f'{prefix}{text}{suffix}'
        return text

    # ------------------------------------------------------------- headings

    def render_header(self, raw: str) -> str:
        """Raw Hdg cell (or plain heading text) -> this format's heading markup.

        MySword and e-Sword both have their own built-in pericope (section
        heading) display, on by default and not something module data can
        suppress — so rendering the main 'hdg'/'suphdg' segments here would
        double them up, and for e-Sword there's no way to make our version
        render above the verse the way its native pericopes do anyway.
        Default: skip those, and render only the classes native pericopes
        don't cover — 'acrostic' (Psalm 119 stanza letters), 'ihdg' (Song of
        Solomon speaker labels), 'subhdg' (nested headings) — each wrapped
        in a same-named <span> (styled by _INTRALINEAR_CSS, so each class
        stays independently stylable and out of the way of
        bracket_replacement's own '<i>' use) followed by a literal <br/> —
        display:block and a float:left/width:100% trick were both tried
        first to get the same "same line as the verse number, then wrap"
        effect without a literal break, and both broke against the real
        e-Sword/MySword rendering engines; a trailing <br/> is what actually
        works reliably. Override to change this policy or the markup.
        """
        return ''.join(
            f'<span class="{cls}">{text}</span><br/>'
            for cls, text in parse_headers(raw)
            if cls in _INLINE_HEADER_CLASSES
        )

    # ------------------------------------------------------- cross-references

    def transform_reference(self, ref: Reference) -> str:
        """One Reference -> this format's own link/tag syntax. Default: plain
        text, no link — safe fallback for formats with no native ref tag.
        """
        return ref.label or _default_ref_label(ref)

    def render_crossref(self, xrefs: list) -> str:
        """A verse's full cross-reference data ({'key':..., 'text':...} dicts,
        as produced by both Composers) -> this format's placement. Default:
        nothing (subclasses that support inline/note-table xrefs override).
        """
        return ''


# ============================================================ e-Sword profiles

class _ESwordXrefMixin:
    """e-Sword's inline marker is a bare '<not>R{key}</not>' note reference;
    the actual reference text lives in the Notes table (see esword_writer.py),
    wrapped per-piece in '<ref>...</ref>' — e-Sword's own reference tag,
    which parses/links its contents natively. Exact verse refs get that
    treatment; degraded ranges (no single verse target) render as plain,
    non-linked text instead of risking a broken or absurd native-parsed link.

    Also carries red_letter_tags: shared by both e-Sword formatters (not
    xref-specific, just the natural shared home given this mixin already
    covers both), e-Sword's own '<red>...</red>' tag, tied to its built-in
    words-of-Christ display toggle rather than a fixed CSS color.
    """
    red_letter_tags = ('<red>', '</red>')

    def transform_reference(self, ref: Reference) -> str:
        if ref.verse is None:
            return ref.label or _default_ref_label(ref)
        return f"<ref>{_default_ref_label(ref)}</ref>"

    def render_crossref(self, xrefs: list) -> str:
        return ''.join(f' <not>R{vx["key"]}</not>' for vx in xrefs)


_ESWORD_INTRALINEAR_CSS = (_INTRALINEAR_CSS +
    '\nruby > ro {opacity:0}\n' +
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

        if header:
            parts.append(self.render_header(header))
        if xref_placement == 1:
            parts.append(self.render_crossref(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(self.transform_english(token.english, token.par_class, token.is_red))
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')
            else:
                core, trail = _split_trailing_punct(token.english)
                parts.append(self.transform_english(core, token.par_class, token.is_red))
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    strongs = sw.stem.strongs
                    # yes I know the ruby / rt tags are semantically inverted - easier to hide rt
                    # <num> is invisible (opacity:0) and only overlays <rt> so e-Sword's own
                    # tap handling resolves a Strong's popup there. With no strongs number,
                    # that tap would silently do nothing, so <rt> gets 'unlinked' instead —
                    # a dimmer, grayish blue reads as "known unavailable" rather than "broken".
                    rt_class = ' class="unlinked"' if not strongs else ''
                    num_tag  = f'<num>{strongs}</num>' if strongs else ''
                    lemmas.append(
                        f'<span class="ilb">'
                        f'<ruby><rt{rt_class}>{xlit}</rt><ro>{sw.text}</ro></ruby>'
                        f'{num_tag}'
                        f'</span>'
                    )
                parts.append(' '.join(lemmas))
                parts.append(trail)
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)


_ESWORD_STACKED_CSS = _INTRALINEAR_CSS + \
    '\nruby > ro {opacity:1}' + \
    '\n.ilb ruby ~ * {position:absolute; z-index:9999; top:0.5em; left:0; right:0; text-align:center; opacity:0;}'


class ESwordStackedFormatter(ESwordIntralinearFormatter):
    abbreviation   = "BSXB"
    module_name    = "Berean Standard Translinear Bible"
    file_extension = ".bbli"
    css            = _ESWORD_STACKED_CSS

_ESWORD_INTERLINEAR_CSS = (
    'ilb {display:inline-flex; flex-direction:column; align-items:center; text-align:center; vertical-align:top; margin:0 0.2em 0.75em 0;}'
    'ilb > lg {display:inline-flex; flex-direction:row; gap:4px; font-size:0.8em; line-height:1.1em;}'
    'lm {display:inline-flex; flex-direction:column; align-items:center; gap:2px;}'
    # Plain block stacking for the word+transliteration pair -- no flex, no
    # ruby, nothing for e-Sword's own native tag handling to hook into.
    'lm > wt {display:inline-block; text-align:center;}'
    'lm > wt > * {display:block;}'
    'sup.num, sup.morph {font-size:0.9em;} lg t {width:100%; border-bottom:2px solid #222;}'
    'lm heb, lm grk {color:#065e69;} lm lat {color:green;} ilb i {color: #444;} red i {color: #8f4b4b;}'
    '.acrostic, .ihdg, .subhdg {color:#777; font-style:italic; font-weight:bold;}'
    '.acrostic {text-align:center;} .ihdg {font-weight:normal;} .subhdg {font-style:normal;}'
    '.pshdg, .inscrip, .selah {font-style:italic;}'
    'lm mb, lm sb {display:inline-flex; flex-wrap:wrap; gap:3px; justify-content:center; width:100%; margin:0 !important; padding: 0 !important;}'
)

class ESwordReverseInterlinearFormatter(_ESwordXrefMixin, VerseFormatter):
    abbreviation   = "BSRB"
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

                segments = []
                for sw in token.source_words:
                    xlit    = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    morph = sw.stem.morph or sw.stem.token_class
                    morph_tags = ''.join([f'<tvm>{mph}</tvm>' for mph in morph.split('|')])
                    word_tag = 'grk' if sw.lang == 'G' else 'heb'
                    # A real <ruby><rt>...</rt><ro>...</ro></ruby> fixed the
                    # word/transliteration overlap on e-Sword iOS, but <rt>
                    # brought two problems along with it that CSS couldn't
                    # override: e-Sword's own hardcoded rt+num Strong's-link
                    # binding (broke <tvm>'s independent link), and native
                    # ruby-text sizing (font-size override had no effect).
                    # <wt> is a plain display:inline-block wrapper with
                    # display:block children instead -- ordinary block-flow
                    # stacking, no flex, no ruby, nothing for e-Sword's
                    # native tag handling to hook into.
                    # The <sb> and <mb> wrappers around the <num> and <tvm> tags are important.
                    # Without them, e-Sword puts a very large space around the links when it does the replacement.
                    segments.append(
                        f'<lm><wt><{word_tag}>{sw.text}</{word_tag}><lat>{xlit}</lat></wt>'
                        f'<sb><num>{sw.stem.strongs}</num></sb>'
                        f'<mb>{morph_tags}</mb></lm>'
                    )

                parts.append(
                    f'<ilb><eng>{english}</eng>'
                    f'<lg>{"".join(segments)}</lg></ilb>'
                )
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)

# ============================================================ MySword profiles

class _MySwordXrefMixin:
    """MySword's RX tag is a bare milestone with no visible label of its own,
    so each tag is followed by its own display text — a note popup containing
    only RX tags and nothing else renders as blank. RX's b.c.v addressing
    gives every reference — even a degraded, no-single-verse one — a real
    click target: an exact ref points at its verse, a whole-chapter/book-span
    range points at the range's first chapter (verse 1), while still
    *displaying* the full range text via the tag's label.

    Also carries red_letter_tags: shared by both MySword formatters (not
    xref-specific, just the natural shared home given this mixin already
    covers both), MySword's own '<FR>...<Fr>' red-letter markup, tied to
    its built-in words-of-Christ display toggle rather than a fixed CSS color.

    And render_header(): unlike the base default (which skips 'hdg'/
    'suphdg' to avoid doubling up with e-Sword's native pericopes — see
    VerseFormatter.render_header()), MySword renders every header class via
    its own '<TS>...<Ts>' title tag, undifferentiated by class.
    """
    red_letter_tags = ('<FR>', '<Fr>')

    def render_header(self, raw: str) -> str:
        return ''.join(f"<TS>{text}<Ts>" for _, text in parse_headers(raw))

    def transform_reference(self, ref: Reference) -> str:
        if ref.book is None or ref.chapter is None:
            return ref.label or ''
        book_num = ABBREV_TO_BOOK_NUM.get(ref.book)
        if not book_num:
            return ref.label or _default_ref_label(ref)

        if ref.verse is not None:
            loc = f"{book_num}.{ref.chapter}.{ref.verse}"
            if ref.end_verse:
                loc += (f"-{ref.end_chapter}.{ref.end_verse}" if ref.end_chapter
                        else f"-{ref.end_verse}")
        else:
            loc = f"{book_num}.{ref.chapter}.1"

        label = ref.label or _default_ref_label(ref)
        return f"<RX{loc}>{label}"

    def render_crossref(self, xrefs: list) -> str:
        parts = []
        for vx in xrefs:
            rx_tags = '; '.join(
                self.transform_reference(parse_reference(piece))
                for piece in vx['text'].split(';') if piece.strip()
            )
            if rx_tags:
                parts.append(f"<RF q=R{vx['key']}>{rx_tags}<Rf>")
        return ''.join(parts)


_MYSWORD_INTRALINEAR_CSS = _INTRALINEAR_CSS +\
    '\nruby > ro {opacity:0} ruby a {text-decoration: none;}'

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
                core, trail = _split_trailing_punct(token.english)
                parts.append(self.transform_english(core, token.par_class, token.is_red))
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
                    strongs = sw.stem.strongs
                    # With no strongs number, `<a href="s">` would be a real but broken
                    # link, so <rt> gets plain text instead — plus 'unlinked' so it reads
                    # as "known unavailable" rather than a dead link (see _INTRALINEAR_CSS).
                    rt_content = f'<a href="s{strongs}">{xlit}</a>' if strongs else xlit
                    rt_class   = ' class="unlinked"' if not strongs else ''
                    lemmas.append(
                        f'<span class="ilb"><ruby><rt{rt_class}>{rt_content}</rt>'
                        f'<ro>{sw.text}</ro></ruby></span>'
                    )
                parts.append(' '.join(lemmas))
                parts.append(trail)
                for note in token.notes:
                    parts.append(f"<RF q={note_id_map.get(note['noteId'], note['noteId'])}>{note['text']}<Rf>")

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self.render_crossref(xrefs))

        return ''.join(parts)

    def preview_transform(self, scripture: str) -> str:
        return self._apply_rules(scripture, self.verse_rules)

_MYSWORD_STACKED_CSS = _INTRALINEAR_CSS + \
    '\nruby a {text-decoration: none;}'

_MYSWORD_STACKED_RULES = ''

class MySwordStackedFormatter(MySwordIntralinearFormatter):
    """Stacked variant: same verse content, different CSS."""
    abbreviation = "BSXB"
    module_name  = "Berean Standard Translinear Bible"
    css          = _MYSWORD_STACKED_CSS
    verse_rules  = _MYSWORD_STACKED_RULES

_MYSWORD_INTERLINEAR_CSS = """
ilb {display:inline-flex; flex-direction:column; align-items:center; vertical-align:top; margin-bottom:0.75em;}
ilb > lg {display:inline-flex; flex-direction:row; gap:2px;}
lm {display:inline-flex; flex-direction:column; align-items:center;}
ilb ro {color:#065e69;} ilb rt {color:#7a10ad;} ilb i {color: #444;}
.wjc i {color: #8f4b4b;}
.strong, .morph {font-size:0.7em}
.acrostic, .ihdg, .subhdg {color:#777; font-style:italic; font-weight:bold;}
.acrostic {text-align:center;} .ihdg {font-weight:normal;} .subhdg {font-style:normal;}
.pshdg, .inscrip, .selah {font-style:italic;}
ilb mg {display:inline-flex; flex-wrap:wrap; gap:3px; justify-content:center; width:100%;}
"""

_MYSWORD_INTERLINEAR_RULES = ""  # GBF tags handled natively by MySword

class MySwordReverseInterlinearFormatter(_MySwordXrefMixin, VerseFormatter):
    abbreviation   = "BSRB"
    module_name    = "BSB Reverse Interlinear Bible"
    file_extension = ".bbl.mybible"
    css            = _MYSWORD_INTERLINEAR_CSS
    verse_rules    = _MYSWORD_INTERLINEAR_RULES
    # Supplied words (no source-language counterpart, e.g. "[he] said") get
    # italicized here rather than stripped (the base default) or bracketed --
    # this is the class-attribute override transform_english() is built for,
    # so it's scoped to just this formatter and doesn't touch the intralinear
    # ones, which keep stripping.
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
                segments = []
                for sw in token.source_words:
                    xlit    = self.transliterate(sw.text, sw.lang, sw.is_proper, provided=sw.stem.translit)
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
                english = self.transform_english(token.english, token.par_class, token.is_red)
                parts.append(
                    f"<ilb><t>{english}</t><lg>{''.join(segments)}</lg></ilb>"
                )
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
