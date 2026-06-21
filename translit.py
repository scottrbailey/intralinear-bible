"""
translit_v2.py

Hebrew transliterator — refactored from biblical_transliteration library.
Uses a single scheme dict with dagesh-keyed consonants for clean extensibility.

Scheme dict keys:
  Hebrew char        — consonant without dagesh (or non-BGDKPT consonant)
  Hebrew char+dagesh — consonant with dagesh (BGDKPT hard forms + dagesh forte doubling)
  Vowel point char   — vowel point
  'syllable_sep'     — syllable separator character (e.g. middle dot, hyphen, empty)
  'stress_marker'    — stress marker character or None
"""

import re
import unicodedata

import biblical_transliteration as bt
from typing import Optional


# ================== UNICODE CONSTANTS ==================

DAGESH     = '\u05BC'
SHIN_DOT   = '\u05C1'
SIN_DOT    = '\u05C2'
MAQAF      = '\u05BE'
SOF_PASUQ  = '\u05C3'
METEG      = '\u05BD'
PASEQ      = '\u05C0'  # vertical bar used as separator between words

GUTTURALS  = {'\u05D0', '\u05D4', '\u05D7', '\u05E2', '\u05E8'}  # א ה ח ע ר
BEGADKEFAT = {'\u05D1', '\u05D2', '\u05D3', '\u05DB', '\u05E4', '\u05EA'}  # ב ג ד כ פ ת

VOWEL_POINTS = {
    '\u05B0', '\u05B1', '\u05B2', '\u05B3', '\u05B4', '\u05B5',
    '\u05B6', '\u05B7', '\u05B8', '\u05B9', '\u05BA', '\u05BB',
    '\u05BC', '\u05BD', '\u05BE', '\u05BF', '\u05C1', '\u05C2',
    '\u05C3', '\u05C4', '\u05C5', '\u05C7',
}

SHORT_VOWELS = {'\u05B7', '\u05B6', '\u05B4', '\u05BB'}  # patach, segol, hiriq, qibbuts
LONG_VOWELS  = {'\u05B5', '\u05B9', '\u05BA'}             # tsere, holam, holam haser

FINAL_FORMS = {
    '\u05DA': '\u05DB',  # Final Kaf  -> Kaf
    '\u05DD': '\u05DE',  # Final Mem  -> Mem
    '\u05DF': '\u05E0',  # Final Nun  -> Nun
    '\u05E3': '\u05E4',  # Final Pe   -> Pe
    '\u05E5': '\u05E6',  # Final Tsade -> Tsade
}


# ================== BASE SCHEME ==================

BASE = {
    # ---- Consonants (without dagesh = default/spirant form) ----
    '\u05D0': '\u2018',   # Aleph א
    '\u05D1': 'v',        # Bet ב spirant
    '\u05D1\u05BC': 'b',  # Bet ב hard
    '\u05D2': 'g',        # Gimel ג
    '\u05D3': 'd',        # Dalet ד
    '\u05D4': 'h',        # He ה
    '\u05D5': 'v',        # Vav ו
    '\u05D6': 'z',        # Zayin ז
    '\u05D7': 'ch',       # Chet ח
    '\u05D8': 't',        # Tet ט
    '\u05D9': 'y',        # Yod י
    '\u05DB': 'kh',       # Kaf כ spirant
    '\u05DB\u05BC': 'k',  # Kaf כ hard
    '\u05DC': 'l',        # Lamed ל
    '\u05DE': 'm',        # Mem מ
    '\u05E0': 'n',        # Nun נ
    '\u05E1': 's',        # Samekh ס
    '\u05E2': '\u2019',   # Ayin ע
    '\u05E4': 'f',        # Pe פ spirant
    '\u05E4\u05BC': 'p',  # Pe פ hard
    '\u05E6': 'tz',       # Tsade צ
    '\u05E7': 'q',        # Qof ק
    '\u05E8': 'r',        # Resh ר
    '\u05E9': 'sh',       # Shin ש (default, sin dot changes it)
    '\u05E9\u05C2': 's',  # Sin ש with sin dot
    '\u05EA': 't',        # Tav ת

    # ---- Vowels ----
    '\u05B0': '\u1D49',   # Sheva ə (superscript e)
    '\u05B1': 'e',        # Hataf Segol
    '\u05B2': 'a',        # Hataf Patach
    '\u05B3': 'o',        # Hataf Qamats
    '\u05B4': 'i',        # Hiriq
    '\u05B5': 'e',        # Tsere
    '\u05B6': 'e',        # Segol
    '\u05B7': 'a',        # Patach
    '\u05B8': 'a',        # Qamats gadol
    '\u05B9': 'o',        # Holam
    '\u05BA': 'o',        # Holam haser (for vav)
    '\u05BB': 'u',        # Qibbuts
    '\u05C7': 'o',        # Qamats qatan

    # ---- Scheme config ----
    'syllable_sep':  '',
    'stress_marker': None,
    'divine_name':   'Yehovah',
}


