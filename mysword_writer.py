"""
mysword_writer.py

MySwordWriter: writes MySword Bible modules (.bbl.mybible).
CSS and VerseRules come from the injected ModuleProfile.
"""

from datetime import date
from pathlib import Path

from sqlite_writer import SQLiteBibleWriter
from module_profile import ModuleProfile


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
            self.version,
            today, today,
            0,
            1 if self._has_ot else 0,
            1 if self._has_nt else 0,
            1,
            self.profile.css,
            self.profile.verse_rules,
        ))
