"""
esword_writer.py

ESwordWriter: writes e-Sword LT Bible modules (.bbli).

Responsibilities beyond SQLiteBibleWriter:
  - Mods table + Bible view (CSS injection)
  - Notes table (translator notes and cross-references stored separately)
  - Sequential per-chapter note numbering
"""

from textwrap import dedent
from pathlib import Path

from sqlite_writer import SQLiteBibleWriter
from module_profile import ModuleProfile


class ESwordWriter(SQLiteBibleWriter):
    """Writes e-Sword LT .bbli SQLite Bible modules."""

    _table_name = '_Bible'

    def __init__(self, profile: ModuleProfile, **kwargs):
        super().__init__(profile, **kwargs)
        self._note_counter    = 0
        self._current_chapter = None

    def open(self, output_dir: Path) -> None:
        super().open(output_dir)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS Notes (
                Book    INT,
                Chapter INT,
                Verse   INT,
                ID      NVARCHAR,
                Note    TEXT
            )
        """)
        self.conn.commit()

    def add_verse(self, osis_ref: str, tokens: list,
                  header: str = None, xrefs: dict = None) -> None:
        parts    = osis_ref.split('.')
        chapter  = int(parts[1])
        verse    = int(parts[2])
        book_num = self._book_num(parts[0])

        if chapter != self._current_chapter:
            self._note_counter    = 0
            self._current_chapter = chapter

        verse_notes = []
        note_id_map = {}
        if self.notes:
            for token in tokens:
                for note in token.notes:
                    self._note_counter += 1
                    verse_notes.append({
                        'seq':  self._note_counter,
                        'text': note['text'],
                        'note': note,
                    })
            note_id_map = {vn['note']['noteId']: vn['seq'] for vn in verse_notes}

        verse_xrefs = []
        if self.xref and xrefs:
            verse_xrefs = [{'key': k, 'text': v} for k, v in xrefs.items()]

        self._add_verse_impl(
            osis_ref, tokens,
            header=header,
            note_id_map=note_id_map,
            xrefs=verse_xrefs,
            xref_placement=self.xref,
        )

        for vn in verse_notes:
            self.conn.execute(
                "INSERT INTO Notes (Book, Chapter, Verse, ID, Note) VALUES (?,?,?,?,?)",
                (book_num, chapter, verse, f"N{vn['seq']}", vn['text']),
            )
        for vx in verse_xrefs:
            note_text = ' ; '.join(
                f"<ref>{r.strip()}</ref>" for r in vx['text'].split(';') if r.strip()
            )
            self.conn.execute(
                "INSERT INTO Notes (Book, Chapter, Verse, ID, Note) VALUES (?,?,?,?,?)",
                (book_num, chapter, verse, f"R{vx['key']}", note_text),
            )

    def _create_bible_table(self):
        super()._create_bible_table()
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS Mods (
                mode_id      INT PRIMARY KEY,
                title        VARCHAR,
                css          TEXT,
                replacements TEXT,
                active       BOOL DEFAULT 0
            )
        """)
        self.conn.execute(
            "INSERT INTO Mods (mode_id, title, css, active) VALUES (?, ?, ?, ?)",
            (1, self.profile.module_name, self.profile.css, 1),
        )
        self.conn.execute("""
            CREATE VIEW Bible AS
            SELECT Book, Chapter, Verse,
                '<style>' || (SELECT css FROM Mods WHERE active=1) || '</style>' || Scripture AS Scripture
            FROM _Bible
        """)
        self.conn.commit()

    def insert_details(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS Details (
                Title        NVARCHAR,
                Abbreviation NVARCHAR,
                Information  TEXT,
                Version      INT,
                OldTestament BOOL,
                NewTestament BOOL,
                Apocrypha    BOOL,
                Strongs      BOOL,
                RightToLeft  BOOL
            )
        """)
        self.conn.execute("""
            INSERT INTO Details (
                Title, Abbreviation, Information, Version,
                OldTestament, NewTestament, Apocrypha, Strongs, RightToLeft
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.profile.module_name,
            self.profile.abbreviation,
            self.profile.description,
            self.version,
            1 if self._has_ot else 0,
            1 if self._has_nt else 0,
            0,
            1,
            0,
        ))