# ================== SCHEME DEFINITIONS ==================

SCHEMES = {
    'brill_simple': {
        **BASE,
        '\u05D0': '',         # Aleph dropped
        '\u05E2': '',         # Ayin dropped
        'stress_marker': '\u0301',
        'syllable_sep': '\u00B7',  # middle dot
        'divine_name': 'Yᵉ·hó·vah',
    },

    'sbl_simple': {
        **BASE,
        '\u05D0': '',         # Aleph dropped
        '\u05E2': '',         # Ayin dropped
        '\u05D7': 'ch',
        '\u05DB': 'k',
        '\u05DB\u05BC': 'k',
        '\u05E4': 'f',
        '\u05E4\u05BC': 'p',
        '\u05E6': 'ts',
        '\u05E5': 'ts',
        '\u05B0': 'e',        # Sheva -> plain e
        'drop_final_he': True,
    },

    'sbl_academic': {
        **BASE,
        '\u05D0': '\u02BE',   # Aleph ʾ
        '\u05D1': '\u1E87',   # Bet spirant ḇ — actually ḇ U+1E07... use v for now
        '\u05D1': 'v',        # Bet spirant (Brill compatible)
        '\u05D1\u05BC': 'b',
        '\u05D5': 'w',        # Vav -> w in academic
        '\u05D7': '\u1E25',   # Chet ḥ
        '\u05DB': 'k',
        '\u05DB\u05BC': 'k',
        '\u05E2': '\u02BF',   # Ayin ʿ
        '\u05E4': 'f',
        '\u05E4\u05BC': 'p',
        '\u05E6': '\u1E63',   # Tsade ṣ
        '\u05E5': '\u1E63',
        '\u05E9': '\u0161',   # Shin š
        '\u05B0': '\u0259',   # Sheva ə
        '\u05B1': '\u0115',   # Hataf Segol ĕ
        '\u05B2': '\u0103',   # Hataf Patach ă
        '\u05B3': '\u014F',   # Hataf Qamats ŏ
        '\u05B5': '\u0113',   # Tsere ē
        '\u05B8': '\u0101',   # Qamats ā
        '\u05B9': '\u014D',   # Holam ō
        '\u05BB': '\u00FB',   # Qibbuts û
        'stress_marker': '\u0301',
    },

    'phonetic_dot': {
        **BASE,
        '\u05D0': '',         # Aleph dropped
        '\u05D4': 'h',        # He
        '\u05D7': 'ch',
        '\u05D8': 't',
        '\u05DB': 'kh',
        '\u05DB\u05BC': 'k',
        '\u05E2': '',         # Ayin dropped
        '\u05E4': 'f',
        '\u05E4\u05BC': 'p',
        '\u05E6': 'ts',
        '\u05E7': 'k',        # Qof -> k in phonetic
        '\u05B0': 'e',
        '\u05B4': 'i',
        '\u05B5': 'e',
        '\u05B7': 'a',
        '\u05B8': 'a',
        '\u05B9': 'o',
        '\u05BB': 'u',
        'syllable_sep': '\uA78F',  # sinological dot
        'stress_marker': '\u0301',   # combining acute on stressed vowel
        'divine_name':   'Adonai',
    },
}


# ================== HELPERS ==================

def is_hebrew(char: str) -> bool:
    return '\u05D0' <= char <= '\u05EA'

