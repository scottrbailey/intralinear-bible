"""
mysword_writer.py

MySwordWriter: writes MySword Bible modules (.bbl.mybible).
CSS and VerseRules come from the injected VerseFormatter.
"""

from datetime import date

from sqlite_writer import SQLiteBibleWriter


class MySwordWriter(SQLiteBibleWriter):
    """Writes MySword .bbl.mybible SQLite Bible modules."""

    _table_name  = 'Bible'
    _format_name = 'mysword'

    def __init__(self, profile, **kwargs):
        super().__init__(profile, **kwargs)
        self._note_counter    = 0
        self._current_chapter = None

    def add_verse(self, osis_ref: str, tokens: list,
                  header: str = None, xrefs: dict = None) -> None:
        chapter = int(osis_ref.split('.')[1])
        if chapter != self._current_chapter:
            self._note_counter    = 0
            self._current_chapter = chapter

        # Raw noteIds (e.g. TableComposer's "F{bsb_sort}") are large and
        # meaningless as a display id — remap to small numbers, reset each
        # chapter, same convention as ESwordWriter's Notes table ids.
        note_id_map = {}
        if self.notes:
            for token in tokens:
                for note in token.notes:
                    self._note_counter += 1
                    note_id_map[note['noteId']] = self._note_counter

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

    def insert_details(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS Details (
                Description  NVARCHAR(255),
                Abbreviation NVARCHAR(50),
                Comments     TEXT,
                Version      TEXT,
                VersionDate  DATETIME,
                PublishDate  DATETIME,
                RightToLeft  BOOL,
                OT           BOOL,
                NT           BOOL,
                Strong       BOOL,
                CustomCSS    TEXT,
                VerseRules   TEXT
            )
        """)
        today = date.today().isoformat()
        self.conn.execute("""
            INSERT INTO Details (
                Description, Abbreviation, Comments, Version,
                VersionDate, PublishDate, RightToLeft, OT, NT, Strong,
                CustomCSS, VerseRules
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            self.profile.module_name,
            self.profile.abbreviation,
            self.profile.description,
            4,           # needs 4 to indicate HTML... I know
            today,
            self.profile.publish_date,
            0,
            1 if self._has_ot else 0,
            1 if self._has_nt else 0,
            1,
            self.profile.css,
            self.profile.verse_rules,
        ))
