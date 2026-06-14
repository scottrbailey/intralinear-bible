"""
OSIS Renderer for Intralinear Bible
Converts joined intralinear tokens to OSIS XML output.
"""

import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element, SubElement
from pathlib import Path

from translit import make_transliterator


# ================== OSIS NAMESPACE ==================

OSIS_NS = "http://www.bibletechnologies.net/2003/OSIS/namespace"


def ns(tag: str) -> str:
    return f"{{{OSIS_NS}}}{tag}"


# ================== HEADER ==================

def make_osis_header(work_id: str = "BSB_intralinear") -> tuple:
    """Build a minimal OSIS root element with header."""
    ET.register_namespace('', OSIS_NS)

    osis = Element(ns("osis"), {
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
        "xsi:schemaLocation": (
            "http://www.bibletechnologies.net/2003/OSIS/namespace "
            "http://www.crosswire.org/~dmsmith/osis/osisCore.2.1.1-cw-latest.xsd"
        ),
    })

    osis_text = SubElement(osis, ns("osisText"), {
        "osisIDWork": work_id,
        "osisRefWork": "Bible",
        "xml:lang": "en",
        "canonical": "true",
    })

    header = SubElement(osis_text, ns("header"))

    work = SubElement(header, ns("work"), {"osisWork": work_id})
    SubElement(work, ns("title")).text = "BSB Intralinear Bible"
    SubElement(work, ns("type"), {"type": "x-bible"})
    SubElement(work, ns("identifier"), {"type": "OSIS"}).text = work_id
    SubElement(work, ns("refSystem")).text = "Bible"

    strong_work = SubElement(header, ns("work"), {"osisWork": "strong"})
    SubElement(strong_work, ns("refSystem")).text = "Dict.Strongs"

    bsblex_work = SubElement(header, ns("work"), {"osisWork": "lemma.BSBlex"})
    SubElement(bsblex_work, ns("refSystem")).text = "Dict.BSBlex"

    return osis, osis_text


# ================== VERSE RENDERING ==================

def render_verse(parent: Element, osis_ref: str, intralinear_tokens: list,
                 transliterate: callable):
    """Append a rendered verse to parent element.

    Each IntralinearToken may have multiple SourceWords. Each SourceWord
    becomes a separate <w> element with its own lemma and xlit attributes.
    """
    verse = SubElement(parent, ns("verse"), {"osisID": osis_ref})

    for i, token in enumerate(intralinear_tokens):
        next_token = intralinear_tokens[i + 1] if i + 1 < len(intralinear_tokens) else None

        if token.is_plain_text or not token.source_words:
            _append_text(verse, token.english)
            for note in token.notes:
                _append_note(verse, note)
        else:
            # Emit English text before the <w> elements
            _append_text(verse, token.english)

            for sw in token.source_words:
                xlit = "Latn:" + transliterate(sw.text, sw.lang, sw.is_proper)
                lemma = f"lemma.BSBlex:{sw.text} strong:{sw.stem.strongs}"

                w = SubElement(verse, ns("w"), {
                    "lemma": lemma,
                    "xlit":  xlit,
                })

            # Notes go after the final <w>
            for note in token.notes:
                _append_note(verse, note)

        # Space handling
        if not token.skip_space_after and next_token is not None:
            _append_text(verse, " ")

    return verse


def _append_note(parent: Element, note: dict):
    """Append a <note> element to parent."""
    note_el = SubElement(parent, ns("note"), {
        "type":      "footnote",
        "placement": "foot",
        "n":         str(note['noteId']),
    })
    note_el.text = note['text']


def _append_text(element: Element, text: str):
    """Append text to the tail of the last child, or to element.text if no children."""
    children = list(element)
    if children:
        last = children[-1]
        last.tail = (last.tail or "") + text
    else:
        element.text = (element.text or "") + text


# ================== BOOK / CHAPTER STRUCTURE ==================

def get_or_create_book(osis_text: Element, book_map: dict, osis_book: str) -> dict:
    """Get or create a <div type='book'> element for the given book."""
    if osis_book not in book_map:
        div = SubElement(osis_text, ns("div"), {
            "type": "book",
            "osisID": osis_book,
            "canonical": "true",
        })
        book_map[osis_book] = {"div": div, "chapters": {}}
    return book_map[osis_book]


def get_or_create_chapter(book_entry: dict, osis_chapter: str) -> Element:
    """Get or create a <chapter> element."""
    if osis_chapter not in book_entry["chapters"]:
        chapter = SubElement(book_entry["div"], ns("chapter"), {
            "osisID": osis_chapter,
        })
        book_entry["chapters"][osis_chapter] = chapter
    return book_entry["chapters"][osis_chapter]


def osis_ref_to_parts(osis_ref: str) -> tuple:
    """Split 'Gen.1.1' into ('Gen', 'Gen.1', 'Gen.1.1')"""
    parts   = osis_ref.split(".")
    book    = parts[0]
    chapter = f"{parts[0]}.{parts[1]}"
    return book, chapter, osis_ref


# ================== WRITER ==================

class OSISWriter:
    """Manages incremental OSIS document construction and final output."""

    def __init__(self, work_id: str = "BSB_intralinear", transliterate: callable = None):
        self.osis, self.osis_text = make_osis_header(work_id)
        self.book_map = {}
        self.transliterate = transliterate or make_transliterator()

    def add_verse(self, osis_ref: str, intralinear_tokens: list,
                  header: str = None):
        """Add a verse to the document, optionally preceded by a section title."""
        book_id, chapter_id, verse_id = osis_ref_to_parts(osis_ref)
        book_entry = get_or_create_book(self.osis_text, self.book_map, book_id)
        chapter_el = get_or_create_chapter(book_entry, chapter_id)

        # Emit section title before the verse if present
        if header:
            title_el = SubElement(chapter_el, ns("title"), {
                "type": "section",
            })
            title_el.text = header

        render_verse(chapter_el, verse_id, intralinear_tokens, self.transliterate)

    def write(self, output_path: Path):
        """Write the OSIS document to file."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(self.osis, space="  ")
        tree = ET.ElementTree(self.osis)
        tree.write(
            output_path,
            encoding="utf-8",
            xml_declaration=True,
        )
        print(f"Written to {output_path}")