def is_combining(char: str) -> bool:
    # These are NOT combining marks despite being in the Hebrew block range
    if char in {PASEQ, SOF_PASUQ, MAQAF}:
        return False
    return '\u05B0' <= char <= '\u05C7' or '\u0591' <= char <= '\u05AF'

def get_marks(chars: list, i: int) -> list:
    """Collect combining marks following position i."""
    marks = []
    j = i + 1
    while j < len(chars) and is_combining(chars[j]):
        marks.append(chars[j])
        j += 1
    return marks

def prev_base_loc(chars: list, i: int) -> int:
    """Return index of nearest base character before i, skipping combining marks."""
    j = i - 1
    while j >= 0 and is_combining(chars[j]):
        j -= 1
    return j

def has_preceding_vowel_point(chars: list, i: int) -> bool:
    """Check if there's a full vowel point (not sheva) between the previous base char and i.
    Sheva is excluded because sheva nach closes a syllable and does not make dagesh forte."""
    SHEVA = '\u05B0'
    j = i - 1
    while j >= 0 and is_combining(chars[j]):
        if (chars[j] in VOWEL_POINTS
                and chars[j] not in {DAGESH, METEG, SHEVA}):
            return True
        j -= 1
    return False


# ================== DIVINE NAME ==================

TETRA = ['\u05D9', '\u05D4', '\u05D5', '\u05D4']  # י ה ו ה

def find_tetragrammaton(chars: list) -> list:
    spans = []
    i = 0
    while i < len(chars):
        if chars[i] == TETRA[0]:
            positions = [i]
            ti = 1
            j = i + 1
            while j < len(chars) and ti < 4:
                if is_combining(chars[j]):
                    j += 1
                elif chars[j] == TETRA[ti]:
                    positions.append(j)
                    ti += 1
                    j += 1
                else:
                    break
            if ti == 4:
                end = positions[-1] + 1
                while end < len(chars) and is_combining(chars[end]):
                    end += 1
                spans.append((i, end))
                i = end
                continue
        i += 1
    return spans

def substitute_divine_name(text: str, substitute: str) -> str:
    if not substitute:
        return text
    chars = list(text)
    spans = find_tetragrammaton(chars)
    if not spans:
        return text
    result = []
    last = 0
    for start, end in spans:
        result.append(text[last:start])
        result.append(substitute)
        last = end
    result.append(text[last:])
    return ''.join(result)


# ================== MATER LECTIONIS ==================

def is_mater_lectionis(chars: list, i: int, scheme_name: str,
                       scheme: dict = None) -> bool:
    """Check if character at i is a mater lectionis (silent vowel letter)."""
    if i <= 0 or i >= len(chars):
        return False
    char = chars[i]

    # Collect preceding vowels
    prev_vowels = []
    for j in range(i - 1, -1, -1):
        if is_hebrew(chars[j]):
            for k in range(j + 1, i):
                if is_combining(chars[k]):
                    prev_vowels.append(chars[k])
            break
        elif is_combining(chars[j]):
            prev_vowels.append(chars[j])

    # Yod as mater lectionis
    if char == '\u05D9':
        is_word_initial = True
        for k in range(i - 1, -1, -1):
            if is_hebrew(chars[k]):
                is_word_initial = False
                break
            elif not is_combining(chars[k]):
                break
        if is_word_initial:
            return False
        if '\u05B4' in prev_vowels:  # hiriq
            return True
        if '\u05B5' in prev_vowels:  # tsere
            return True
        if '\u05B6' in prev_vowels:  # segol
            return True

    # Aleph as mater (Simple/Phonetic only — SBL always emits ʾ)
    if char == '\u05D0' and scheme_name != 'sbl_academic':
        aleph_vowels = []
        for k in range(i + 1, len(chars)):
            if is_combining(chars[k]):
                aleph_vowels.append(chars[k])
            else:
                break
        if prev_vowels and not any(v in aleph_vowels for v in VOWEL_POINTS - {DAGESH, METEG}):
            return True

    # He as mater (silent final he — he with NO vowel point of its own)
    # We only drop final he if the scheme explicitly requests it via 'drop_final_he'
    # By default we preserve it (gives hinneh, leokhlah, laylah)
    # This avoids false positives like dropping consonantal -āh suffixes
    if char == '\u05D4':
        # Check if this is word-final
        is_final = True
        for k in range(i + 1, len(chars)):
            if is_hebrew(chars[k]):
                is_final = False
                break
            elif not is_combining(chars[k]):
                break

        if is_final:
            he_marks = []
            for k in range(i + 1, len(chars)):
                if is_combining(chars[k]):
                    he_marks.append(chars[k])
                else:
                    break

            # He with mappiq (dagesh) is always consonantal
            if DAGESH in he_marks:
                return False

            # He with its OWN vowel point is a consonantal suffix (-ah, -āh)
            he_vowels = [m for m in he_marks
                         if m in VOWEL_POINTS and m not in {DAGESH, METEG, SHIN_DOT, SIN_DOT}
                         and ord(m) not in CANTILLATION]
            if he_vowels:
                return False  # consonantal suffix, keep it

            # Final he with no vowel — preserve by default
            # Only drop if scheme explicitly sets 'drop_final_he': True
            if scheme and scheme.get('drop_final_he', False) and prev_vowels:
                return True  # silent mater lectionis — drop only if scheme opts in

    return False


