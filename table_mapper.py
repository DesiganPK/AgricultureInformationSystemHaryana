"""Table understanding shared by the agents: read a ruled table from a PDF page and decide what each column means.

Two deciders, used together:
  rules  fixed vocabularies (crop names, measures, units; Hindi and English)            -> always run, no model
  LLM    asked only for what the rules could not decide (policy 'fill', the default)    -> see llm.py
The LLM answer is checked before use: column numbers must exist, the crop must be a known crop, the measure must be in the
list, and a unit is accepted only if the words for that unit are actually printed in the title or header (grounded).
Fields filled by the LLM are stored with confidence 0.8 and needs_review = 1.
Numbers are never taken from the LLM: values are copied from the table cells by code.

AGRI_LLM_POLICY = fill (default: ask only when rules leave a gap) | always (ask for every table, still fill gaps only)
                | prefer (ask for every table and let a valid LLM answer override the rules; use after fine-tuning)"""
import json, os, re
import llm
from district_names import canonical

# ------------------------------------------------------------------ vocabularies
CROP_ALIASES = {
    "Rice": ["rice", "paddy", "चावल", "धान"], "Wheat": ["wheat", "गेहूं", "गेहूँ", "गेंहू", "गेहू", "गेंहूं"], "Jowar": ["jowar", "sorghum", "ज्वार"],
    "Bajra": ["bajra", "pearl millet", "बाजरा"], "Maize": ["maize", "मक्का", "मक्की"], "Barley": ["barley", "जौ", "जो"],
    "Gram": ["gram", "chickpea", "चना", "चने"], "Sugarcane": ["sugarcane", "sugar cane", "गन्ना", "गन्ने", "ईख"],
    "Cotton": ["cotton", "कपास"], "Cotton (American)": ["american cotton", "cotton american", "अमेरिकन कपास", "कपास अमेरिकन", "कपास अमरीकन", "अमरीकन कपास"],
    "Cotton (Desi)": ["desi cotton", "cotton desi", "देसी कपास", "कपास देसी", "देशी कपास", "कपास देशी"],
    "Rape/Mustard": ["rapeseed", "mustard", "rape and mustard", "rape & mustard", "rape/mustard", "सरसों", "राई", "राया", "सरसों व राई"],
    "Groundnut": ["groundnut", "मूंगफली"], "Sesamum": ["sesamum", "til", "तिल"], "Sunflower": ["sunflower", "सूरजमुखी"],
    "Potato": ["potato", "potatoes", "आलू"], "Onion": ["onion", "प्याज"], "Moong": ["moong", "mung", "मूंग"], "Urad": ["urad", "mash", "उड़द", "उडद", "माश"],
    "Arhar": ["arhar", "tur", "अरहर"], "Masur": ["masur", "massar", "lentil", "मसूर"], "Guar": ["guar", "ग्वार", "गवार"],
    "Tobacco": ["tobacco", "तम्बाकू"], "Chillies": ["chillies", "chilli", "मिर्च"], "Pulses": ["pulses", "total pulses", "दालें", "दालों", "दाले"],
    "Oilseeds": ["oilseeds", "total oilseeds", "तिलहन", "तेल बीज"], "Foodgrains": ["foodgrains", "total foodgrains", "खाद्यान्न", "खाद्यान्नों"],
    "Other foodgrains": ["other foodgrains", "other food grains", "अन्य खाद्यान्न", "अन्य खाद्यान्नों"], "Other pulses": ["other pulses", "अन्य दालें", "अन्य दालों", "अन्य दाले"],
    "Other oilseeds": ["other oilseeds", "अन्य तिलहन", "अन्य तेल बीज"],
    "Ragi": ["ragi", "रागी"], "Soyabean": ["soyabean", "soybean", "सोयाबीन"], "Linseed": ["linseed", "अलसी"], "Castor": ["castor", "castor seed", "अरंडी"],
    "Jute": ["jute", "जूट"], "Sannhemp": ["sannhemp", "sann-hemp", "सन"], "Turmeric": ["turmeric", "हल्दी"], "Garlic": ["garlic", "लहसुन"], "Coriander": ["coriander", "धनिया"],
    "Toria": ["toria", "तोरिया"], "Ginger": ["ginger", "अदरक"], "Pepper": ["pepper", "black pepper"], "Mesta": ["mesta"], "Gur": ["gur", "sugar raw", "गुड"],
    "Fodder": ["fodder", "चारा"], "Vegetables": ["vegetables", "सब्जियां", "सब्जियों"], "Fruits": ["fruits", "फल", "फलों"],
}
MEASURE_ALIASES = {      # checked in this order: 'yield' before 'production' and 'area' because 'average yield per hectare' names all three ideas
    "yield": ["average yield", "yield", "औसत उपज", "उपज", "avg. yield"],
    "production": ["production", "output", "उत्पादन"],
    "area": ["area", "क्षेत्रफल", "क्षेत्र"],
    "price": ["price", "prices", "मूल्य", "भाव"],
    "rainfall": ["rainfall", "rain", "वर्षा"],
    "percent": ["percentage", "percent", "%", "प्रतिशत", "प्रतिशतता"],
    "count": ["number", "no.", "nos.", "संख्या", "population", "persons", "जनसंख्या", "व्यक्ति", "व्यक्तियों"],
}
MEASURES = list(MEASURE_ALIASES) + ["other"]
ALLOWED_UNITS = ["ha", "00 ha", "000 ha", "lakh ha", "tonnes", "00 tonnes", "000 tonnes", "lakh tonnes", "kg/ha", "quintal/ha", "quintal", "000 quintal",
                 "Rs/quintal", "Rs", "000 Rs", "lakh Rs", "mm", "cm", "km", "sq km", "per sq km", "%", "number", "000", "lakh", "bales", "000 bales", "litres", "000 litres", "kg"]
