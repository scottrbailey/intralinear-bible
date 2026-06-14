"""
sqlite_writer.py

Base class for SQLite-based Bible module writers (MySword, e-Sword LT).
Handles Bible table insertion and shared rendering in two modes:
  - 'interlinear': GBF tags (<Q><H>...<WH776><h><X>...<x><E>...<e><q>)
  - 'intralinear': English text with <lemma> tags for VerseRules transformation
"""

import sqlite3
from pathlib import Path
from translit import make_transliterator


# ================== GBF INTERLINEAR RENDERING ==================

def render_verse_gbf(intralinear_tokens: list, transliterate: callable,
                     header: str = None) -> str:
    """Render IntralinearTokens to GBF-tagged interlinear string.

    Format per aligned token (one <H>...<h> segment per source display-word):
      <Q><H>בְּרֵאשִׁית<WH7225><X>bereshit<x><h><E>in the beginning<e><q>

    Multiple source words in one alignment group get multiple segments:
      <Q><H>word1<WH1234><X>xlit1<x><h><H>word2<WH5678><X>xlit2<x><h><E>english<e><q>

    VerseRules transforms each <H>...<h> segment into a superscript link,
    then strips the outer <Q>...<q> wrapper leaving English + superscripts.
    """
    parts = []

    if header:
        parts.append(f"<TS>{header}<Ts>")

    for i, token in enumerate(intralinear_tokens):
        next_token = intralinear_tokens[i + 1] if i + 1 < len(intralinear_tokens) else None

        if token.is_plain_text or not token.source_words:
            parts.append(token.english)
            for note in token.notes:
                parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")
        else:
            segments = []
            for sw in token.source_words:
                xlit = transliterate(sw.text, sw.lang, sw.is_proper)
                strongs = sw.stem.strongs
                if sw.lang == 'G':
                    seg = f"<G>{sw.text}<W{strongs}><X>{xlit}<x><g>"
                else:
                    seg = f"<H>{sw.text}<W{strongs}><X>{xlit}<x><h>"
                segments.append(seg)

            parts.append(
                f"<Q>"
                f"{''.join(segments)}"
                f"<E>{token.english}<e>"
                f"<q>"
            )

            for note in token.notes:
                parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")

        if not token.skip_space_after and next_token is not None:
            parts.append(' ')

    return ''.join(parts)


# ================== INTRALINEAR RENDERING ==================

def render_verse_intralinear(intralinear_tokens: list, transliterate: callable,
                              header: str = None) -> str:
    """Render IntralinearTokens to intralinear string with <lemma> tags.

    Format per aligned token (one <lemma> per source display-word):
      in the beginning <lemma sn="H7225" o="בְּרֵאשִׁית">bereshit</lemma>

    Multiple source words in one alignment group emit multiple <lemma> tags:
      english phrase <lemma sn="H1234" o="word1">xlit1</lemma><lemma sn="H5678" o="word2">xlit2</lemma>

    VerseRules transform <lemma> to superscript colored dictionary links.
    The 'o' attribute carries the original script for optional CSS display.
    Notes rendered as <RF q=N>text<Rf>.
    Section headers as <TS>text<Ts>.
    """
    parts = []

    if header:
        parts.append(f"<TS>{header}<Ts>")

    for i, token in enumerate(intralinear_tokens):
        next_token = intralinear_tokens[i + 1] if i + 1 < len(intralinear_tokens) else None

        if token.is_plain_text or not token.source_words:
            parts.append(token.english)
            for note in token.notes:
                parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")
        else:
            parts.append(token.english)
            parts.append(' ')
            lemmas = []
            for sw in token.source_words:
                xlit = transliterate(sw.text, sw.lang, sw.is_proper)
                lemmas.append(
                    f'<lemma sn="{sw.stem.strongs}" o="{sw.text}">'
                    f'{xlit}'
                    f'</lemma>'
                )
            parts.append(' '.join(lemmas))

            for note in token.notes:
                parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")

        if not token.skip_space_after and next_token is not None:
            parts.append(' ')

    return ''.join(parts)


# ================== BASE SQLITE WRITER ==================

class SQLiteBibleWriter:
    """Base class for SQLite Bible module writers.

    Subclasses must implement:
      - file_extension: str
      - insert_details(conn, work_id, has_ot, has_nt, render_mode)

    render_mode: 'interlinear' (GBF) or 'intralinear' (<lemma> tags)
    """

    file_extension = '.sqlite'

    def __init__(self, transliterate: callable = None,
                 render_mode: str = 'intralinear'):

        self.transliterate = transliterate or make_transliterator()
        self.render_mode   = render_mode
        self.conn          = None
        self.output_path   = None
        self._has_ot       = False
        self._has_nt       = False
        self._verse_count  = 0

    def open(self, output_path: Path, work_id: str = "BSBIntralinear"):
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
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS Bible (
                Book      INT,
                Chapter   INT,
                Verse     INT,
                Scripture TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS bible_key ON Bible (Book, Chapter, Verse)"
        )
        self.conn.commit()

    def add_verse(self, osis_ref: str, intralinear_tokens: list,
                  header: str = None):
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

        if self.render_mode == 'interlinear':
            scripture = render_verse_gbf(
                intralinear_tokens, self.transliterate, header=header
            )
        else:
            scripture = render_verse_intralinear(
                intralinear_tokens, self.transliterate, header=header
            )

        self.conn.execute(
            "INSERT INTO Bible (Book, Chapter, Verse, Scripture) VALUES (?, ?, ?, ?)",
            (book_num, chapter, verse, scripture)
        )
        self._verse_count += 1

        if self._verse_count % 1000 == 0:
            self.conn.commit()

    def write(self, output_path: Path = None):
        """Finalize — insert Details table and close connection."""
        self.conn.commit()
        self.insert_details(self.conn, self.work_id,
                            self._has_ot, self._has_nt, self.render_mode)
        self.conn.commit()
        self.conn.close()
        print(f"Written to {self.output_path} ({self._verse_count:,} verses)")

    def insert_details(self, conn, work_id: str, has_ot: bool, has_nt: bool,
                       render_mode: str):
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