# ================== VOCAL SHEVA ==================

def is_vocal_sheva(chars: list, i: int) -> bool:
    """Determine if sheva at position i is vocal (na) or silent (nach)."""
    # Word-final sheva is always silent
    is_final = True
    for k in range(i + 1, len(chars)):
        if is_hebrew(chars[k]):
            is_final = False
            break
        elif chars[k] == '/':
            continue
        elif not is_combining(chars[k]):
            break
    if is_final:
        return False

    # Word-initial sheva is vocal
    is_word_initial = True
    for k in range(i - 1, -1, -1):
        if is_hebrew(chars[k]):
            is_word_initial = False
            break
        elif chars[k] == MAQAF:
            break
        elif not is_combining(chars[k]):
            break
    if is_word_initial:
        return True

    # Collect previous consonant's vowels
    prev_vowels = []
    for k in range(i - 1, -1, -1):
        if is_hebrew(chars[k]):
            for m in range(k + 1, i):
                if is_combining(chars[m]) and chars[m] != DAGESH:
                    prev_vowels.append(chars[m])
            break

    # Consecutive sheva: second is vocal
    if '\u05B0' in prev_vowels:
        return True

    # Sheva before identical consonant is vocal
    next_consonant = None
    for k in range(i + 1, len(chars)):
        if is_hebrew(chars[k]):
            next_consonant = chars[k]
            break
        elif not is_combining(chars[k]):
            break
    # Get current base char
    base = chars[prev_base_loc(chars, i + 1) + 1] if prev_base_loc(chars, i + 1) >= 0 else None
    if next_consonant and base and next_consonant == base:
        return True

    if any(v in SHORT_VOWELS for v in prev_vowels):
        # Exception: sheva after dagesh forte is always vocal
        current_marks = get_marks(chars, i)
        if DAGESH in current_marks and has_preceding_vowel_point(chars, i):
            return True
        return False
    if any(v in LONG_VOWELS for v in prev_vowels):
        return True

    return False


# ================== QAMATS QATAN ==================