UNIT_WORDS = {           # words that must be printed near a table for a unit to count as grounded
    "ha": ["हैक्टेयर", "हेक्टेयर", "hectare", "ha"], "tonnes": ["टन", "टनों", "tonne", "tonnes", "ton", "tons", "mt"], "kg/ha": ["किलोग्राम", "कि0", "kg", "kgs"],
    "quintal/ha": ["क्विंटल", "quintal", "quintals", "qtl", "qtls"], "quintal": ["क्विंटल", "quintal", "quintals", "qtl", "qtls"],
    "Rs/quintal": ["रुपये", "रूपये", "रु0", "rs", "rs."], "Rs": ["रुपये", "रूपये", "रुपयों", "रु0", "rs", "rs.", "₹"], "mm": ["मि0", "मिलीमीटर", "mm", "millimetre", "millimetres"],
    "cm": ["सै0", "सेंटीमीटर", "cm"], "km": ["किलोमीटर", "कि0", "km", "kms"], "sq km": ["वर्ग", "sq", "sq.", "sq.km", "sq.km.", "sq.kms."], "per sq km": ["वर्ग", "sq", "sq.", "sq.km", "sq.km.", "sq.kms."], "%": ["प्रतिशत", "प्रतिशतता", "percent", "percentage", "%"],
    "number": ["संख्या", "number", "numbers", "no.", "nos."], "bales": ["गांठ", "गांठे", "गांठें", "bale", "bales"], "litres": ["लीटर", "litre", "litres"], "kg": ["किलोग्राम", "kg", "kgs"],
}
MULT_WORDS = {"00": ["00", "hundred", "सौ"], "000": ["000", "'000", "हजार", "हज़ार", "thousand"], "lakh": ["लाख", "lakh", "lakhs", "lac"]}
TOTAL_WORDS = {"जोड", "जोड़", "योग", "कुल", "total", "grand total", "all", "haryana", "state", "हरियाणा"}
ROW_KIND_WORDS = (("district", ("जिला", "जिले", "district")), ("tehsil", ("तहसील", "खण्ड", "खंड", "मण्डल", "मंडल", "उपमण्डल", "tehsil", "block", "sub-division", "subdivision", "division", "town", "नगर")),
                  ("crop", ("फसल", "फसलें", "crop", "crops", "commodity")), ("item", ("मद", "विवरण", "item", "items", "particulars", "description")),
                  ("year", ("वर्ष", "year")))
ROW_KINDS = ["year", "district", "tehsil", "crop", "item"]

_HI = "\u0900-\u097f"
def tidy_hindi(text):
    """Undo the small, regular glitches the legacy-font decoder leaves in a word, for MATCHING only (stored text is not changed):
    a space before a vowel sign ('टना ें' -> 'टनों'), anusvara placed before the vowel sign ('गेहंू' -> 'गेहूं') or after a halant ('सख्ंया' -> 'संख्या')."""
    t = (text or "").replace("\u093c", "").replace("\u0901", "\u0902")
    t = re.sub("\\s+(?=[\u093e-\u094d\u0902])", "", t)
    t = t.replace("\u093e\u0947", "\u094b").replace("\u093e\u0948", "\u094c")              # aa + e -> o ,  aa + ai -> au
    t = re.sub("(\u0902)([\u093e-\u094c])", r"\2\1", t)                                    # anusvara goes after the vowel sign
    t = re.sub("([\u0915-\u0939])\u094d\u0902", "\u0902\\1\u094d", t)                    # consonant + halant + anusvara -> anusvara + consonant + halant
    t = re.sub("\u094d\\s+(?=[\u0915-\u0939])", "\u094d", t)                                       # a halant is never followed by a space inside a word
    return t
def _toks(text):
    """Lower-cased word tokens; Devanagari words keep their vowel signs; nukta / chandrabindu spelling variants are folded."""
    t = tidy_hindi(text).lower().replace("@", " / ")
    return re.findall(rf"[{_HI}]+|[a-z]+\.?|\d+(?:\.\d+)?|['%/₹&]", t)
def _phrase_index(aliases):
    idx = []
    for name, al in aliases.items():
        for a in al: idx.append((tuple(_toks(a)), name))
    return sorted(idx, key=lambda x: -len(x[0]))
_CROPS = _phrase_index(CROP_ALIASES); _MEAS = [(tuple(_toks(a)), m) for m, al in MEASURE_ALIASES.items() for a in al]
_CROP_NAMES = {n.lower(): n for n in CROP_ALIASES}
def _find(tokens, index):
    """First entry of `index` (already in priority order) that occurs as a whole-token phrase."""
    for ph, name in index:
        n = len(ph)
        if n and any(tuple(tokens[i:i + n]) == ph for i in range(len(tokens) - n + 1)): return name
    return None
def crop_of(text): return _find(_toks(text), _CROPS)
def measure_of(text): return _find(_toks(text), _MEAS)
def canon_crop(name):
    """A crop name given by the LLM -> our crop list, or None if it is not a crop we know (never stored as free text)."""
    if not isinstance(name, str) or not name.strip(): return None
    return _CROP_NAMES.get(name.strip().lower()) or crop_of(name)

