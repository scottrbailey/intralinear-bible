"""
mysword_writer.py

MySword Bible module writer (.bbl.mybible).
Supports both interlinear (GBF) and intralinear (<lemma>) render modes.
"""

from pathlib import Path
from sqlite_writer import SQLiteBibleWriter


# ================== CSS AND VERSE RULES ==================

INTERLINEAR_CSS = """
sup { font-size: 70%; }
.xlitH a { color: blue; text-decoration: none; }
.xlitG a { color: green; text-decoration: none; }
"""

INTERLINEAR_RULES = ""  # GBF tags handled natively by MySword

INTRALINEAR_CSS = """
sup { font-size: 70%; }
.xlitH a { color: blue; text-decoration: none; }
.xlitG a { color: green; text-decoration: none; }
.ref { font-size: 0.65em; color: #333; background-color: #e8e8e8;
       border-radius: 3px; padding: 0 2px; text-decoration: none; }
"""

# VerseRules transform <lemma sn="H7225" o="רֵאשִׁ֖ית">bereshit</lemma>
# into superscript colored dictionary links.
# Note: tab character between regex and replacement is required by MySword.
INTRALINEAR_RULES = (
    '<lemma sn="(H[^ "]+)" o="([^"]*?)">([^<]*)</lemma>\t'
    '<sup class="xlitH"><a href="s$1">$3</a></sup>\n'
    '<lemma sn="(G[^ "]+)" o="([^"]*?)">([^<]*)</lemma>\t'
    '<sup class="xlitG"><a href="s$1">$3</a></sup>'
)

# CSS variant that stacks xlit above original script using inline-flex
# Uncomment in Details if you want to try the stacked display
STACKED_CSS = """
sup { font-size: 70%; }
.lemma-block {
    display: inline-flex;
    flex-direction: column;
    align-items: center;
    vertical-align: middle;
    line-height: 1.2;
}
.xlitH { color: blue; font-size: 0.65em; text-decoration: none; }
.xlitG { color: green; font-size: 0.65em; text-decoration: none; }
.orig   { color: #888; font-size: 0.65em; direction: rtl; }
"""

STACKED_RULES = (
    '<lemma sn="(H[^ "]+)" o="([^"]*?)">([^<]*)</lemma>\t'
    '<span class="lemma-block">'
    '<a class="xlit-h" href="s$1">$3</a>'
    '<span class="orig">$2</span>'
    '</span>\n'
    '<lemma sn="(G[^ "]+)" o="([^"]*?)">([^<]*)</lemma>\t'
    '<span class="lemma-block">'
    '<a class="xlit-g" href="s$1">$3</a>'
    '<span class="orig">$2</span>'
    '</span>'
)


class MySwordWriter(SQLiteBibleWriter):
    """Writes MySword .bbl.mybible SQLite Bible modules."""

    file_extension = '.bbl.mybible'

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

        if self.render_mode == 'interlinear':
            css   = INTERLINEAR_CSS
            rules = INTERLINEAR_RULES
        elif self.render_mode == 'intralinear_stacked':
            css   = STACKED_CSS
            rules = STACKED_RULES
        else:  # intralinear
            css   = INTRALINEAR_CSS
            rules = INTRALINEAR_RULES

        self.conn.execute("""
            INSERT INTO Details (
                Description, Abbreviation, Comments, Version,
                VersionDate, PublishDate, RightToLeft, OT, NT, Strong,
                CustomCSS, VerseRules
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "BSB Intralinear Bible",
            self.work_id,
            "Berean Standard Bible with inline Hebrew and Greek transliteration. "
            "Source language data from WLC (OT) and SBLGNT (NT) via Clear Bible "
            "Alignments project (CC BY 4.0).",
            "1.0",
            "2026-01-01",
            "2026-01-01",
            0,
            1 if self._has_ot else 0,
            1 if self._has_nt else 0,
            1,
            css,
            rules,
        ))

    def render_verse_intralinear(self, tokens: list,
                                 note_id_map: dict = None,
                                 header: str = None,
                                 xrefs: list = None,
                                 xref_placement: int = 0) -> str:
        """Render tokens to intralinear string with <lemma> tags.

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

        if header and self.headers:
            parts.append(f"<TS>{header}<Ts>")

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                if self.notes:
                    for note in token.notes:
                        parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")
            else:
                parts.append(token.english)
                parts.append(' ')
                lemmas = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper)
                    lemmas.append(
                        f'<lemma sn="{sw.stem.strongs}" o="{sw.text}">'
                        f'{xlit}'
                        f'</lemma>'
                    )
                parts.append(' '.join(lemmas))

                if self.notes:
                    for note in token.notes:
                        parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        return ''.join(parts)

    def render_verse_interlinear(self, tokens: list, header: str = None,
                                  note_id_map: dict = None,
                                  xrefs: list = None,
                                  xref_placement: int = 0):
        """Render tokens to GBF-tagged interlinear string.

        Format per aligned token (one <H>...<h> segment per source display-word):
          <Q><H>בְּרֵאשִׁית<WH7225><X>bereshit<x><h><E>in the beginning<e><q>

        Multiple source words in one alignment group get multiple segments:
          <Q><H>word1<WH1234><X>xlit1<x><h><H>word2<WH5678><X>xlit2<x><h><E>english<e><q>

        VerseRules transforms each <H>...<h> segment into a superscript link,
        then strips the outer <Q>...<q> wrapper leaving English + superscripts.
        """
        parts = []

        if header and self.headers:
            parts.append(f"<TS>{header}<Ts>")

        for i, token in enumerate(tokens):
            next_token = tokens[i + 1] if i + 1 < len(tokens) else None

            if token.is_plain_text or not token.source_words:
                parts.append(token.english)
                if self.notes:
                    for note in token.notes:
                        parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")
            else:
                segments = []
                for sw in token.source_words:
                    xlit = self.transliterate(sw.text, sw.lang, sw.is_proper)
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

                if self.notes:
                    for note in token.notes:
                        parts.append(f"<RF q={note['noteId']}>{note['text']}<Rf>")

            if not token.skip_space_after and next_token is not None:
                parts.append(' ')

        return ''.join(parts)