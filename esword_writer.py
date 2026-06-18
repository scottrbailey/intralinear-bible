"""
esword_writer.py

e-Sword LT Bible module writer (.bbli).
"""

from sqlite_writer import SQLiteBibleWriter
from textwrap import dedent

CHAPTER_CSS = (
    '<style>'
    '.stk{display:inline-flex;flex-direction:column;align-items:center;'
    'vertical-align:super;font-size:0.65em;line-height:1.2;}'
    '.xlitH{color:blue;}'
    '.xlitG{color:green;}'
    '.snum{color:gray !important;}'
    '</style>'
)


class ESwordWriter(SQLiteBibleWriter):
    """Writes e-Sword LT .bbli SQLite Bible modules."""

    file_extension = '.bbli'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._note_counter   = 0
        self._current_chapter = None

    def open(self, output_path, work_id: str = "BSBi"):
        super().open(output_path, work_id)
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

    def add_verse(self, osis_ref: str, intralinear_tokens: list,
                  header: str = None, xrefs: dict = None):
        parts    = osis_ref.split('.')
        chapter  = int(parts[1])
        verse    = int(parts[2])
        book_num = self._book_num(parts[0])

        if chapter != self._current_chapter:
            self._note_counter    = 0
            self._current_chapter = chapter

        # Collect translator notes, assigning sequential N# IDs per chapter
        verse_notes = []
        if self.notes:
            for token in intralinear_tokens:
                for note in token.notes:
                    self._note_counter += 1
                    verse_notes.append({
                        'seq':        self._note_counter,
                        'text':       note['text'],
                        'token_note': note,
                    })

        note_id_map = {
            vn['token_note']['noteId']: vn['seq'] for vn in verse_notes
        }

        # Collect xrefs for this verse (keys are already per-chapter sequential)
        verse_xrefs = []
        if self.xref and xrefs:
            for key, text in xrefs.items():
                verse_xrefs.append({'key': key, 'text': text})

        super().add_verse(osis_ref, intralinear_tokens, header=header,
                          note_id_map=note_id_map,
                          xrefs=verse_xrefs if self.xref else None,
                          xref_placement=self.xref,
                          prepend=CHAPTER_CSS if verse == 1 else None)

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

    def _xref_markers(self, xrefs: list) -> str:
        return ''.join(f' <not>R{vx["key"]}</not>' for vx in xrefs)

    def render_verse_intralinear(self, tokens: list, header: str = None,
                                 note_id_map: dict = None,
                                 xrefs: list = None,
                                 xref_placement: int = 0) -> str:
        """Render tokens to intralinear HTML with <sup class="str"> Strong's links.

        Notes rendered as <not>N#</not>, xrefs as <not>R#</not>.
        xref_placement: 1 = beginning of verse, 2 = end of verse.
        Section headers as <h3 class="headline">text</h3>.
        """
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []

        if header:
            parts.append(f'<b class="headline">{header}</b><br>')

        if xref_placement == 1:
            parts.append(self._xref_markers(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')
            else:
                parts.append(token.english)
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit   = self.transliterate(sw.text, sw.lang)
                    cls    = 'xlitH' if sw.lang != 'G' else 'xlitG'
                    lemmas.append(
                        f'<span class="stk {cls}" num="{sw.stem.strongs}">'
                        f'{xlit}'
                        f'<num class="snum">{sw.stem.strongs}</num>'
                        f'</span>'
                    )
                parts.append(' '.join(lemmas))

                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self._xref_markers(xrefs))

        return ''.join(parts)

    def render_verse_interlinear(self, tokens: list, header: str = None,
                                 note_id_map: dict = None,
                                 xrefs: list = None,
                                 xref_placement: int = 0) -> str:
        """Render tokens to interlinear HTML.

        Format per aligned token:
          <q><heb>בְּרֵאשִׁית</heb><xlit>bereshit</xlit><num>H7225</num><tvm>in the beginning</tvm></q>
        """
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []

        if header:
            parts.append(f'<b class="headline">{header}</b> ')

        if xref_placement == 1:
            parts.append(self._xref_markers(xrefs))

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')
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
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        if xref_placement == 2:
            parts.append(self._xref_markers(xrefs))

        return ''.join(parts)