_BASES = [("kg/ha", r"(?:किलोग्राम|कि0 ग्रा0|kgs?\.?) (?:/|प्रति|per) (?:हैक्टेयर|हेक्टेयर|hectares?|ha)"),
          ("quintal/ha", r"(?:क्विंटल|quintals?|qtls?\.?) (?:/|प्रति|per) (?:हैक्टेयर|हेक्टेयर|hectares?|ha)"),
          ("Rs/quintal", r"(?:रुपये|रूपये|रु0|rs\.?) (?:/|प्रति|per) (?:क्विंटल|quintals?|qtls?\.?)"),
          (None, r"(?:प्रति|per) (?:हैक्टेयर|हेक्टेयर|hectares?|ha)"),          # 'per hectare' on its own names a rate, it is not a unit
          ("per sq km", r"(?:प्रति|per) (?:वर्ग कि0 मी0|वर्ग किलोमीटर|sq\.? ?kms?\.?)"), ("sq km", r"वर्ग कि0 मी0|वर्ग किलोमीटर|sq\.? ?kms?\.?"), ("ha", r"हैक्टेयर|हेक्टेयर|hectares?|ha"), ("tonnes", r"मीट्रिक टन|टनों|टन|tonnes?|tons?|m\.?t\.?"),
          ("quintal", r"क्विंटल|quintals?|qtls?\.?"), ("bales", r"गांठें|गांठे|गांठ|bales?"), ("mm", r"मि0 मी0|मिलीमीटर|millimetres?|mm\.?"), ("cm", r"सै0 मी0|सेंटीमीटर|cm\.?"),
          ("km", r"किलोमीटर|कि0 मी0|kms?\.?"), ("litres", r"लीटर|litres?"), ("%", r"प्रतिशतता|प्रतिशत|percentage|percent|%"), ("Rs", r"रुपये|रूपये|रुपयों|रु0|rs\.?|₹"),
          ("number", r"संख्या|numbers?|nos?\.")]
_MULT = r"(?:(?P<m>000|00|'000|हजार|लाख|thousand|hundred|lakhs?) )?"
_UNIT_RE = [(u, re.compile(rf"(?<!\S){_MULT}(?:{p})(?!\S)")) for u, p in _BASES]
def _flat(text):
    t = tidy_hindi(text).lower().replace("@", " / ")
    t = re.sub(r"[\]\[(),;:|>]", " ", t).replace("/", " / ")
    return re.sub(r"\s+", " ", t).strip()
def find_units(text):
    """All unit phrases printed in the text, in reading order: [(position, unit)]. '00 हैक्टेयर' -> '00 ha', 'हजार टन' -> '000 tonnes'."""
    t = _flat(text); out = []; taken = []
    for unit, rx in _UNIT_RE:
        for m in rx.finditer(t):
            if any(a < m.end() and m.start() < b for a, b in taken): continue        # a longer phrase already used these words (kg/ha contains ha)
            taken.append((m.start(), m.end()))
            if unit is None: continue
            mult = {"000": "000", "'000": "000", "हजार": "000", "thousand": "000", "00": "00", "hundred": "00", "लाख": "lakh", "lakh": "lakh", "lakhs": "lakh"}.get(m.group("m") or "")
            u = f"{mult} {unit}" if mult and unit not in ("%", "number", "kg/ha", "quintal/ha", "Rs/quintal", "per sq km") else unit
            out.append((m.start(), u))
    return sorted(out)
def _measure_positions(flat):
    out = []
    for ph, name in _MEAS:
        if name in ("percent", "count"): continue
        for m in re.finditer(r"(?<!\S)" + re.escape(" ".join(ph)) + r"(?!\S)", flat): out.append((m.start(), name))
    return sorted(out)
def unit_note(title):
    """Units stated in a table title. 'क्षेत्रफल 00 हैक्टेयर में, उत्पादन 00 टनों में तथा औसत उपज किलोग्राम/हैक्टेयर में' ->
    {'area': '00 ha', 'production': '00 tonnes', 'yield': 'kg/ha'}. Each unit belongs to the measure word printed just before it.
    A title with a single unit also gives {'*': unit}: it applies to every value column."""
    out = {}; flat = _flat(title); meas = _measure_positions(flat); prev = -1
    real = [(p, u) for p, u in find_units(title) if u not in ("number", "%")]
    for p, u in real:
        before = [name for mp, name in meas if prev < mp < p]
        if before and before[-1] not in out: out[before[-1]] = u
        prev = p
    if len({u for _, u in real}) == 1: out["*"] = real[0][1]
    return out
def unit_grounded(unit, text):
    """True if the words for this unit (and its multiplier) are printed in `text`."""
    if unit not in ALLOWED_UNITS: return False
    parts = unit.split(" ", 1); mult, base = (parts[0], parts[1]) if len(parts) == 2 and parts[0] in MULT_WORDS else (None, unit)
    if unit in ("000", "lakh"): mult, base = unit, None
    tk = set(_toks(text)) | set(_flat(text).split())
    squashed = re.sub("[\\s\u094d\u0902]", "", tidy_hindi(text))               # tolerant form for damaged Hindi words: no spaces, halant or anusvara
    def printed(w): return w in tk or w + "s" in tk or (bool(re.match("[\u0900-\u097f]", w)) and len(w) >= 2 and re.sub("[\u094d\u0902]", "", w) in squashed)
    if base and not any(printed(w) for w in UNIT_WORDS.get(base, [])): return False
    return mult is None or any(w in tk for w in MULT_WORDS[mult])

# ------------------------------------------------------------------ cell tests
MISSING = {"", "-", "--", "---", "..", "...", ".", "@", "na", "n.a.", "n.a", "nil", "—", "–", "*"}
NUM = re.compile(r"^\(?[-+]?\d[\d,]*(?:\.\d+)?\)?[*a-zA-Z]{0,2}$")
NUM_LEGACY = re.compile(r"^[-+]?\d[\d,]*(?:[.\-]\d+)?\*?$")
YEARTOK = re.compile(r"^(?:19|20)\d{2}(?:\s*[-–/]\s*\d{2,4})?(?:\s*\([A-Za-z]\))?$")
YEARIN = re.compile(r"(?<!\d)((?:19|20)\d{2}(?:\s*[–-]\s*\d{2,4})?)(?!\d)")
def is_year(c): return bool(YEARTOK.match((c or "").strip()))
def is_missing(c): return (c or "").strip().lower() in MISSING
def is_num(c, legacy=False): return bool((NUM_LEGACY if legacy else NUM).match((c or "").strip()))     # '575 360' is two numbers, not one: not accepted
def norm_year(c):
    y = re.sub(r"\([A-Za-z]\)", "", c or "").strip(); y = re.sub(r"\s*[–/]\s*", "-", y); return re.sub(r"\s+", "", y)
