"""
sqlite_writer.py

Base class for SQLite-based Bible module writers (MySword, e-Sword LT).
Handles Bible table insertion and shared rendering in two modes:
  - 'interlinear': GBF tags (<Q><H>...<WH776><h><X>...<x><E>...<e><q>)
  - 'intralinear': English text with <lemma> tags for VerseRules transformation
"""

import sqlite3
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from translit import make_transliterator


class SQLiteBibleWriter:
    """Base class for SQLite Bible module writers.

    Subclasses must implement:
      - file_extension: str
      - insert_details()

    render_mode: 'interlinear' (GBF) or 'intralinear' (<lemma> tags)
    """

    file_extension = '.sqlite'
    _table_name    = 'Bible'

    def __init__(self,
                 transliterate: callable = None,
                 render_mode: str = 'intralinear',
                 headers: bool = True,
                 notes: bool = True,
                 xref: bool = False,
                 version: str = '1.0.0'):

        self.transliterate = transliterate or make_transliterator()
        self.render_mode   = render_mode
        self.version       = version
        self.conn          = None
        self.output_path   = None
        self.headers       = headers
        self.notes         = notes
        self.xref          = xref
        self._has_ot        = False
        self._has_nt        = False
        self._verse_count   = 0
        self._previewed_ot  = False
        self._previewed_nt  = False

    def open(self, output_path: Path, work_id: str = "BSBi"):
        """Open (or create) the SQLite database."""
        path_str = str(output_path)
        if not path_str.endswith(self.file_extension):
            # Strip any existing extension and add ours
            self.output_path = Path(path_str).with_suffix('').parent / (
                Path(path_str).with_suffix('').name + self.file_extension
            )
        else:
            self.output_path = output_path

        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        if self.output_path.exists():
            self.output_path.unlink()

        self.conn    = sqlite3.connect(self.output_path)
        self.work_id = work_id
        self._create_bible_table()

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

    def add_verse(self, osis_ref: str, intralinear_tokens: list,
                  header: str = None, note_id_map: dict = None,
                  xrefs: list = None, xref_placement: int = 0):
        """Render and insert one verse."""
        parts     = osis_ref.split('.')
        book_name = parts[0]
        chapter   = int(parts[1])
        verse     = int(parts[2])
        book_num  = self._book_num(book_name)

        if book_num <= 39:
            self._has_ot = True
        else:
            self._has_nt = True

        kwargs = {
            'tokens':          intralinear_tokens,
            'header':          header,
            'note_id_map':     note_id_map or {},
            'xrefs':           xrefs or [],
            'xref_placement':  xref_placement,
        }

        if self.render_mode == 'interlinear':
            scripture = self.render_verse_interlinear(**kwargs)
        else:
            scripture = self.render_verse_intralinear(**kwargs)

        is_ot = book_num <= 39
        if (is_ot and not self._previewed_ot) or (not is_ot and not self._previewed_nt):
            self._pretty_print(osis_ref, scripture)
            self._preview_transform(osis_ref, scripture)
            if is_ot:
                self._previewed_ot = True
            else:
                self._previewed_nt = True

        self.conn.execute(
            f"INSERT INTO {self._table_name} (Book, Chapter, Verse, Scripture) VALUES (?, ?, ?, ?)",
            (book_num, chapter, verse, scripture)
        )
        self._verse_count += 1

        if self._verse_count % 1000 == 0:
            self.conn.commit()

    def write(self, output_path: Path = None):
        """Finalize — insert Details table and close connection."""
        self.conn.commit()
        self.insert_details()
        self.conn.commit()
        self.conn.close()
        print(f"Written to {self.output_path} ({self._verse_count:,} verses)")

    def insert_details(self):
        raise NotImplementedError

    @staticmethod
    def _book_num(osis_book: str) -> int:
        """Convert biblelib OSIS book name to 1-based integer book number."""
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
            # Print leading text, then each child element on its own line
            if root.text:
                print(root.text, end='')
            for child in root:
                print('\n' + ET.tostring(child, encoding='unicode'), end='')
            print()
        except ET.ParseError as e:
            print(f'(parse error: {e})')
            print(scripture)
        print()

    def _preview_transform(self, osis_ref: str, scripture: str) -> None:
        pass

    def render_verse_intralinear(self, tokens: list, header: str = None):
        raise NotImplementedError

    def render_verse_interlinear(self, tokens: list, header: str = None):
        raise NotImplementedError
