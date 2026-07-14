import sqlite3
import json
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULE_NAME='Robinson\'s Morphological Analysis Codes - Extended'
MODULE_ABBREV='RMAC'
MODULE_VERSION='1.3'
MODULE_CREATOR='Costas Stergiou (root@theword.gr), Rúbio Terra (rubio.terra@gmail.com)'
MODULE_DESCRIPTION='''This is a list of abbreviations for the grammar morphology codes that are used in various Bible texts. Several New Testament texts are tagged with an abbreviation code after each word that explains its grammar, and this dictionary contains the analytical explanation of each abbreviation. This has been extended to cover Hebrew morphologies.'''
MODULE_CSS=''
REF_AUTHOR='Maurice A. Robinson'

class DictionaryWriter:
    def __init__(self, cursor):
        self.cursor = cursor
        self.init_tables()

    def init_tables(self):
        pass

    def write_entry(self, parms):
        pass


class EswordDictionaryWriter(DictionaryWriter):

    def init_tables(self):
        # Create and populate Details table
        sql_create = '''CREATE TABLE Details (Title NVARCHAR(255), Abbreviation NVARCHAR(50), Information TEXT, Version INT);'''
        self.cursor.execute(sql_create)
        sql_insert = '''INSERT INTO Details (Title, Abbreviation, Information, Version) VALUES (?, ?, ?, ?)'''
        self.cursor.execute(sql_insert, (MODULE_NAME, MODULE_ABBREV, MODULE_DESCRIPTION, 4))

        # Create and populate Dictionary table
        self.cursor.execute('CREATE TABLE Dictionary (Topic NVARCHAR(100), Definition TEXT)')
        self.cursor.execute('CREATE INDEX TopicIndex ON Dictionary (Topic)')

    def write_entry(self, parms):
        if len(parms) > 2:
           parms = parms[:2]
        self.cursor.execute("INSERT INTO Dictionary (Topic, Definition) VALUES (?, ?)", parms)

class MyswordDictionaryWriter(DictionaryWriter):
    def init_tables(self):
        # Create and populate details table
        self.cursor.execute("""CREATE TABLE Details (
                title TEXT,
                abbreviation TEXT,
                description TEXT,
                comments TEXT,
                author TEXT,
                strong INTEGER,
                version TEXT,
                versiondate DATETIME,
                publishdate TEXT, publisher TEXT, creator TEXT, source TEXT, language NVARCHAR(3),
                editorialcomments TEXT,
                righttoleft INT default 0,
                customcss TEXT)""")
        params = (MODULE_NAME, MODULE_ABBREV, MODULE_DESCRIPTION, REF_AUTHOR, MODULE_VERSION, dt.date.today().strftime('%Y-%m-%d'), MODULE_CSS)
        self.cursor.execute('''INSERT INTO details (title, abbreviation, description, author, version, versiondate, customcss)
        VALUES (?, ?, ?, ?, ?, ?, ?)''', params)

        # create dictionary table
        self.cursor.execute("""CREATE TABLE dictionary(
                relativeorder INTEGER,
                word TEXT primary key collate nocase,
                data TEXT)""")
        self.cursor.execute('CREATE INDEX dictionary_relativeorder on dictionary(relativeorder asc)')

    def write_entry(self, parms):
        if len(parms) < 3:
            raise ValueError('Three values are required')
        self.cursor.execute("INSERT INTO dictionary (word, data, relativeorder) VALUES (?, ?, ?)", parms)


if __name__ == '__main__':
    ES_TARGET = ROOT / 'output' / 'rmac.dcti'
    MS_TARGET = ROOT / 'output' / 'rmac.dct.mybible'
    SRC_FILE = ROOT / 'data' / 'rmac.json'

    if ES_TARGET.exists():
        ES_TARGET.unlink()
    if MS_TARGET.exists():
        MS_TARGET.unlink()
    es_db = sqlite3.connect(ES_TARGET)
    ms_db = sqlite3.connect(MS_TARGET)
    es_writer = EswordDictionaryWriter(es_db.cursor())
    ms_writer = MyswordDictionaryWriter(ms_db.cursor())
    # es_writer.write_entry(('entree', 'description'))
    rmac_data = json.load(open(SRC_FILE))
    for i, (key, val) in enumerate(rmac_data.items()):
        params = (key, val, i+1)
        es_writer.write_entry(params)
        ms_writer.write_entry(params)
    es_db.commit()
    es_db.close()
    ms_db.commit()
    ms_db.close()
    print(f'Wrote {len(rmac_data)} entries to {ES_TARGET} and {MS_TARGET}')



