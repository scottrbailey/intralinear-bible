"""
mysword_writer.py

MySwordWriter: writes MySword Bible modules (.bbl.mybible).
CSS and VerseRules come from the injected VerseFormatter.
"""

from datetime import date

from sqlite_writer import SQLiteBibleWriter


class MySwordWriter(SQLiteBibleWriter):
    """Writes MySword .bbl.mybible SQLite Bible modules."""

    _table_name = 'Bible'

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
