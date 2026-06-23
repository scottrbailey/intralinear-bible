"""
esword_writer.py

e-Sword LT Bible module writer (.bbli).
"""

from sqlite_writer import SQLiteBibleWriter
from textwrap import dedent

INTRALINEAR_CSS = (
    '.stk{display:inline-flex;flex-direction:column;align-items:center;'
    'vertical-align:super;font-size:0.65em;color:blue;line-height:0.9}'
    'span.stk a{opacity:0 !important;}'
)

REVERSE_INTERLINEAR_CSS = (
    # Word block: vertical column, top-aligned, small horizontal margin
    'qi{display:inline-flex;flex-direction:column;align-items:center;'
    'vertical-align:top;margin:0 3px}'
    # English line
    'e{white-space:nowrap}'
    # Wrapper for one or more <lem> blocks: lay them out side by side
    'qi>span{display:flex;flex-direction:row;gap:0px}'
    # Each source-word block: vertical column of script/xlit/strongs/morph
    'lem {display:inline-flex;flex-direction:column;align-items:center;vertical-align:top;'
    'font-size:.9em;margin-top:2px;padding-top:2px;gap:2px;line-height:1 !important;}'
    'lem sup{display:block;vertical-align:baseline;margin:0;padding:0;line-height:1}'
    #'hs{font-size:1.2em}'
    #'gs{font-size:1.1em}'
    '.xlit{color:#2244aa}'
    #'num{color:#7722aa}'
    'tvm{color:#666}'
)


class ESwordWriter(SQLiteBibleWriter):
    """Writes e-Sword LT .bbli SQLite Bible modules."""

    file_extension = '.bbli'
    _table_name    = '_Bible'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._note_counter   = 0
        self._current_chapter = None

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
        if self.render_mode == 'interlinear':
            css   = REVERSE_INTERLINEAR_CSS
            title = 'Interlinear'
        else:
            css   = INTRALINEAR_CSS
            title = 'Intralinear'
        self.conn.execute(
            "INSERT INTO Mods (mode_id, title, css, active) VALUES (?, ?, ?, ?)",
            (1, title, css, 1)
        )
        self.conn.execute("""
            CREATE VIEW Bible AS
            SELECT Book, Chapter, Verse,
                '<style>' || (SELECT css FROM Mods WHERE active=1) || '</style>' || Scripture AS Scripture
            FROM _Bible
        """)
        self.conn.commit()

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
                          xref_placement=self.xref)

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
        if self.render_mode == 'interlinear':
            title = "BSB Interlinear Bible"
        else:
            title = "BSB Intralinear Bible"

        self.conn.execute("""
            INSERT INTO Details (
                Title, Abbreviation, Information, Version,
                OldTestament, NewTestament, Apocrypha, Strongs, RightToLeft
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            title,
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

    def render_verse_interlinear(self, tokens: list, header: str = None,
                                 note_id_map: dict = None,
                                 xrefs: list = None,
                                 xref_placement: int = 0) -> str:
        """Render tokens to interlinear HTML.

        Each aligned token becomes a <q> column: English on top, then one <lem>
        per source word side-by-side inside a <span> row.  Unaligned tokens also
        get a <q><e> wrapper so they stay on the same vertical grid.

        Punctuation absorption:
          - Leading (plain token with skip_space_after): accumulated in `pending`
            and prepended to the next aligned token's <e>.
          - Trailing (aligned token with skip_space_after followed by plain token):
            peeked forward and appended to the current <e>, then skipped.
        """
        note_id_map = note_id_map or {}
        xrefs       = xrefs or []
        parts       = []
        skip        = set()   # indices already absorbed as trailing punctuation
        pending     = ''      # leading punctuation waiting for next aligned token

        if header:
            parts.append(f'<b class="headline">{header}</b><br>')

        if xref_placement == 1:
            parts.append(self._xref_markers(xrefs))

        for i, token in enumerate(tokens):
            if i in skip:
                continue

            is_plain = token.is_plain_text or not token.source_words

            if is_plain:
                if token.skip_space_after:
                    # Leading punctuation — hold for the next aligned token
                    pending += token.english
                else:
                    # Truly standalone plain text
                    text    = pending + token.english
                    pending = ''
                    parts.append(f'<q><e>{text}</e></q>')
            else:
                english = pending + token.english
                pending = ''

                # Absorb any immediately following plain-text tokens that are
                # glued to this one via skip_space_after chains.
                j         = i + 1
                cur_skip  = token.skip_space_after
                while cur_skip and j < len(tokens):
                    next_tok = tokens[j]
                    if next_tok.is_plain_text or not next_tok.source_words:
                        english += next_tok.english
                        skip.add(j)
                        cur_skip = next_tok.skip_space_after
                        j += 1
                    else:
                        break

                segments = []
                for sw in token.source_words:
                    xlit    = self.transliterate(sw.text, sw.lang, sw.is_proper)
                    strongs = sw.stem.strongs
                    if sw.lang == 'G':
                        seg = (f'<lem>'
                               f'<gs>{sw.text}</gs>'
                               f'<xlit>{xlit}</xlit>'
                               f'<num>{strongs}</num>'
                               f'<tvm>{sw.stem.morph}</tvm>'
                               f'</lem>')
                    else:
                        seg = (f'<lem>'
                               f'<heb>{sw.text}</heb>'
                               f'<xlit>{xlit}</xlit>'
                               #f'<num>{strongs}</num>'
                               f'<tvm>{sw.stem.morph}</tvm>'
                               f'</lem>')
                    segments.append(seg)

                parts.append(
                    f'<qi>'
                    f'<e>{english}</e>'
                    f'<span>{"".join(segments)}</span>'
                    f'</qi>'
                )

                for note in token.notes:
                    seq = note_id_map.get(note['noteId'], note['noteId'])
                    parts.append(f' <not>N{seq}</not>')

        if xref_placement == 2:
            parts.append(self._xref_markers(xrefs))

        return ''.join(parts)

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
                    xlit   = self.transliterate(sw.text, sw.lang, sw.is_proper)
                    # cls    = 'xlitH' if sw.lang != 'G' else 'xlitG'
                    lemmas.append(
                        f'<span class="stk">'
                        f'{xlit}'
                        f'<num>{sw.stem.strongs}</num>'
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