def is_qamats_qatan(chars: list, i: int) -> bool:
    """Determine if qamats at i is qamats qatan (o) rather than gadol (a)."""
    SHEWA       = '\u05B0'
    HATAF_QAMATS = '\u05B3'
    FULL_VOWELS = set('\u05B1\u05B2\u05B3\u05B4\u05B5\u05B6\u05B7\u05B8\u05B9\u05BA\u05BB\u05C7')

    next_consonant_idx = None
    next_marks = []
    for k in range(i + 1, len(chars)):
        ch = chars[k]
        if ch in ' \t\n':
            break
        if ch == MAQAF:
            return True
        if is_hebrew(ch):
            next_consonant_idx = k
            for m in range(k + 1, len(chars)):
                if chars[m] == MAQAF:
                    return True
                if is_combining(chars[m]):
                    next_marks.append(chars[m])
                else:
                    break
            break
        if not is_combining(ch):
            break

    if next_consonant_idx is None:
        return False
    if HATAF_QAMATS in next_marks:
        return True

    has_shewa      = SHEWA in next_marks
    has_full_vowel = any(v in next_marks for v in FULL_VOWELS)

    if has_shewa and not has_full_vowel:
        for k in range(next_consonant_idx + 1, len(chars)):
            ch = chars[k]
            if ch in (' ', '\t', '\n', MAQAF):
                break
            if ch in FULL_VOWELS:
                return True
        return False

    if not has_shewa and not has_full_vowel:
        is_next_final = True
        for k in range(next_consonant_idx + 1, len(chars)):
            if is_hebrew(chars[k]):
                is_next_final = False
                break
            if not is_combining(chars[k]):
                break
        if not is_next_final:
            return False
        if chars[next_consonant_idx] == '\u05D4' and DAGESH not in next_marks:
            return False
        consonants_before = 0
        for k in range(i - 1, -1, -1):
            if is_hebrew(chars[k]):
                consonants_before += 1
            elif not is_combining(chars[k]):
                break
        return consonants_before == 0

    return False


# ================== CANTILLATION ==================

# Primary stress cantillation marks (te'amim) — disjunctive accents
CANTILLATION = set(range(0x0591, 0x05AF + 1))

def has_cantillation(marks: list) -> bool:
    """Check if any mark is a cantillation (te'am) mark indicating primary stress."""
    return any(ord(m) in CANTILLATION for m in marks)


# ================== MAIN TRANSLITERATOR ==================

