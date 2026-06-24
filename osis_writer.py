"""
osis_writer.py

OSISWriter: builds an OSIS XML document from AlignedToken verse streams.
"""

import xml.etree.ElementTree as ET
from xml.etree.ElementTree import Element, SubElement
from pathlib import Path

from bible_writer import BibleWriter
from translit import make_transliterator

OSIS_NS  = "http://www.bibletechnologies.net/2003/OSIS/namespace"
WORK_ID  = "BSB_intralinear"


def _ns(tag: str) -> str:
    return f"{{{OSIS_NS}}}{tag}"


class OSISWriter(BibleWriter):
    """Builds an incremental OSIS XML document and writes it to file."""

    abbreviation   = "BSBi"
    module_name    = "BSB Intralinear Bible"
    file_extension = ".osis.xml"

    def __init__(self, work_id: str = WORK_ID, transliterate: callable = None):
        self.work_id       = work_id
        self.transliterate = transliterate or make_transliterator()
        self._osis         = None
        self._osis_text    = None
        self._book_map     = {}
        self._output_path  = None

    def open(self, output_dir: Path) -> None:
        self._output_path = output_dir / (self.abbreviation + self.file_extension)
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        self._osis, self._osis_text = self._make_header()

    def add_verse(self, osis_ref: str, tokens: list,
                  header: str = None, xrefs: dict = None) -> None:
        book_id, chapter_id, verse_id = _split_ref(osis_ref)
        book_entry = self._get_or_create_book(book_id)
        chapter_el = self._get_or_create_chapter(book_entry, chapter_id)

        if header:
            title_el = SubElement(chapter_el, _ns("title"), {"type": "section"})
            title_el.text = header

        self._render_verse(chapter_el, verse_id, tokens)

    def write(self) -> None:
        ET.register_namespace('', OSIS_NS)
        ET.indent(self._osis, space="  ")
        tree = ET.ElementTree(self._osis)
        tree.write(self._output_path, encoding="utf-8", xml_declaration=True)
        print(f"Written to {self._output_path}")

    # --------------------------------------------------------------- internals

    def _make_header(self) -> tuple:
        ET.register_namespace('', OSIS_NS)
        osis = Element(_ns("osis"), {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:schemaLocation": (
                "http://www.bibletechnologies.net/2003/OSIS/namespace "
                "http://www.crosswire.org/~dmsmith/osis/osisCore.2.1.1-cw-latest.xsd"
            ),
        })
        osis_text = SubElement(osis, _ns("osisText"), {
            "osisIDWork": self.work_id,
            "osisRefWork": "Bible",
            "xml:lang": "en",
            "canonical": "true",
        })
        header = SubElement(osis_text, _ns("header"))

        work = SubElement(header, _ns("work"), {"osisWork": self.work_id})
        SubElement(work, _ns("title")).text = self.module_name
        SubElement(work, _ns("type"), {"type": "x-bible"})
        SubElement(work, _ns("identifier"), {"type": "OSIS"}).text = self.work_id
        SubElement(work, _ns("refSystem")).text = "Bible"

        strong_work = SubElement(header, _ns("work"), {"osisWork": "strong"})
        SubElement(strong_work, _ns("refSystem")).text = "Dict.Strongs"

        bsblex_work = SubElement(header, _ns("work"), {"osisWork": "lemma.BSBlex"})
        SubElement(bsblex_work, _ns("refSystem")).text = "Dict.BSBlex"

        return osis, osis_text

    def _render_verse(self, parent: Element, verse_id: str, tokens: list):
        verse = SubElement(parent, _ns("verse"), {"osisID": verse_id})
        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                _append_text(verse, token.english)
                for note in token.notes:
                    _append_note(verse, note)
            else:
                _append_text(verse, token.english)
                for sw in token.source_words:
                    xlit  = "Latn:" + self.transliterate(sw.text, sw.lang, sw.is_proper)
                    lemma = f"lemma.BSBlex:{sw.text} strong:{sw.stem.strongs}"
                    SubElement(verse, _ns("w"), {"lemma": lemma, "xlit": xlit})
                for note in token.notes:
                    _append_note(verse, note)

            if not token.skip_space_after and next_token is not None:
                _append_text(verse, " ")

    def _get_or_create_book(self, osis_book: str) -> dict:
        if osis_book not in self._book_map:
            div = SubElement(self._osis_text, _ns("div"), {
                "type": "book",
                "osisID": osis_book,
                "canonical": "true",
            })
            self._book_map[osis_book] = {"div": div, "chapters": {}}
        return self._book_map[osis_book]

    def _get_or_create_chapter(self, book_entry: dict, osis_chapter: str) -> Element:
        if osis_chapter not in book_entry["chapters"]:
            chapter = SubElement(book_entry["div"], _ns("chapter"), {"osisID": osis_chapter})
            book_entry["chapters"][osis_chapter] = chapter
        return book_entry["chapters"][osis_chapter]


# ------------------------------------------------------------------- helpers

def _split_ref(osis_ref: str) -> tuple:
    """Split 'Gen.1.1' into ('Gen', 'Gen.1', 'Gen.1.1')."""
    parts = osis_ref.split(".")
    return parts[0], f"{parts[0]}.{parts[1]}", osis_ref


def _append_text(element: Element, text: str):
    children = list(element)
    if children:
        last      = children[-1]
        last.tail = (last.tail or "") + text
    else:
        element.text = (element.text or "") + text


def _append_note(parent: Element, note: dict):
    note_el = SubElement(parent, _ns("note"), {
        "type": "footnote", "placement": "foot", "n": str(note['noteId']),
    })
    note_el.text = note['text']
