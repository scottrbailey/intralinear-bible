"""
verse_formatter/base.py

Shared plumbing for every VerseFormatter: the ABC itself, cross-reference
parsing, header parsing, English-text helpers, and the two platform xref/
red-letter mixins. Rarely touched day to day -- CSS and render_verse()
tweaks live in intralinear.py / reverse_interlinear.py instead.

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
_BOOKS_DB = Path(__file__).resolve().parent.parent / "data" / "books.db"
with sqlite3.connect(_BOOKS_DB) as _conn:
    ABBREV_TO_BOOK_NUM = {r[0]: r[1] for r in
                           _conn.execute("SELECT display_abbrev, usfm_number FROM books")}


MODULE_DESCRIPTION = dedent("""\
    Berean Standard Bible with inline Hebrew and Greek transliteration.
    Source language data from BSB Translation Tables - https://berean.bible/downloads.htm""")


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
                     narrower category — see verse_formatter/base.py's module-level
                     comment above _IMPLIED_WORD_RE); independent of
                     bracket_replacement, same default and value shapes.
      red_letter_tags — (prefix, suffix) wrapping red-letter (words of
                     Christ) text in transform_english() when is_red is set.
                     Default is a CSS-based <span>; e-Sword/MySword override
                     this with their own native red-letter markup instead
                     (<red>...</red>, <FR>...<Fr>) so the reader's own
                     display-setting toggle controls visibility, rather than
                     baking a fixed color into the module's CSS.
      min_lemma_row_len — reverse-interlinear only: floor (character count)
                     under the English label's own length when sizing an
                     aligned token's *first* <ilb> lemma row (see
                     reverse_interlinear.py's _group_source_words()); every
                     source word past that first row is always its own
                     singleton <ilbc>, never grouped further. A rough proxy
                     for on-screen width, not a real measurement -- tune by
                     eye against e-Sword/MySword's actual rendering,
                     independently per formatter if their real layouts diverge.
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
    min_lemma_row_len:   int = 16

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
        by intralinear.py's _INTRALINEAR_CSS. is_red (also TableComposer-only,
        and only ever True when the writer's red_letter option is on — see
        SQLiteBibleWriter) wraps the result in red_letter_tags, nested
        inside the par_class span when both apply. Intralinear formatters
        wrap red-letter runs themselves instead (see intralinear.py's
        render_verse()), so they never pass is_red here.
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
        in a same-named <span> (styled by intralinear.py's _INTRALINEAR_CSS,
        so each class stays independently stylable and out of the way of
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


# ============================================================ platform mixins

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
                parts.append(f"<RF q=R{vx['key']}>{rx_tags}<Rf> ")
        return ''.join(parts)