def hebrew_translit(text: str, scheme_name: str = 'brill_simple') -> str:
    """
    Transliterate Hebrew text using the specified scheme.
    """
    scheme      = SCHEMES.get(scheme_name, SCHEMES['brill_simple'])
    divine      = scheme.get('divine_name', 'Yehovah')
    syl_sep     = scheme.get('syllable_sep', '')
    stress_mark = scheme.get('stress_marker', None)

    text = substitute_divine_name(
        unicodedata.normalize('NFC', text), divine
    )

    chars  = list(text)
    # Each unit: (text, has_vowel, has_stress, is_word_break)
    units  = []
    i      = 0
    tlen   = len(chars)

    def push_unit(text, has_vowel=False, has_stress=False, is_word_break=False):
        units.append((text, has_vowel, has_stress, is_word_break))

    while i < tlen:
        char = chars[i]

        # ---- Pass through non-Hebrew ----
        if not is_hebrew(char):
            if not is_combining(char):
                if char == SOF_PASUQ:
                    pass  # drop sof pasuq
                elif char == PASEQ:
                    push_unit(' ', is_word_break=True)  # paseq = word boundary
                elif char == MAQAF:
                    # Maqaf attaches to the preceding unit — append to its tail
                    # rather than creating a new unit that would get its own syllable
                    if units:
                        last = list(units[-1])
                        last[0] = last[0] + '-'
                        units[-1] = tuple(last)
                    else:
                        push_unit('-', is_word_break=False)
                elif char == ' ':
                    push_unit(' ', is_word_break=True)
                elif ord(char) == 0x05E4 and i > 0 and chars[i-1] == SOF_PASUQ:
                    pass  # paragraph pe after sof pasuq — drop
                elif is_hebrew(char) and char == '\u05E4':
                    # Pe used as paragraph marker — drop
                    pass
                else:
                    push_unit(char)
            i += 1
            continue

        # ---- Skip mater lectionis ----
        if is_mater_lectionis(chars, i, scheme_name, scheme):
            i += 1
            while i < tlen and is_combining(chars[i]):
                i += 1
            continue

        # ---- Skip paragraph markers (pe/samekh after sof pasuq) ----
        # Pe (פ) or Samekh (ס) with no vowel points and no geresh = paragraph marker
        # Geresh (׳ U+05F3) or gershayim (״ U+05F4) indicate numeric usage
        if char in {'\u05E4', '\u05E1'}:
            marks_ahead = get_marks(chars, i)
            has_vowel  = any(m in VOWEL_POINTS - {DAGESH, METEG, SHIN_DOT, SIN_DOT}
                             for m in marks_ahead)
            j = i + 1 + len(marks_ahead)
            has_geresh = j < tlen and chars[j] in {'\u05F3', '\u05F4'}
            if not has_vowel and not has_geresh:
                i += 1
                continue

        # Collect combining marks for this consonant
        marks      = get_marks(chars, i)
        has_dagesh   = DAGESH in marks
        has_sin_dot  = SIN_DOT in marks
        stressed     = has_cantillation(marks) or METEG in marks

        # ---- Resolve consonant ----
        base_char = FINAL_FORMS.get(char, char)

        # Vav as vowel letter
        if base_char == '\u05D5':
            if '\u05B9' in marks or '\u05BA' in marks:
                push_unit(scheme.get('\u05B9', 'o'), has_vowel=True, has_stress=stressed)
                i += 1 + len(marks)
                continue
            elif has_dagesh and not any(
                v in marks for v in ['\u05B4','\u05B5','\u05B6','\u05B7','\u05B8']
            ):
                push_unit(scheme.get('\u05BB', 'u'), has_vowel=True, has_stress=stressed)
                i += 1 + len(marks)
                continue

        # Shin/Sin
        if base_char == '\u05E9':
            key = '\u05E9\u05C2' if has_sin_dot else '\u05E9'
            consonant = scheme.get(key, 'sh')
        elif base_char in BEGADKEFAT:
            key = base_char + DAGESH if has_dagesh else base_char
            consonant = scheme.get(key, scheme.get(base_char, '?'))
        else:
            consonant = scheme.get(base_char, '?')

        # Dagesh forte handling (non-guttural letters)
        # For BGDKPT: dagesh after vowel = forte, else lene (just hard form)
        # For non-BGDKPT: dagesh is always forte
        # With syllable separator: split the consonant across the boundary
        #   push a vowel-less unit to close the previous syllable,
        #   then the consonant+vowel unit opens the next — gives hash·sha not ha·shsha
        # Without syllable separator: double the consonant (only single chars)
        is_forte = (has_dagesh and base_char not in GUTTURALS and
                    (base_char not in BEGADKEFAT or
                     has_preceding_vowel_point(chars, i)))
        if is_forte and syl_sep:
            # Emit closing consonant now, then fall through to emit opening consonant+vowel
            push_unit(consonant, has_vowel=False, has_stress=False)
            # don't double — the same consonant will be emitted again below with its vowels
        elif is_forte and len(consonant) == 1:
            consonant = consonant + consonant

        # ---- Process vowels ----
        vowels = []
        for mark in marks:
            if mark in {DAGESH, METEG, SHIN_DOT, SIN_DOT}:
                continue
            if ord(mark) in CANTILLATION:
                continue
            if mark in VOWEL_POINTS:
                if mark == '\u05B0':  # sheva
                    if is_vocal_sheva(chars, i):
                        vowels.append(scheme.get('\u05B0', ''))
                elif mark == '\u05B8':  # qamats
                    if is_qamats_qatan(chars, i):
                        vowels.append(scheme.get('\u05C7', 'o'))
                    else:
                        vowels.append(scheme.get('\u05B8', 'a'))
                else:
                    v = scheme.get(mark, '')
                    if v:
                        vowels.append(v)

        # SBL academic: hiriq+yod -> î, tsere+yod -> ê
        if scheme_name == 'sbl_academic' and vowels:
            j = i + 1 + len(marks)
            if j < tlen and chars[j] == '\u05D9' and is_mater_lectionis(chars, j, scheme_name, scheme):
                _LONG = {'i': '\u00EE', 'e': '\u00EA'}
                last = vowels[-1]
                if last in _LONG:
                    vowels[-1] = _LONG[last]

        # Furtive patach
        FURTIVE_GUTTURALS = {'\u05D7', '\u05E2'}
        if base_char in FURTIVE_GUTTURALS and '\u05B7' in marks:
            is_word_final = True
            for k in range(i + 1, tlen):
                if is_hebrew(chars[k]):
                    is_word_final = False
                    break
                elif not is_combining(chars[k]):
                    break
            if is_word_final:
                push_unit('a' + consonant, has_vowel=True, has_stress=stressed)
                i += 1 + len(marks)
                continue

        has_v = bool(vowels)
        unit_text = consonant + ''.join(vowels)
        push_unit(unit_text, has_vowel=has_v, has_stress=stressed)
        i += 1 + len(marks)

    # ---- Post-process units into final string ----
    return _build_output(units, syl_sep, stress_mark, scheme_name)


