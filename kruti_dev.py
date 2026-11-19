"""Kruti Dev 010 -> Unicode Devanagari. Fixed rules, no model.
Many Haryana DESA PDFs store Hindi in the legacy Kruti Dev font: the text layer is Latin-looking
('ftyk lkaf[;dh;') and only the glyphs are Hindi. Digits and punctuation in the same layer are exact,
so decoding the letters is enough; no OCR is needed for these files.
Pipeline: longest-match table scan -> move the pre-base vowel sign (i-matra) after its consonant cluster
-> move reph (r+halant) in front of its cluster -> tidy.
Known limits (reported, not hidden): the PDF text layer sometimes splits a word with a stray space
('d s' for 'ds'); such labels may stay imperfect. Numbers and units are not affected."""
import re

HAL = "्"
_MAP = {
    # independent vowels
    "vkS": "औ", "vks": "ओ", "vk": "आ", "v": "अ", "bZ": "ई", "b": "इ", "m": "उ", "Å": "ऊ", ",s": "ऐ", ",": "ए", "_": "ऋ",
    # vowel signs
    "kS": "ौ", "ks": "ो", "k": "ा", "h": "ी", "q": "ु", "w": "ू", "`": "ृ", "s": "े", "S": "ै",
    "a": "ं", "¡": "ँ", "%": "ः", "W": "ॉ", "kW": "ॉ", "~": HAL, "z": HAL + "र", "Z": "",
    # consonants
    "d": "क", "[k": "ख", "x": "ग", "?k": "घ", "³": "ङ", "p": "च", "N": "छ", "t": "ज", ">": "झ", "¥": "ञ",
    "V": "ट", "B": "ठ", "M": "ड", "<": "ढ", ".k": "ण", "r": "त", "Fk": "थ", "n": "द", "/k": "ध", "u": "न",
    "i": "प", "Q": "फ", "c": "ब", "Hk": "भ", "e": "म", ";": "य", "j": "र", "y": "ल", "G": "ळ", "o": "व",
    "'k": "श", "\"k": "ष", "l": "स", "g": "ह",
    # half forms
    "D": "क" + HAL, "[": "ख" + HAL, "X": "ग" + HAL, "?": "घ" + HAL, "P": "च" + HAL, "T": "ज" + HAL,
    "F": "थ" + HAL, "/": "ध" + HAL, "C": "ब" + HAL, "H": "भ" + HAL, "E": "म" + HAL, "Y": "ल" + HAL,
    "L": "स" + HAL, "'": "श" + HAL, "\"": "ष" + HAL, "U": "न" + HAL, "R": "त" + HAL, "I": "प" + HAL,
    "O": "व" + HAL, "¶": "फ" + HAL,
    # conjuncts
    "{k": "क्ष", "{": "क्ष" + HAL, "K": "ज्ञ", "=": "त्र", "J": "श्र", "Ø": "क्र", "ô": "क्ल", "#": "ट्ठ", "ß": "द्ध", "¸": "ट्ट",
    # punctuation
    "|": "द्य", "\ue002": "ण" + HAL, "\ue003": "रु", "A": "।", "¼": "(", "½": ")", "&": "–", "f": "",
}
_MAXLEN = max(len(k) for k in _MAP)
_CONS = "क-हळ"
_MATRA = "ािीुूृेैोौंँॉ"

def convert(text):
    text = re.sub(r"(?<=[A-Za-z])\.(?=[A-Za-z])(?!k)", "\ue002", text)       # in-word '.' = half-ण ('mi&e.My' = उपमण्डल); '.k' = ण is handled by the table
    text = re.sub(r"(?<=[A-Za-z\"'])\:(?=[A-Za-z\"'])", "\ue003", text)              # in-word ':' = रु ('iq:"k' = पुरुष)
    out, i, n = [], 0, len(text)
    while i < n:
        for L in range(min(_MAXLEN, n - i), 0, -1):
            u = _MAP.get(text[i:i + L])
            if u is not None: out.append(u); i += L; break
        else: out.append(text[i]); i += 1
    s = "".join(out)
    # pre-base i-matra () is written before the cluster; move it after the cluster
    s = re.sub(rf"((?:[{_CONS}]{HAL})*[{_CONS}])", "\\1ि", s)
    s = s.replace("", "ि")
    # reph () is written after the syllable; move it in front of the cluster
    s = re.sub(rf"((?:[{_CONS}]{HAL})*[{_CONS}][{_MATRA}]*)", "र्\\1", s)
    s = s.replace("", "र्")
    s = s.replace("‍", "")
    s = re.sub(r"\s+ः", " :", s)
    return re.sub(r"[ \t]+", " ", s).strip()

_NUMLIKE = re.compile(r"^[\d.,\-()/%rDA+*]*\d[\d.,\-()/%rDA+*]*$|^\.\.+$|^-+$|^\.$")
_ENG = re.compile(r"^(?:\d+\.)?[A-Z][a-z]+$")          # Capitalised English-looking word such as 'Bhiwani'
def convert_line(line, english=frozenset()):
    """Convert a text line token by token so numbers, '..' markers, year labels and listed English words stay as printed."""
    out = []
    for tok in line.split(" "):
        bare = re.sub(r"^\d+\.", "", tok)
        if not tok: continue
        if _NUMLIKE.match(tok) or re.fullmatch(r"(?:19|20)\d{2}(?:[-&–]\d{2,4})?(?:\([A-Z]\))?", tok): out.append(tok.replace("&", "-"))
        elif bare.lower() in english or tok.lower() in english: out.append(tok)
        else: out.append(convert(tok))
    return " ".join(out)

def looks_legacy(text, threshold=0.2):
    """Same idea as pdf_agent.legacy_font_score; kept here so this module stands alone."""
    toks = [t for t in text.split() if len(t) >= 3 and not any(ch.isdigit() for ch in t)]
    if not toks: return False
    bad = [t for t in toks if re.search(r"[A-Za-z][\[\];\"”“'’&{}<>^@/+][A-Za-z]|[A-Za-z]\.[A-Za-z]", t)]
    return len(bad) / len(toks) > threshold
