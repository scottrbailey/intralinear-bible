"""
esword_writer.py

e-Sword LT Bible module writer (.bblx).
Uses GBF interlinear tags via SQLiteBibleWriter base class.
"""

import sqlite3
from pathlib import Path
from sqlite_writer import SQLiteBibleWriter


class ESwordWriter(SQLiteBibleWriter):
    """Writes e-Sword LT .bblx SQLite Bible modules."""

    file_extension = '.bblx'

    def insert_details(self, conn, work_id: str, has_ot: bool, has_nt: bool):
        conn.execute("""
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
        conn.execute("""
            INSERT INTO Details (
                Title, Abbreviation, Information, Version,
                OldTestament, NewTestament, Apocrypha, Strongs, RightToLeft
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "BSB Intralinear Bible",
            "BSBi",
            "Berean Standard Bible with inline Hebrew and Greek transliteration. "
            "Source language data from WLC (OT) and SBLGNT (NT) via Clear Bible "
            "Alignments project (CC BY 4.0).",
            1,
            1 if has_ot else 0,
            1 if has_nt else 0,
            0,           # Apocrypha
            1,           # Strongs
            0,           # RightToLeft
        ))