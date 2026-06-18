"""
esword_writer.py

e-Sword LT Bible module writer (.bblx).
Uses GBF interlinear tags via SQLiteBibleWriter base class.
"""

from sqlite_writer import SQLiteBibleWriter
from textwrap import dedent


class ESwordWriter(SQLiteBibleWriter):
    """Writes e-Sword LT .bbli SQLite Bible modules."""

    file_extension = '.bbli'

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
            "BSB Intralinear Bible",
            self.work_id,
            dedent("""\
            Berean Standard Bible with inline Hebrew and Greek transliteration.
            Source language data from WLC (OT) and SBLGNT (NT) via Clear Bible
            Alignments project (CC BY 4.0)."""),
            4,          # Version
            1 if self._has_ot else 0,
            1 if self._has_nt else 0,
            0,           # Apocrypha
            1,           # Strongs
            0,           # RightToLeft
        ))

    def render_verse_intralinear(self, tokens: list,
                                 header: str = None) -> str:
        """Render tokens to intralinear string with <lemma> tags.

        Format per aligned token (one <sup> per source display-word):
          in the beginning <sup class="str" num="H7225">bereshit</sup>

        Notes rendered as <not>N#</not> with note text inserted into notes table
        Section headers as <b class="headline">text</b>.
        """
        parts = []

        if header:
            parts.append(f"<b class=\"headline\">{header}</b> ")

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                for note in token.notes:
                    parts.append(f"<not>N{note['noteId']}</not>")
                    # need to insert note['text'] into notes table
            else:
                parts.append(token.english)
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang)
                    lemmas.append(
                        f'<sup class="str" num="{sw.stem.strongs}">{xlit}</sup>'
                    )
                parts.append(' '.join(lemmas))

                for note in token.notes:
                    parts.append(f"<not>N{note['noteId']}</not>")
                    # need to insert note['text'] into notes table

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        return ''.join(parts)

    def render_verse_interlinear(self, tokens: list, header: str = None):
        """Render tokens to interlinear string.

        Format per aligned token (one <H>...<h> segment per source display-word):
          <q><heb>בְּרֵאשִׁית</heb><num>H7225</num><xl>bereshit</xl><tvm>in the beginning</tvm></q>

        Multiple source words in one alignment group get multiple segments:
          <q><heb>word1</heb><xlit>xlit1</xlit> <heb>word2</heb><num>H4321</num><xlit>xlit2</xlit><tvm>english</tvm></q>
      """
        parts = []

        if header:
            parts.append(f"<b class=\"headline\">{header}</b> ")

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                for note in token.notes:
                    parts.append(f"<rf q=\"{note['noteId']}\">{note['text']}</rf>")
            else:
                segments = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang)
                    strongs = sw.stem.strongs
                    if sw.lang == 'G':
                        seg = f"<grk>{sw.text}</grk><xlit>{xlit}</xlit><num>{strongs}</num>"
                    else:
                        seg = f"<heb>{sw.text}</heb><xlit>{xlit}</xlit><num>{strongs}</num>"
                    segments.append(seg)

                parts.append(
                    f"<q>"
                    f"{' '.join(segments)}"
                    f"<tvm>{token.english}</tvm>"
                    f"</q>"
                )

                for note in token.notes:
                    parts.append(f"<rf q=\"{note['noteId']}\">{note['text']}</rf>")

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        return ''.join(parts)