def parse_number(c, legacy=False):
    t = (c or "").strip()
    if is_missing(t) or not is_num(t, legacy): return None
    t = t.strip("()").rstrip("*")
    t = re.sub(r"[a-zA-Z]+$", "", t)
    if legacy: t = re.sub(r"(?<=\d)-(?=\d)", ".", t)             # Kruti Dev prints the decimal point as '-'
    try: return float(t.replace(",", ""))
    except ValueError: return None

# ------------------------------------------------------------------ grid tables
def grid_tables(page, decode=None, allow_borderless=False):
    """Ruled tables on a pdfplumber page as lists of rows with merged cells filled in: a header cell that spans three
    columns is repeated in all three, so every column gets its full header path. Returns [{'rows': [[str]], 'top': y}]."""
    out = []
    found = page.find_tables(); borderless = False
    if not found and allow_borderless: return borderless_tables(page, decode)      # no ruled table: read aligned text lines instead
    for t in found:
        text = t.extract(); rows = t.rows
        if not text or not rows: continue
        ncol = max(len(r) for r in text); nrow = len(text)
        col_x0 = [None] * ncol; row_top = [None] * nrow
        for r, row in enumerate(rows):
            for c, cell in enumerate(row.cells[:ncol]):
                if cell:
                    col_x0[c] = cell[0] if col_x0[c] is None else min(col_x0[c], cell[0])
                    row_top[r] = cell[1] if row_top[r] is None else min(row_top[r], cell[1])
        filled = [[None] * ncol for _ in range(nrow)]
        for r, row in enumerate(rows):
            for c, cell in enumerate(row.cells[:ncol]):
                if not cell: continue
                val = text[r][c] if c < len(text[r]) else None
                val = "" if val is None else str(val)
                for rr in range(r, nrow):
                    if rr > r and (row_top[rr] is None or row_top[rr] >= cell[3] - 1.5): break
                    for cc in range(c, ncol):
                        if cc > c and (col_x0[cc] is None or col_x0[cc] >= cell[2] - 1.5): break
                        if filled[rr][cc] is None: filled[rr][cc] = val
        grid = [[(decode(v) if decode else v.replace("\n", " ").strip()) if v else "" for v in row] for row in filled]
        keep = [c for c in range(ncol) if any(row[c] for row in grid)]
        grid = [[row[c] for c in keep] for row in grid if any(row)]
        if grid and len(grid[0]) >= 2: out.append(dict(rows=grid, top=t.bbox[1], bbox=t.bbox, borderless=borderless))
    return sorted(out, key=lambda g: g["top"])