VOWEL_CHARS = set('aeiouāēīōūᵉĕăŏâêîôû\u0259')


def _apply_stress(text: str, stress_mark: str) -> str:
    """Insert stress marker on or before the first vowel in text.
    If stress_mark is U+0301 (combining acute), replaces plain vowels with
    precomposed accented equivalents (á é í ó ú) for clean rendering.
    Other markers go BEFORE the vowel.
    If no vowel found, return text unchanged."""
    if not stress_mark:
        return text

    # Precomposed accented vowels for combining acute
    ACUTE_MAP = {
        'a': 'á', 'e': 'é', 'i': 'í', 'o': 'ó', 'u': 'ú',
        'A': 'Á', 'E': 'É', 'I': 'Í', 'O': 'Ó', 'U': 'Ú',
    }
    use_precomposed = (stress_mark == '\u0301')

    new_text = []
    inserted = False
    for ch in text:
        if not inserted and ch in VOWEL_CHARS:
            if use_precomposed and ch in ACUTE_MAP:
                new_text.append(ACUTE_MAP[ch])
            elif use_precomposed:
                new_text.append(ch)  # no mapping, leave unmarked
            else:
                new_text.append(stress_mark)  # before the vowel
                new_text.append(ch)
            inserted = True
        else:
            new_text.append(ch)
    return ''.join(new_text) if inserted else text


def _group_syllables(word_units: list) -> list:
    """
    Group units into syllables.
    Each syllable is: zero or more consonant-only units + one vowel-bearing unit.
    Trailing consonant-only units are appended to the last syllable.

    Special case: forte split — a vowel-less unit immediately followed by an
    identical vowel-bearing unit (e.g. sh + sha) means the first closes the
    previous syllable rather than opening the next. Gives hash·sha not ha·shsha.

    Returns list of (syllable_text, has_stress) tuples.
    """
    syllables = []
    pending   = []   # consonant-only units accumulating before next vowel
    stressed  = False

    for idx, (text, has_vowel, has_s, _) in enumerate(word_units):
        if not text:
            continue
        if has_s:
            stressed = True
        if has_vowel:
            # Check for forte split: pending has one unit whose text matches
            # the consonant prefix of this unit (e.g. pending=['sh'], this='sha')
            if (pending and len(pending) == 1
                    and text.startswith(pending[0][0])
                    and len(pending[0][0]) > 0):
                # This is a forte split — attach the closing consonant to prev syllable
                closing = pending[0][0]
                if syllables:
                    last_text, last_stress = syllables[-1]
                    syllables[-1] = (last_text + closing, last_stress)
                else:
                    syllables.append((closing, False))
                pending = []
                # Now this unit opens a new syllable (already starts with consonant)
                syllables.append((text, stressed))
                stressed = False
            else:
                # Normal case: pending consonants + this vowel-bearing unit
                syl_text = ''.join(p[0] for p in pending) + text
                syllables.append((syl_text, stressed))
                pending  = []
                stressed = False
        else:
            pending.append((text, has_vowel, has_s, False))

    # Trailing consonant-only units — attach to last syllable
    if pending:
        if syllables:
            last_text, last_stress = syllables[-1]
            extra = ''.join(p[0] for p in pending)
            syllables[-1] = (last_text + extra, last_stress)
        else:
            syllables.append((''.join(p[0] for p in pending), stressed))

    return syllables


