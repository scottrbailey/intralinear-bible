"""
sqlite_writer.py

SQLiteBibleWriter: shared base for e-Sword and MySword writers.
Handles Bible table creation, verse insertion, preview, and finalization.
Verse rendering is fully delegated to the injected VerseFormatter.
"""

import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from bible_writer import BibleWriter
from verse_formatter import VerseFormatter


class SQLiteBibleWriter(BibleWriter):
    """Base class for SQLite Bible module writers.

    Subclasses must implement:
      - _table_name: str
      - insert_details()
      - Any format-specific table setup in open() / _create_bible_table()
    """

    _table_name: str = 'Bible'

    def __init__(self, profile: VerseFormatter,
                 headers: bool = True,
                 notes: bool = True,
                 xref: bool = False,
                 version: str = '1.0.0'):
        self.profile  = profile
        self.headers  = headers
        self.notes    = notes
        self.xref     = xref
        self.version  = version

        self.conn        = None
        self.output_path = None
        self.work_id     = profile.abbreviation

        self._has_ot       = False
        self._has_nt       = False
        self._verse_count  = 0
        self._previewed_ot = False
        self._previewed_nt = False

    # ------------------------------------------------------------------ public

    def open(self, output_dir: Path) -> None:
        """Create the SQLite database at output_dir / (abbreviation + extension)."""
        self.output_path = output_dir / (self.profile.abbreviation + self.profile.file_extension)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.output_path.exists():
            self.output_path.unlink()
        self.conn = sqlite3.connect(self.output_path)
        self._create_bible_table()

    def add_verse(self, osis_ref: str, tokens: list,
                  header: str = None, xrefs: dict = None) -> None:
        """Render and insert one verse via the profile."""
        self._add_verse_impl(osis_ref, tokens, header=header, xrefs=xrefs)

    def write(self) -> None:
        """Finalize: commit, insert Details, close."""
        self.conn.commit()
        self.insert_details()
        self.conn.commit()
        self.conn.close()
        print(f"Written to {self.output_path} ({self._verse_count:,} verses)")

    def insert_details(self):
        raise NotImplementedError

    # --------------------------------------------------------------- internals

    def _create_bible_table(self):
        t = self._table_name
        self.conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {t} (
                Book      INT,
                Chapter   INT,
                Verse     INT,
                Scripture TEXT
            )
        """)
        self.conn.execute(
            f"CREATE INDEX IF NOT EXISTS bible_key ON {t} (Book, Chapter, Verse)"
        )
        self.conn.commit()

    def _add_verse_impl(self, osis_ref: str, tokens: list,
                        header: str = None, xrefs=None,
                        note_id_map: dict = None, xref_placement: int = 0):
        """Core insert: render via profile, preview on first OT/NT verse, insert row."""
        parts    = osis_ref.split('.')
        book_num = self._book_num(parts[0])
        chapter  = int(parts[1])
        verse    = int(parts[2])

        if book_num <= 39:
            self._has_ot = True
        else:
            self._has_nt = True

        # Filter here so the formatter only sees what it should emit.
        # If a feature is off, the formatter never writes the corresponding tags
        # and its CSS rules for those tags are never exercised.
        render_header = header if self.headers else None
        render_tokens = tokens if self.notes else [replace(t, notes=[]) for t in tokens]

        scripture = self.profile.render_verse(
            render_tokens,
            header=render_header,
            note_id_map=note_id_map or {},
            xrefs=xrefs or [],
            xref_placement=xref_placement,
        )

        is_ot = book_num <= 39
        if (is_ot and not self._previewed_ot) or (not is_ot and not self._previewed_nt):
            self._pretty_print(osis_ref, scripture)
            transformed = self.profile.preview_transform(scripture)
            if transformed != scripture:
                print(f"--- {osis_ref} transformed ---")
                print(transformed)
            if is_ot:
                self._previewed_ot = True
            else:
                self._previewed_nt = True

        self.conn.execute(
            f"INSERT INTO {self._table_name} (Book, Chapter, Verse, Scripture) VALUES (?,?,?,?)",
            (book_num, chapter, verse, scripture),
        )
        self._verse_count += 1
        if self._verse_count % 1000 == 0:
            self.conn.commit()

    @staticmethod
    def _book_num(osis_book: str) -> int:
        from biblelib.book import Books
        if not hasattr(SQLiteBibleWriter, '_book_cache'):
            SQLiteBibleWriter._book_cache = {
                book.osisID: int(book.usfmnumber)
                for book in Books().values()
                if book.usfmnumber.isdigit()
            }
        return SQLiteBibleWriter._book_cache.get(osis_book, 0)

    @staticmethod
    def _pretty_print(osis_ref: str, scripture: str):
        print(f'\n--- {osis_ref} ---')
        try:
            root = ET.fromstring(f'<v>{scripture}</v>')
            if root.text:
                print(root.text, end='')
            for child in root:
                print('\n' + ET.tostring(child, encoding='unicode'), end='')
            print()
        except ET.ParseError as e:
            print(f'(parse error: {e})')
            print(scripture)
        print()