def borderless_tables(page, decode=None, min_rows=2):
    """Tables without ruling lines. A data line is a label followed by two or more numbers; consecutive data lines with the same
    number of values form a table. Header words are attached to the value column they stand above (by x position).
    Same output shape as grid_tables; rows are flagged borderless (stored with needs_review = 1)."""
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    lines = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0]["top"] - w["top"]) <= 3: lines[-1].append(w)
        else: lines.append([w])
    lines = [sorted(l, key=lambda w: w["x0"]) for l in lines]
    def val(t): return is_num(t) or is_missing(t) and t != ""
    def split(line):
        toks = [w["text"] for w in line]; k = len(toks)
        while k > 0 and val(toks[k - 1]) and not (is_year(toks[k - 1]) and k <= 2): k -= 1
        if all(_int(t) is not None and t.startswith("(") for t in toks): return None          # '(1) (2) (3)': the printed column numbers
        return (line[:k], line[k:]) if len(toks) - k >= 2 else None
    out, i = [], 0
    while i < len(lines):
        sp = split(lines[i])
        if not sp: i += 1; continue
        n = len(sp[1]); j = i; body = []
        while j < len(lines):
            s2 = split(lines[j])
            if not s2 or len(s2[1]) != n: break
            body.append(s2); j += 1
        if len(body) < min_rows: i += 1; continue
        centers = [sorted((w["x0"] + w["x1"]) / 2 for _, vals in body for w in [vals[c]])[len(body) // 2] for c in range(n)]
        left = min(w["x0"] for _, vals in body for w in vals[:1])
        gap = min([b - a for a, b in zip(centers, centers[1:])] or [60]) / 2
        head = []
        for hl in lines[max(0, i - 6):i]:
            if split(hl): head = []; continue                                                   # an earlier table's rows: start again below them
            if not hl: continue
            row = [""] * (n + 1)
            gaps = [b["x0"] - a["x1"] for a, b in zip(hl, hl[1:])]; h = max(w["bottom"] - w["top"] for w in hl) or 8
            if len(hl) >= 3 and sum(g < 0.7 * h for g in gaps) >= 0.75 * len(gaps) and not all(_int(w["text"]) is not None for w in hl):
                row[0] = " ".join(w["text"] for w in hl); head.append(("title", row)); continue       # running text (a title or a unit note), not column headings
            nums_line = all(_int(w["text"]) is not None and w["text"].startswith("(") for w in hl)
            for w in hl:
                cx = (w["x0"] + w["x1"]) / 2
                c = min(range(n), key=lambda c: abs(centers[c] - cx))
                idx = c + 1 if abs(centers[c] - cx) <= gap and w["x1"] > left - 2 else 0
                if nums_line and idx == 0 and row[0]: continue                                  # '(1) (2)' above the label column: keep the first number only
                row[idx] = (row[idx] + " " + w["text"]).strip()
            head.append(("head", row))
        titles = [r for k, r in head if k == "title"]
        grid = [[" ".join(r[0] for r in titles)] + [""] * n] * bool(titles) + [r for k, r in head if k == "head"] \
               + [[" ".join(w["text"] for w in lab)] + [w["text"] for w in vals] for lab, vals in body]
        if decode: grid = [[decode(c) if c else "" for c in r] for r in grid]
        tops = [w["top"] for l in lines[max(0, i - len(head)):j] for w in l]
        out.append(dict(rows=grid, top=min(tops), bbox=(0, min(tops), page.width, max(tops) + 12), borderless=True))
        i = j
    return out

def _distinct(row): return list(dict.fromkeys(c for c in row if c))
def _int(c):
    m = re.fullmatch(r"\(?(\d{1,3})\)?", (c or "").strip()); return int(m.group(1)) if m else None
def _is_colnum_row(row):
    vals = [_int(c) for c in row if c]
    if len(vals) < 2 or any(v is None for v in vals) or len(vals) < max(2, len(row) // 2): return False
    return vals[0] == 1 or all(b > a for a, b in zip(vals, vals[1:]))
def _is_data_row(row, legacy):
    rest = [c for c in row[1:] if c]
    nums = sum(is_num(c, legacy) and not is_year(c) for c in rest); miss = sum(is_missing(c) for c in row[1:])
    return bool(row[0]) and nums >= 1 and (nums + miss) >= 0.6 * max(len(row) - 1, 1)

def structure(grid, legacy=False, carried=None):
    """Split a filled grid into title text, header rows, the printed column-number row and body rows.
    `carried` is the header of the previous table (a table continued on the next page prints no header)."""
    rows = grid["rows"]; ncol = len(rows[0])
    k = next((i for i, r in enumerate(rows[:12]) if _is_colnum_row(r)), None)
    first = k + 1 if k is not None else next((i for i, r in enumerate(rows) if _is_data_row(r, legacy)), len(rows))
    top = rows[:k if k is not None else first]
    title, header = [], []
    for r in top:
        d = _distinct(r)
        if not d: continue
        if len(d) == 1 and re.fullmatch(r"\D{2,45}\s\d{1,3}|\d{1,3}\s\D{2,45}", d[0]): continue      # running page header: chapter name + page number
        if len(d) == 1 and (len(r) > 2 or len(d[0]) > 25): title.append(d[0])
        elif re.match(r"^(सारणी|Table|TABLE)", d[0]): title.extend(d)
        else: header.append(r)
    colnums = [_int(c) for c in rows[k]] if k is not None else None
    if not header and carried and len(carried["header"]) and len(carried["header"][0]) == ncol:
        header = carried["header"]; title = title or carried["title"]
    labels = [0]
    if colnums:                                                    # side-by-side sub-tables restart their numbering at the first number
        labels += [c for c in range(1, ncol) if colnums[c] == colnums[0] and colnums[c] is not None and (c == 0 or colnums[c - 1] != colnums[c])]
    return dict(title=title, header=header, colnums=colnums, body=rows[first:], body_start=first, labels=labels, ncol=ncol)

def header_path(header, c):
    parts = []
    for r in header:
        v = re.sub(r"\s+", " ", r[c]).strip() if c < len(r) else ""
        if v and v not in parts: parts.append(v)
    return " > ".join(parts)

def columns_of(st):
    """Value columns with their header path and printed number: [{'c': printed number or position, 'i': grid index, 'header': path, 'block': label column}]."""
    cols = []
    for bi, lab in enumerate(st["labels"]):
        end = st["labels"][bi + 1] if bi + 1 < len(st["labels"]) else st["ncol"]
        for i in range(lab + 1, end):
            num = st["colnums"][i] if st["colnums"] and st["colnums"][i] is not None else i + 1
            cols.append(dict(c=num, i=i, header=header_path(st["header"], i), block=lab))
    return cols

# ------------------------------------------------------------------ deciders
def row_kind_rules(label_header, labels, legacy=False):
    """What the row labels are, from the label-column header and the labels themselves. None = rules cannot tell."""
    labs = [l for l in labels if l]
    tk = set(_toks(label_header))
    non_year = [l for l in labs if not is_year(l)]
    if labs and not non_year: return "year"
    if non_year and sum(canonical(re.sub(r"^\d+\.\s*", "", l)) is not None for l in non_year) >= 0.5 * len(non_year): return "district"
    for kind, words in ROW_KIND_WORDS:
        if kind != "year" and tk & set(words): return kind
    if non_year and sum(crop_of(l) is not None for l in non_year) >= 0.5 * len(non_year): return "crop"
    if non_year and sum(bool(re.match(r"^\d+\.\s?[A-Z]", l)) for l in non_year) >= 0.5 * len(non_year): return "tehsil"     # '1.Bhiwani', '2.Bawani Khera'
    return None

def map_rules(title, cols):
    """Rule decision per column: crop, measure, unit, year (for year-wise columns). Unknown stays None / 'unspecified'."""
    note = unit_note(title); t_tk = set(_toks(title)); out = []
    t_meas = {m for ph, m in _MEAS if m not in ("percent", "count") and _find(_toks(title), [(ph, m)])}
    title_meas = next(iter(t_meas)) if len(t_meas) == 1 else None
    title_crop = crop_of(title) if sum(1 for ph, _ in _CROPS if _find(_toks(title), [(ph, 1)])) == 1 else None
    for col in cols:
        h = col["header"]; parts = [x for x in h.split(" > ") if x][::-1]                 # deepest header level first
        crop = next((c for c in map(crop_of, parts) if c), None) or crop_of("".join(parts[::-1][-2:])) or title_crop       # 'Ground' over 'nut' = Groundnut
        if crop == "Cotton":
            tk = set(_toks(h))
            crop = "Cotton (Desi)" if tk & {"देसी", "देशी", "desi"} else "Cotton (American)" if tk & {"अमेरिकन", "अमरीकन", "american"} else crop
        meas = next((m for m in map(measure_of, parts) if m), None)
        if meas is None and crop and title_meas: meas = title_meas                    # 'Farm harvest prices of principal crops': every crop column is a price
        hu = [u for _, u in find_units(h)]
        unit = None
        real = [u for u in hu if u not in ("number", "%")]
        if real: unit = real[0]
        elif meas == "percent" or "%" in hu: unit = "%"
        elif meas and meas in note: unit = note[meas]
        elif "*" in note and meas != "count" and (meas is None or len(note) <= 2): unit = note["*"]
        elif meas == "count" or (not note and not meas and ("संख्या" in t_tk or "number" in t_tk)): unit = "number"
        ym = YEARTOK.match(h.split(" > ")[-1].strip()) if h else None
        out.append(dict(c=col["c"], header=h, crop=crop, measure=meas, unit=unit or "unspecified", year=norm_year(h.split(" > ")[-1]) if ym else None, by={}))
    split_merged_crops(out)
    n_crop = sum(m["crop"] is not None for m in out)
    if title_meas and (n_crop >= 0.5 * len(out) or (n_crop and t_tk & CROP_HINTS)):   # a crop-wise table: an unknown short heading is a crop we do not have in the list
        for m in out:
            words = m["header"].replace(" > ", " ").split()
            clean = re.fullmatch(r"[A-Za-z][a-z]+(?:[ -][A-Za-z][a-z]+){0,2}", " ".join(words)) or re.fullmatch(rf"[{_HI} ]+", " ".join(words))
            if m["crop"] is None and 1 <= len(words) <= 3 and clean and not measure_of(m["header"]):
                m["crop"] = " ".join(words); m["measure"] = m["measure"] or title_meas; m["by"]["crop"] = "as printed"
    return out

SYSTEM_PROMPT = ("You label the columns of statistical tables from Haryana (India) agriculture reports. The text is Hindi or English and may "
                 "contain small spelling errors. For each column give: \"crop\" (English crop name, or null if the column is not about one crop), "
                 "\"measure\" (one of: " + ", ".join(MEASURES) + "), \"unit\" (one of the allowed units, or null if the title and header do not state it). "
                 "Also give \"row_label\": what the first column of the table contains (one of: " + ", ".join(ROW_KINDS) + "). "
                 "Use only what the title and headers say. Never output table values. Answer with one JSON object only, in this form: "
                 "{\"row_label\": \"...\", \"columns\": [{\"c\": 2, \"crop\": null, \"measure\": \"...\", \"unit\": null}]}")
def mapping_prompt(title, cols, label_header="", sample_labels=()):
    """The user message for the column-labelling task (the same text is used to build the fine-tuning data)."""
    return json.dumps({"title": (title or "")[:400], "first_column_header": (label_header or "")[:80], "first_column_examples": [s[:30] for s in list(sample_labels)[:4]],
                       "allowed_units": ALLOWED_UNITS, "columns": [{"c": c["c"], "header": c["header"][:120]} for c in cols]}, ensure_ascii=False)

def validate_mapping(obj, cols, evidence):
    """Keep only the parts of an LLM answer that pass the checks. Returns ({c: {crop, measure, unit}}, row_label or None, problems)."""
    ok, problems = {}, []
    if not isinstance(obj, dict) or not isinstance(obj.get("columns"), list): return {}, None, ["no 'columns' list"]
    valid_c = {c["c"] for c in cols}
    for e in obj["columns"]:
        if not isinstance(e, dict) or e.get("c") not in valid_c: problems.append(f"unknown column {e.get('c') if isinstance(e, dict) else e!r}"); continue
        d = {}
        if e.get("crop") is not None:
            cc = canon_crop(e["crop"])
            if cc: d["crop"] = cc
            else: problems.append(f"col {e['c']}: crop {e['crop']!r} not in the crop list")
        if e.get("measure") in MEASURES and e["measure"] != "other": d["measure"] = e["measure"]
        elif e.get("measure") not in (None, "other"): problems.append(f"col {e['c']}: measure {e.get('measure')!r} not allowed")
        if e.get("unit") is not None:
            if unit_grounded(e["unit"], evidence + " " + next(c["header"] for c in cols if c["c"] == e["c"])): d["unit"] = e["unit"]
            else: problems.append(f"col {e['c']}: unit {e['unit']!r} is not printed in the title or header")
        ok[e["c"]] = d
    rk = obj.get("row_label") if obj.get("row_label") in ROW_KINDS else None
    return ok, rk, problems

def policy(): return (os.environ.get("AGRI_LLM_POLICY") or "fill").lower()

CROP_HINTS = {"फसल", "फसलों", "फसलें", "कृषि", "crop", "crops", "agriculture", "agricultural", "harvest", "kharif", "rabi", "खरीफ", "रबी"}
def llm_could_help(title, cols, mapping, row_kind):
    """Ask the LLM only where a checked answer could be used: a unit word is printed but the rules could not read it, a crop table has
    headings the rules do not know, or the kind of row label is unknown. A table of counts with no unit printed is not sent (nothing could be accepted)."""
    ev = title + " " + " ".join(c["header"] for c in cols)
    unit_gap = any(m["unit"] == "unspecified" for m in mapping) and any(unit_grounded(u, ev) for u in ALLOWED_UNITS if u not in ("number", "%", "000", "lakh"))
    cropish = any(m["crop"] for m in mapping) or bool(set(_toks(ev)) & CROP_HINTS)
    crop_gap = cropish and any((m["crop"] is None or m["by"].get("crop") == "as printed") and m["header"] for m in mapping)
    meas_gap = cropish and any(m["measure"] is None and m["header"] for m in mapping)
    return unit_gap or crop_gap or meas_gap or row_kind is None

def map_columns(title, cols, label_header="", sample_labels=(), row_kind=None, doc=""):
    """Rules first, then the LLM for the gaps. Returns (mapping list, row_kind, info). Each mapping has 'by': which fields the LLM supplied."""
    mapping = map_rules(title, cols); info = dict(llm_asked=False, llm_filled=0, problems=[], agree=0, differ=0)
    gaps = llm_could_help(title, cols, mapping, row_kind)
    pol = policy()
    if not llm.enabled() or not cols or not (gaps or pol in ("always", "prefer")): return mapping, row_kind, info
    obj = llm.ask_json("map_columns", SYSTEM_PROMPT, mapping_prompt(title, cols, label_header, sample_labels), doc=doc)
    info["llm_asked"] = True
    if obj is None: llm.note(False); return mapping, row_kind, info
    got, rk, problems = validate_mapping(obj, cols, title); info["problems"] = problems
    for m in mapping:
        g = got.get(m["c"], {})
        for f in ("crop", "measure", "unit"):
            if f not in g: continue
            empty = m[f] in (None, "unspecified") or (f == "crop" and m["by"].get("crop") == "as printed")     # a heading kept as printed may be replaced by a known crop
            if not empty: info["agree" if m[f] == g[f] else "differ"] += 1
            if empty or (pol == "prefer" and m[f] != g[f]):
                m[f] = g[f]; m["by"][f] = "llm"; info["llm_filled"] += 1
    if row_kind is None and rk: row_kind = rk; info["row_kind_by"] = "llm"
    llm.note(bool(got) or rk is not None)
    llm.STATS["labels_filled"] += info["llm_filled"]; llm.STATS["labels_agree"] += info["agree"]; llm.STATS["labels_differ"] += info["differ"]
    return mapping, row_kind, info

TABLE_NO = re.compile(r"(?:सारणी|Table|TABLE)\D{0,12}(\d+)\s*[-.]\s*(\d+)")
def with_carried_note(title, notes):
    """A table continued on later pages prints its unit note only once. `notes` remembers it by table number ('9-2')."""
    m = TABLE_NO.search(title or ""); key = f"{m.group(1)}-{m.group(2)}" if m else None
    if key is None or notes is None: return title
    stated = [g for g in re.findall(r"\([^()]*\)?", title) if any(u not in ("number", "%") for _, u in find_units(g))]
    if stated: notes[key] = " ".join(stated); return title
    return f"{title} {notes[key]}" if key in notes else title

def crops_in_order(text):
    """Crop names in the order they are printed: 'चावल मक्की' -> ['Rice', 'Maize']."""
    tk = _toks(text); out = []; i = 0
    while i < len(tk):
        hit = next(((name, len(ph)) for ph, name in _CROPS if ph and tuple(tk[i:i + len(ph)]) == ph), None)     # longest alias starting at word i
        if hit: out.append(hit[0]); i += hit[1]
        else: i += 1
    return out

def split_merged_crops(out):
    """One header cell drawn over two crops ('चावल  मक्की' spanning six columns): give each crop an equal share of the columns, in printed order.
    Only applied when the span divides evenly; otherwise the columns keep the first crop and a later check flags them."""
    i = 0
    while i < len(out):
        top = out[i]["header"].split(" > ")[0] if out[i]["header"] else ""
        j = i
        while j + 1 < len(out) and out[j + 1]["header"].split(" > ")[0] == top: j += 1
        crops = crops_in_order(top) if top else []
        n = j - i + 1
        if len(crops) >= 2 and n % len(crops) == 0:
            w = n // len(crops)
            for k in range(n): out[i + k]["crop"] = crops[k // w]; out[i + k]["by"]["crop_split"] = "rules"
        i = j + 1

HYV = re.compile(r"\bHYV\b|अधिक उपजाऊ", re.I)
def crop_indicator(m, title=""):
    """Indicator for a crop column: area / production / yield / price, with 'area under HYV' kept apart from total area."""
    h = m.get("header") or ""
    if m["measure"] == "area" and re.search(r"\bHYV\b", h, re.I): return "area under HYV"
    if m["measure"] == "percent" and (HYV.search(h) or HYV.search(title or "")): return "percent of area under HYV"
    if m["measure"] in ("area", "production", "yield"): return m["measure"]
    if m["measure"] == "price": return "farm_harvest_price"
    return None

def indicator_of(m, title_short):
    """Stored indicator: the measure for crop columns (area / production / yield, as in the Excel data); otherwise table title + header path."""
    if m["crop"] and crop_indicator(m, title_short): return crop_indicator(m, title_short)
    h = m["header"] or f"col {m['c']}"
    return f"{title_short[:120]} | {h}" if title_short else h

def short_title(title_lines):
    t = " ".join(title_lines) if isinstance(title_lines, (list, tuple)) else (title_lines or "")
    return re.sub(r"\s+", " ", t).strip()[:400]

# ------------------------------------------------------------------ generic extraction (any ruled table)
def extract_page(page, pno, ctx, legacy=False, decode=None, above_text=True, allow_borderless=True, require_header=False):
    """Every ruled table on the page -> observation records. ctx: dict(district, year, carried, doc, info list).
    Row labels: a year sets the year; a district name sets the district; other labels become the sub-district (tehsil) or part of the indicator."""
    recs, rejected = [], []
    for tno, grid in enumerate(grid_tables(page, decode, allow_borderless=allow_borderless), 1):
        st = structure(grid, legacy, ctx.get("carried"))
        if not st["body"] or (require_header and not st["header"]): continue
        if st["header"]: ctx["carried"] = dict(header=st["header"], title=st["title"])
        title = list(st["title"])
        if above_text and not title:
            try:
                x0, top, x1, _ = grid["bbox"]
                txt = page.crop((0, max(0, top - 70), page.width, top)).extract_text() or ""
                lines = [decode(l) if decode else l for l in txt.split("\n") if l.strip()]
                title = [l for l in lines[-3:] if len(l) > 8]
            except Exception: pass
        tshort = short_title(title); cols = columns_of(st)
        if not cols: continue
        tfull = with_carried_note(tshort, ctx.setdefault("notes", {}))
        label_header = header_path(st["header"], 0)
        labels = [r[0] for r in st["body"]]
        rk = row_kind_rules(label_header, labels, legacy)
        mapping, rk, info = map_columns(tfull, cols, label_header, [l for l in labels if l and not is_year(l)][:4], rk, ctx.get("doc", ""))
        ctx.setdefault("info", []).append(dict(page=pno, table=tno, **{k: info[k] for k in ("llm_asked", "llm_filled", "agree", "differ")}, problems=info["problems"][:3]))
        by_i = {c["i"]: (c, m) for c, m in zip(cols, mapping)}
        ty = YEARIN.search(tshort); title_year = norm_year(ty.group(1)) if ty else None
        cur_year = {lab: None for lab in st["labels"]}; carry_name = {}
        for ridx, row in enumerate(st["body"], st["body_start"] + 1):
            for bi, lab in enumerate(st["labels"]):
                end = st["labels"][bi + 1] if bi + 1 < len(st["labels"]) else st["ncol"]
                label = re.sub(r"\s+", " ", row[lab]).strip(); cells = row[lab + 1:end]
                if not label and not any(cells): continue
                if not any(c for c in cells):                                   # label-only row: a year line sets the year for the rows below it
                    if is_year(label): cur_year[lab] = norm_year(label)
                    continue
                if not all(is_num(c, legacy) or is_missing(c) for c in cells):
                    if any(is_num(c, legacy) for c in cells): rejected.append((pno, f"table {tno} row {ridx}: " + " ".join([label] + cells)[:70]))
                    continue
                district, tehsil, crop_row, item, year = ctx.get("district"), None, None, None, None
                name = re.sub(r"^\d+\.\s*", "", label)
                ym = re.search(r"(?:^|\s)((?:19|20)\d{2}\s*[-–/]\s*\d{2,4}|(?:19|20)\d{2})$", name)
                if ym and name[:ym.start()].strip():                         # 'AMBALA 1998-99': a place and a year in one label
                    year = norm_year(ym.group(1)); name = name[:ym.start()].strip(); carry_name[lab] = name; label = name
                elif is_year(label) and carry_name.get(lab):                 # year-only line under a named row: same place, next year
                    year = norm_year(label); name = label = carry_name[lab]
                if year is None and is_year(label): year = norm_year(label)
                elif name.lower() in TOTAL_WORDS: tehsil = "Total" if rk in ("tehsil", None) else None; item = None if rk in ("tehsil", "district", None) else name
                elif canonical(name) and rk in ("district", None): district = name
                elif rk == "district": district = name
                elif rk == "tehsil" or (rk is None and re.match(r"^\d+\.\s?[A-Z]", label)): tehsil = name
                elif rk == "crop": crop_row = crop_of(name) or name
                else: item = name
                if rk == "district" and (name.lower() in TOTAL_WORDS or re.search(r"average|total|all districts", name, re.I)): continue   # state totals / averages are derived, not district observations
                if rk == "district" and (is_year(name) or not name):               # a year line whose district is on the previous page: not guessed
                    rejected.append((pno, f"table {tno} row {ridx}: district not on this page: " + " ".join([label] + cells)[:50])); continue
                for off, cell in enumerate(cells):
                    i = lab + 1 + off
                    if i not in by_i or not cell: continue                    # an empty cell is not an observation (a printed '-' or '..' is)
                    col, m = by_i[i]
                    ind = indicator_of(m, tshort)
                    if item: ind = f"{ind} | {item}"
                    filled_by_llm = any(v == "llm" for v in m["by"].values()); as_printed = "crop" in m["by"] and not filled_by_llm
                    recs.append(dict(district_raw=district, tehsil_block=tehsil, crop=crop_row or m["crop"], crop_na=not (crop_row or m["crop"]) and bool(m["header"]),
                                     season=None, indicator=ind, unit=m["unit"],
                                     year=year or m["year"] or cur_year[lab] or title_year or ctx.get("year"), value_num=parse_number(cell, legacy), value_text=cell,
                                     page_no=pno, row_ref=f"row{ridx}", table_ref=f"p{pno}t{tno}c{col['c']}",
                                     confidence=0.8 if filled_by_llm else (0.7 if grid["borderless"] else 1.0),
                                     needs_review=int(filled_by_llm or as_printed or m["unit"] == "unspecified" or grid["borderless"])))
    return recs, rejected

def numbered_column_maps(page, pno, title_hint="", legacy=True, decode=None, doc="", notes=None):
    """For the Hindi agent: one {printed column number: mapping} per numbered table on the page, top to bottom.
    The agent reads rows line by line (exact digits) and looks up what each printed column number means here."""
    maps = []
    for grid in grid_tables(page, decode):
        st = structure(grid, legacy)
        if not st["colnums"] or len(st["labels"]) > 1: maps.append(None); continue          # side-by-side sub-tables repeat their numbers: not mapped
        cols = columns_of(st)
        if not cols or not st["header"]: maps.append(None); continue
        title = with_carried_note(short_title(st["title"]) or title_hint, notes)
        mapping, _, info = map_columns(title, cols, header_path(st["header"], 0), (), "x", doc)
        maps.append(dict(title=title, by_num={m["c"]: m for m in mapping}, info=info, nums=[c for c in st["colnums"] if c is not None]))
    return maps