def _build_output(units: list, syl_sep: str, stress_mark,
                  scheme_name: str) -> str:
    """
    Build final transliteration string from units.
    Inserts syllable separators and stress markers as configured.
    """
    if not syl_sep and not stress_mark:
        translit = ''.join(u[0] for u in units)
        translit = re.sub(r'\s+', ' ', translit).strip()
        return translit

    # Split units into words
    words   = []
    current = []
    for unit in units:
        text, has_vowel, has_stress, is_word_break = unit
        if is_word_break:
            if current:
                words.append(('word', current))
                current = []
            words.append(('break', text))
        else:
            current.append(unit)
    if current:
        words.append(('word', current))

    result_parts = []
    for kind, data in words:
        if kind == 'break':
            result_parts.append(data)
            continue

        syllables  = _group_syllables(data)
        word_parts = []

        for j, (syl_text, has_stress) in enumerate(syllables):
            if j > 0 and syl_sep:
                # Don't insert separator if previous syllable ends with maqaf
                # or if this syllable starts with maqaf remnant
                prev_text = syllables[j-1][0]
                if not prev_text.endswith('-'):
                    word_parts.append(syl_sep)
            if stress_mark and has_stress:
                syl_text = _apply_stress(syl_text, stress_mark)
            word_parts.append(syl_text)

        result_parts.append(''.join(word_parts))

    translit = ''.join(result_parts)
    translit = re.sub(r'\s+', ' ', translit).strip()

    return translit


def capitalize_translit(translit: str) -> str:
    """Capitalize first Latin letter, skipping leading non-alpha chars.
    Used for Greek proper nouns."""
    for idx, ch in enumerate(translit):
        if ch.isalpha():
            return translit[:idx] + ch.upper() + translit[idx + 1:]
    return translit


def lowercase_translit(translit: str) -> str:
    """Lowercase first Latin letter, skipping leading non-alpha chars.
    Used for Greek tokens that are capitalized in the source (sentence-initial)
    but are not proper nouns."""
    for idx, ch in enumerate(translit):
        if ch.isalpha():
            return translit[:idx] + ch.lower() + translit[idx + 1:]
    return translit



# ================== TRANSLITERATOR FACTORY ==================

def make_transliterator(hebrew_scheme: str = "brill_simple",
                        greek_scheme: str = "SIMPLE") -> callable:
    """Return a configured transliterate(text, lang, is_proper) function.

    Hebrew/Aramaic: routed through HebrewTransliterator
    Greek:          routed through biblical_transliteration library
    """
    _hebrew_t = HebrewTransliterator(hebrew_scheme)

    _greek_t = bt.GreekTransliterator(bt.GreekOptions(
        scheme=getattr(bt.GreekScheme, greek_scheme, bt.GreekScheme.SIMPLE)
    ))

    def transliterate(text: str, lang: str, is_proper: bool = False) -> str:
        if lang == 'G':
            result = _greek_t.transliterate(text)
            if not is_proper:
                result = lowercase_translit(result)
            return result
        else:  # H or A
            return _hebrew_t(text)

    return transliterate


# ================== CLASS INTERFACE ==================

class HebrewTransliterator:
    """Transliterator bound to a specific scheme.

    Usage:
        t = HebrewTransliterator('brill_simple')
        t('בְּרֵאשִׁ֖ית')              # callable
        t.transliterate('בְּרֵאשִׁ֖ית') # explicit
        t('יְהוָה')
    """

    def __init__(self, scheme_name: str = 'brill_simple'):
        if scheme_name not in SCHEMES:
            raise ValueError(f"Unknown scheme '{scheme_name}'. "
                             f"Available: {list(SCHEMES.keys())}")
        self.scheme_name = scheme_name
        self.scheme      = SCHEMES[scheme_name]

    def transliterate(self, text: str) -> str:
        return hebrew_translit(text, self.scheme_name)

    def __call__(self, text: str) -> str:
        return self.transliterate(text)

    def __repr__(self) -> str:
        return f"HebrewTransliterator(scheme='{self.scheme_name}')"


# ================== TEST ==================

if __name__ == '__main__':
    test = "בְּרֵאשִׁ֖ית בָּרָ֣א אֱלֹהִ֑ים אֵ֥ת הַשָּׁמַ֖יִם וְאֵ֥ת הָאָֽרֶץ"
    print(f"Input: {test}")
    for scheme_name in ['brill_simple', 'sbl_simple', 'sbl_academic', 'phonetic_dot']:
        t = HebrewTransliterator(scheme_name)
        print(f"{scheme_name:15}: {t(test)}")