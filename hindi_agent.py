"""hindi_ocr agent: Hindi PDF pages -> district rows -> SQLite via the shared loader. Fixed code, no LLM.
Three page kinds, chosen per page by code (never by a model):
  legacy   text layer exists but is Kruti Dev (Latin-looking letters)  -> decode with kruti_dev.convert_line (exact digits)
  scanned  no text layer (page is an image)                            -> OCR backend chain: paddle -> tesseract(hin) -> easyocr
  latin    ordinary English page inside the same PDF                   -> skipped and logged (digital_pdf handles English tables)
Two row layouts on the decoded lines:
  summary  'item  unit  district  state'   (header line 'item unit district Haryana-state')   -> 2 observations per row
  numbered year / tehsil rows under the PDF's own numbered column row                          -> 1 observation per number
Decisions in code: wrong number of values -> row rejected and counted (never guessed); '--' '..' -> missing;
this font writes the decimal point as '-' ('1634-4' = 1634.4) -> decoded only in value cells, raw text always kept;
OCR rows are stored with confidence 0.6 and needs_review=1."""
import re
import pdfplumber
from structured_agent import StructuredDataAgent, MISSING_TOKENS
from district_names import canonical
from kruti_dev import convert_line, looks_legacy

# ---------------------------------------------------------------- vocabularies (decoded Unicode forms)
UNITS = {   # phrase (decoded) -> canonical unit; the longest matching suffix of the label wins
    "हजार हैक्टेयर": "000 ha", "000 हैक्टेयर": "000 ha", "हजार हेक्टेयर": "000 ha", "हैक्टेयर": "ha", "हेक्टेयर": "ha",
    "हजार टन": "000 tonnes", "कि0 ग्रा0 प्रति हैक्टेयर": "kg/ha", "संख्या": "number", "प्रतिशत": "%", "किलोमीटर": "km",
    "हजार": "000", "लाख": "lakh", "सै0 मी0": "cm", "वर्ग कि0 मी0": "sq km", "प्रति वर्ग कि0 मी0": "per sq km",
    "हजार गांठे": "000 bales", "लाख किलोवाट": "lakh kilowatt (as printed)", "हजार लीटर": "000 litres", "क्विंटल": "quintal",
}
UNKNOWN_UNIT = "unspecified"
IND_HI = (("क्षेत्र", "area"), ("उत्पादन", "production"), ("उपज", "yield"))
CROPS = [("चावल", "Rice"), ("गेंहू", "Wheat"), ("गेहूं", "Wheat"), ("गेहू", "Wheat"), ("जो", "Barley"), ("गन्ना", "Sugarcane"),
         ("कपास", "Cotton"), ("बाजरा", "Bajra"), ("ज्वार", "Jowar"), ("मक्का", "Maize"), ("चना", "Gram"),
         ("तांरिया", "Toria/Taramira/Mustard"), ("सरसों", "Rape/Mustard")]
VAL = re.compile(r"^-?\d[\d,]*(?:[.\-]\d+)?\*?$|^\.{2,}$|^-{1,}$")
YEARTOK = re.compile(r"^(?:19|20)\d{2}(?:[-–]\d{2,4})?(?:\([A-Z]\))?$")
YEARIN = re.compile(r"(?<!\d)((?:19|20)\d{2}(?:\s*[–-]\s*\d{2,4})?)(?!\d)")
SECTION = re.compile(r"^\s*(\d+)\s*-\s*(\S.*)$")
OCR_CONF = 0.6
LEGACY_MARKERS = frozenset(["ftyk", "la[;k", "rglhy", "o\"kZ", "izfr", "{ks=", "tksM", "rFkk", "lkj.kh", "mRiknu", "{ks=Qy", "fdyksehVj", "gtkj",
                            "dqy", "esa", "vkSj", "gSDVs;j", "ljdkjh", "xkao", "mit", "tula[;k", "o\"k@Z", "mi&e.My", "izkfIr", "LFkku"])
LEGACY_WORDS = frozenset(["izfr", "oxZ", "ds", "dh", "dk", "dks", "ls", "gS", "gSa", "gSA", "esa", "rFkk", "vkSj", "fd0", "eh0", "xzk0", "gtkj", "dqy", "ds;j", "ek=k"])
def classify_page(page, text):
    """'scanned' (no text layer) / 'legacy' (Kruti Dev) / 'latin' (English). Legacy words are recognised by punctuation inside
    words OR by common legacy function words, so number-heavy Hindi tables are not mistaken for English."""
    return "scanned" if len(page.chars) < 20 else "text"
def _english_like(tok):
    w = re.sub(r"[^A-Za-z]", "", tok)
    return len(w) >= 3 and w == re.sub(r"^[^A-Za-z]+|[^A-Za-z]+$", "", tok).replace("-", "") and not re.search(r"[A-Z][a-z]*[A-Z]", w[1:]) \
        and len(re.findall(r"[aeiouAEIOU]", w)) / len(w) >= 0.25
def line_is_english(line):
    """A line is English if it is a table title, an 'N.Name' row label, or most of its words look English (no inner capitals or
    punctuation, normal vowel share). Legacy Hindi words fail this test (e.g. 'ftyk', 'tksM', 'la[;k')."""
    if re.search(r"[;\[\]{}<>?\"@^`~]|[A-Za-z]0(?:\s|$)", line) or LEGACY_WORDS & set(line.split()): return False   # legacy-only symbols / 'fd0' style abbreviations
    if re.match(r"^(Table No|Source|Note)\b", line) or re.match(r"^\d+\.\s?[A-Z][a-z]", line): return True
    toks = [t for t in re.split(r"[\s./&]+", line) if re.search(r"[A-Za-z]", t) and not re.fullmatch(r"[\d.,\-()/%*]+[A-Za-z]?", t)]
    toks = [t for t in toks if len(re.sub(r"[^A-Za-z]", "", t)) >= 3]
    return len(toks) >= 2 and sum(_english_like(t) for t in toks) / len(toks) >= 0.7
def english_vocab(texts, kinds):
    """Capitalised English names seen on English pages (tehsil names etc.), so they are not decoded as Hindi."""
    out = set()
    for t, k in zip(texts, kinds):
        if k != "latin": continue
        for w in re.findall(r"\b[A-Z][A-Za-z]{3,}\b", t):
            if len(re.findall(r"[aeiouAEIOU]", w)) >= 2 and not re.search(r"[A-Z][a-z]*[A-Z]", w[1:]): out.add(w.lower())
    return out

class OCRUnavailable(RuntimeError): pass

HI_DISTRICTS = {"अंबाला": "Ambala", "अम्बाला": "Ambala", "भिवानी": "Bhiwani", "चरखीदादरी": "Charkhi Dadri", "फरीदाबाद": "Faridabad", "फतेहाबाद": "Fatehabad",
    "गुरुग्राम": "Gurugram", "गुडगांव": "Gurugram", "गुड़गांव": "Gurugram", "हिसार": "Hisar", "झज्जर": "Jhajjar", "जींद": "Jind", "जीन्द": "Jind", "कैथल": "Kaithal",
    "करनाल": "Karnal", "कुरुक्षेत्र": "Kurukshetra", "महेंद्रगढ़": "Mahendragarh", "महेन्द्रगढ़": "Mahendragarh", "नूंह": "Nuh", "मेवात": "Nuh", "पलवल": "Palwal",
    "पंचकूला": "Panchkula", "पानीपत": "Panipat", "रेवाड़ी": "Rewari", "रोहतक": "Rohtak", "सिरसा": "Sirsa", "सोनीपत": "Sonipat", "यमुनानगर": "Yamunanagar"}
def _hi_norm(s):
    """Spelling-tolerant form: drop spaces, halant, nukta, anusvara/chandrabindu and the vowel-sign / retroflex variants decoders disagree on."""
    s = re.sub(r"[\s\u094d\u093c\u0902\u0901]", "", s); return s.replace("ड़", "ड").replace("ढ़", "ढ").replace("ी", "ि").replace("ू", "ु").replace("े", "").replace("ै", "")
_HI_LOOKUP = sorted(((_hi_norm(k), v) for k, v in HI_DISTRICTS.items()), key=lambda kv: -len(kv[0]))
def hindi_district(texts, first_pages=3):
    """District named on a legacy-font cover: decode the first pages and take the earliest district name found (title page names the district first)."""
    from kruti_dev import convert_line
    best = None
    for t in texts[:first_pages]:
        dec = _hi_norm(" ".join(convert_line(ln.strip()) for ln in t.split("\n")))
        for key, name in _HI_LOOKUP:
            i = dec.find(key)
            if i >= 0 and (best is None or i < best[0]): best = (i, name)
        if best: return best[1]
    return None


def parse_val(tok):
    """(number or None, raw). '1634-4' -> 1634.4 (this font prints the decimal point as '-'); '..' '--' -> None."""
    t = tok.strip().rstrip("*")
    if t.lower() in MISSING_TOKENS or re.fullmatch(r"-+|\.{2,}", t): return None
    try: return float(re.sub(r"(?<=\d)-(?=\d)", ".", t).replace(",", ""))
    except ValueError: return None

def split_unit(tokens):
    """Longest suffix of the label tokens that is a known unit -> (label_tokens, canonical_unit or None, matched)."""
    for n in range(min(5, len(tokens)), 0, -1):
        phrase = " ".join(tokens[-n:])
        if phrase in UNITS: return tokens[:-n], UNITS[phrase]
    return tokens, None

def title_unit(title):
    """Unit stated in a table title, e.g. 'क्षेत्रफल 00 हैक्टेयर'. A multiplier is kept as printed ('00 ha (as printed)') because
    it is not verified against the source; titles without a hectare phrase give None (unit stays 'unspecified')."""
    m = re.search(r"(?:(\d{2,3}|हजार)\s*)?(?:हैक्टेयर|हेक्टेयर)", title)
    if not m: return None
    if not m.group(1): return "ha"
    return "000 ha" if m.group(1) in ("000", "हजार") else f"{m.group(1)} ha (as printed)"

def crop_of(label):
    for hi, en in CROPS:
        if hi in label: return en
    return None

def indicator_of(label):
    for hi, en in IND_HI:
        if label.startswith(hi): return en
    return re.sub(r"^\(\d+\)\s*", "", label).strip()

def lines_from_boxes(items, gap=0.6):
    """OCR boxes (x0, y0, x1, y1, text) -> text lines: cluster by vertical centre, order left to right."""
    items = sorted(items, key=lambda b: (b[1] + b[3]) / 2)
    if not items: return []
    h = sorted(b[3] - b[1] for b in items)[len(items) // 2] or 1
    rows, cur, cy = [], [], None
    for b in items:
        c = (b[1] + b[3]) / 2
        if cy is not None and abs(c - cy) > gap * h: rows.append(cur); cur = []
        cur.append(b); cy = c if len(cur) == 1 else (cy + c) / 2
    rows.append(cur)
    return [" ".join(b[4] for b in sorted(r, key=lambda b: b[0])) for r in rows]

# ---------------------------------------------------------------- OCR backends (used only for pages with no text layer)
_BACKEND = {}
def _paddle(img):
    import numpy as np
    from paddleocr import PaddleOCR
    if "paddle" not in _BACKEND:
        _BACKEND["paddle"] = PaddleOCR(lang="hi", use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False)
    out = []
    for r in _BACKEND["paddle"].predict(np.array(img)):
        d = r.json["res"] if hasattr(r, "json") else r
        for box, txt in zip(d.get("rec_boxes", []), d.get("rec_texts", [])): out.append((*[float(v) for v in box[:4]], txt))
    return lines_from_boxes(out)
def remove_grid(img, min_frac=0.04):
    """Erase ruled table lines (long horizontal / vertical strokes). Tesseract reads grid lines as '|' and '_' junk and loses the
    text inside the cells; removing them is a fixed image step, not a model decision."""
    import numpy as np, cv2
    g = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
    bw = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    h, w = bw.shape
    lines = cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(int(w * min_frac), 20), 1)))
    lines |= cv2.morphologyEx(bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(int(h * min_frac), 20))))
    lines = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    out = g.copy(); out[lines > 0] = 255
    from PIL import Image
    return Image.fromarray(out)
def _tesseract(img):
    import pytesseract
    if "hin" not in pytesseract.get_languages(config=""): raise RuntimeError("hin.traineddata not installed")
    img = remove_grid(img)
    def boxes(lang, cfg):
        d = pytesseract.image_to_data(img, lang=lang, config=cfg, output_type=pytesseract.Output.DICT)
        return [(d["left"][i], d["top"][i], d["left"][i] + d["width"][i], d["top"][i] + d["height"][i], d["text"][i].strip())
                for i in range(len(d["text"])) if d["text"][i].strip()]
    text = boxes("hin+eng", "--psm 6")
    digits = boxes("eng", "--psm 6 -c tessedit_char_whitelist=0123456789./-")      # second pass: the English model reads digits far better
    def overlap(a, b):
        w = min(a[2], b[2]) - max(a[0], b[0]); h = min(a[3], b[3]) - max(a[1], b[1])
        return (w * h) / max((a[2] - a[0]) * (a[3] - a[1]), 1) if w > 0 and h > 0 else 0
    merged = []
    for b in text:
        if re.search(r"\d", b[4]) or len(b[4]) <= 3:
            best = max(digits, key=lambda e: overlap(b, e), default=None)
            if best and overlap(b, best) > 0.5 and re.fullmatch(r"[\d.,/\-]+", best[4]): b = (*b[:4], best[4])
        merged.append(b)
    return lines_from_boxes(merged)
def _easyocr(img):
    import numpy as np, easyocr
    if "easy" not in _BACKEND: _BACKEND["easy"] = easyocr.Reader(["hi", "en"], gpu=False, verbose=False)
    return lines_from_boxes([(b[0][0], b[0][1], b[2][0], b[2][1], t) for b, t, _ in _BACKEND["easy"].readtext(np.array(img))])
BACKENDS = (("paddle", _paddle), ("tesseract-hin", _tesseract), ("easyocr", _easyocr))

_BROKEN = {}          # backend name -> first error; a backend that failed once is not retried on later pages (e.g. model download blocked)
def ocr_page(img, backends=BACKENDS):
    errs = []
    for name, fn in backends:
        if name in _BROKEN: errs.append(f"{name}: {_BROKEN[name]}"); continue
        try: return name, fn(img)
        except Exception as e:
            _BROKEN[name] = f"{type(e).__name__}: {str(e)[:80]}"; errs.append(f"{name}: {_BROKEN[name]}")
    raise OCRUnavailable("; ".join(errs))

def check_backends():
    """Report which OCR backends actually work on this machine (run once: python hindi_agent.py --check-ocr)."""
    from PIL import Image
    img = Image.new("RGB", (200, 60), "white"); res = {}
    for name, fn in BACKENDS:
        try: fn(img); res[name] = "ok"
        except Exception as e: res[name] = f"{type(e).__name__}: {str(e)[:100]}"
    return res

# ---------------------------------------------------------------- the agent
class HindiOCRAgent(StructuredDataAgent):
    name = "hindi_ocr"; tool = "pdfplumber+kruti_dev(+ocr chain)"; source_type = "pdf_scanned_hindi"; language = "hi"
    def __init__(self, *a, backends=BACKENDS, **k):
        super().__init__(*a, **k); self.backends = backends

    def fetch(self, url):
        path = super().fetch(url)
        with open(path, "rb") as f:
            if not f.read(5).startswith(b"%PDF"): raise ValueError(f"not a PDF (blocked or wrong URL?): {path}")
        return path

    # -- document level ---------------------------------------------------------------
    def _district_and_edition(self, texts, source_name):
        m = re.search(r"ABSTRACT\s*\n?\s*([A-Za-z][A-Za-z ]+?)\s*\n\s*YEAR\s*:?-?\s*((?:19|20)\d{2}-\d{2})", texts[0] if texts else "")
        d = canonical(m.group(1)) if m else None
        if d is None:
            m2 = re.search(r"Abstract:\s*(.+?)(?:\s*\(|$)", source_name)
            d = canonical(m2.group(1)) if m2 else None
        if d is None: d = hindi_district(texts)                                    # fully legacy-font cover (e.g. Hisar): read the district from the decoded title
        if d is None: raise ValueError("district not identified from the cover page, the source name or the decoded Hindi cover")
        ed = m.group(2) if m else None
        if ed is None:
            m3 = re.search(r"((?:19|20)\d{2})\s*[&\u2013-]\s*(\d{2})(?!\d)", " ".join(texts[:2]))     # '2010&11' on a legacy cover
            ed = f"{m3.group(1)}-{m3.group(2)}" if m3 else None
        return d, ed

    def extract(self, path, source_name=""):
        self.skipped_pages, self.rejected_rows, self.flags, self.ocr_pages, self.headers = [], [], [], set(), []
        recs = []
        with pdfplumber.open(path) as pdf:
            texts = [p.extract_text() or "" for p in pdf.pages]
            district, edition = self._district_and_edition(texts, source_name)
            kinds = [classify_page(p, t) for p, t in zip(pdf.pages, texts)]
            if not any(line_is_english(ln) is False and looks_legacy(ln) for t in texts for ln in t.split("\n")) and not any(k == "scanned" for k in kinds):
                raise ValueError("no Hindi text found (all lines are English); use digital_pdf")
            english = frozenset()      # whole English lines are kept by line_is_english(); a word list only caused legacy words to stay undecoded
            state = dict(summary=False, nums=None, year=edition, section=None, sec_unit=None, sec_year=edition, crop=None, pending=None, title=None)
            for pno, (page, text, kind) in enumerate(zip(pdf.pages, texts, kinds), 1):
                if kind == "scanned":
                    try: backend, lines = ocr_page(page.to_image(resolution=300).original.convert("RGB"), self.backends)
                    except OCRUnavailable as e: self.skipped_pages.append((pno, f"scanned, no OCR backend worked ({e})")); continue
                    self.ocr_pages.add(pno)
                else: lines = [ln.strip() if line_is_english(ln) else convert_line(ln, english) for ln in text.split("\n")]
                n = self._page(pno, lines, district, state, recs, kind == "scanned")
                if not n: self.skipped_pages.append((pno, "no table rows"))
        if not recs: raise ValueError("no district rows found (layout not recognised, or only scanned pages and no OCR backend)")
        return recs

    @staticmethod
    def _english_row(line): return bool(re.match(r"^\d+\.\s?[A-Z][a-z]", line))
    @staticmethod
    def _keep_label(line, english):
        """'2.Bawani Khera 0 26 26' -> keep the English tehsil label as printed, decode only the rest."""
        toks = line.split(" "); i = 1
        while i < len(toks) and not VAL.match(toks[i]) and not YEARTOK.match(toks[i]): i += 1
        return " ".join(toks[:i]) + " " + convert_line(" ".join(toks[i:]), english)

    # -- page level -------------------------------------------------------------------
    def _page(self, pno, lines, district, st, recs, ocr):
        n = 0; st["nums"] = None; page_summary = any(re.search(r"मद\s+इकाई\s+जिला", ln) for ln in lines)
        if page_summary: st["summary"] = True
        elif any(re.match(r"^(सारणी|Table No)", ln) for ln in lines): st["summary"] = False; st["nums"] = None
        header_buf = []
        for i, ln in enumerate(lines):
            ln = ln.strip()
            if not ln or re.fullmatch(r"\d{1,3}", ln): continue
            sm = re.fullmatch(r"(\d+)\s+((?:19|20)\d{2}\s*[–-]\s*\d{2,4})(?:\s+\d+)*", ln)
            if st["summary"] and sm:                                                   # '5 2010-11 2 3 4': section number + its year
                st["section"], st["sec_year"], st["crop"], st["pending"] = sm.group(1), re.sub(r"\s", "", sm.group(2)).replace("–", "-"), None, None; continue
            if re.fullmatch(r"(?:\d+\s+)+\d+", ln) and [int(x) for x in ln.split()] == list(range(int(ln.split()[0]), int(ln.split()[0]) + len(ln.split()))):
                st["nums"] = [int(x) for x in ln.split()]
                if header_buf: self.headers.append((pno, st["title"], " | ".join(header_buf))); header_buf = []
                continue
            if re.match(r"^(सारणी|Table No)", ln): st["title"] = ln; st["title_open"] = True
            elif st.get("title_open"):                                      # the line after 'सारणी नं: 9-1' names the table and often its unit
                st["title_open"] = False
                if not VAL.match(ln.split()[-1]): st["title"] = f"{st['title']} {ln}"
            toks = ln.split()
            tail = toks[-2:]
            if st["summary"] and len(toks) >= 3 and all(VAL.match(t) for t in tail) and not all(VAL.match(t) or YEARTOK.match(t) for t in toks):
                n += self._summary_row(pno, i, toks, district, st, recs, ocr); continue
            m = SECTION.match(ln)
            if st["summary"] and m and not VAL.match(toks[-1]):
                st["section"] = m.group(1); ym = YEARIN.search(ln); st["sec_year"] = ym.group(1).replace(" ", "").replace("–", "-") if ym else st["year"]
                if not ym and re.search(r"वर्ष", ln): self.flags.append((pno, f"year not parseable in section header: {ln[:60]}"))
                st["sec_unit"] = split_unit(toks)[1]; st["crop"] = None; st["pending"] = None; continue
            if not st["summary"] and not st["nums"] and sum(bool(VAL.match(t)) for t in toks[1:]) >= 3 and (YEARTOK.match(toks[0]) or re.match(r"^\d+\.", toks[0])):
                self.rejected_rows.append((pno, "no numbered header on this page: " + " ".join(toks)[:60])); continue
            if not st["summary"] and st["nums"] and (YEARTOK.match(toks[0]) or re.match(r"^\d+\.", toks[0]) or toks[0] == "जोड"):
                n += self._numbered_row(pno, i, toks, district, st, recs, ocr); continue
            if not any(ch.isdigit() for ch in ln): st["pending"] = ln; header_buf.append(ln)
            elif st["summary"] is False: header_buf.append(ln)
        return n

    def _summary_row(self, pno, i, toks, district, st, recs, ocr):
        label_toks, unit = split_unit(toks[:-2])
        if unit is None and label_toks and label_toks[-1] in ("ष्", "ष", "\"", "”"): label_toks, unit = label_toks[:-1], st["sec_unit"]   # ditto mark = same unit as the section
        label = " ".join(label_toks).strip()
        if not label: self.rejected_rows.append((pno, " ".join(toks)[:70])); return 0
        ind = indicator_of(label); is_crop_ind = any(label.startswith(h) for h, _ in IND_HI)
        if is_crop_ind and st["pending"]: st["crop"] = crop_of(st["pending"]) or st["pending"]
        if not is_crop_ind: st["crop"] = None
        st["pending"] = None
        ym = re.search(r"\(\s*((?:19|20)\d{2}(?:\s*[–-]\s*\d{2,4})?)\s*\)", label)          # a year inside the row label wins over the section year
        ys = re.search(r"\(\s*(\d{2})\s*[–-]\s*(\d{2})\s*\)", label)                    # short form '(11-12)' = 2011-12
        year = ym.group(1).replace(" ", "").replace("–", "-") if ym else (f"20{ys.group(1)}-{ys.group(2)}" if ys else st["sec_year"])
        if ym: ind = re.sub(r"\s*\(\s*(?:19|20)\d{2}[^)]*\)", "", ind).strip()
        elif ys: ind = re.sub(r"\s*\(\s*\d{2}\s*[–-]\s*\d{2}\s*\)", "", ind).strip()
        recs_before = len(recs)
        for col, tok, dist in ((3, toks[-2], district), (4, toks[-1], None)):
            if "/" in tok: self.rejected_rows.append((pno, " ".join(toks)[:70])); continue            # 'a/b' pairs: not a single value
            recs.append(dict(district_raw=dist, crop=st["crop"], season=None, indicator=ind, unit=unit or UNKNOWN_UNIT, year=year,
                             value_num=parse_val(tok), value_text=tok, page_no=pno, row_ref=f"line{i + 1}", table_ref=f"p{pno}c{col}",
                             confidence=OCR_CONF if ocr else 1.0, needs_review=int(ocr or unit is None)))
        return len(recs) - recs_before

    def _numbered_row(self, pno, i, toks, district, st, recs, ocr):
        j = 1
        if re.match(r"^\d+\.", toks[0]):                                      # '1.Bhiwani' / '2.Bawani Khera' (English tehsil label)
            while j < len(toks) and not VAL.match(toks[j]): j += 1
            label = re.sub(r"^\d+\.\s*", "", " ".join(toks[:j])); year_tok = None
        elif toks[0] == "जोड": label, year_tok = "जोड (total)", None
        else: label, year_tok = None, toks[0]
        vals = toks[j:]; nums = st["nums"]
        if label is None: st["year"] = re.sub(r"\([A-Z]\)", "", year_tok)
        if not vals: return 0                                                  # year-only line: sets the year for the tehsil rows below
        if not all(VAL.match(v) for v in vals): self.rejected_rows.append((pno, " ".join(toks)[:70])); return 0
        cols = nums if len(vals) == len(nums) else (nums[1:] if len(vals) == len(nums) - 1 else None)
        if cols is None: self.rejected_rows.append((pno, " ".join(toks)[:70])); return 0     # wrong number of values: rejected, never guessed
        year = st["year"]; n = 0
        title = re.sub(r"\s+", " ", (st["title"] or "table")).strip()
        unit = title_unit(title) or UNKNOWN_UNIT
        for c, v in zip(cols, vals):
            recs.append(dict(district_raw=district, tehsil_block=label, crop=None, season=None, indicator=f"{title} | col {c}", unit=unit,
                             year=year, value_num=parse_val(v), value_text=v, page_no=pno, row_ref=f"line{i + 1}", table_ref=f"p{pno}c{c}",
                             confidence=OCR_CONF if ocr else 1.0, needs_review=1))     # column meaning/unit sit in the header text (text_fact): review
            n += 1
        return n

    # -- load: shared loader + OCR flags + header text kept for traceability -------------------------------
    def load(self, recs, src):
        import sqlite3
        out = super().load(recs, src)
        con = sqlite3.connect(self.db_path)
        sid = con.execute("select source_id from extraction_run where run_id=?", (out["run_id"],)).fetchone()[0]
        con.execute("update extraction_run set tool=? where run_id=?", (self.tool, out["run_id"]))
        for pno, title, text in self.headers:
            con.execute("insert into text_fact(run_id,source_id,location_ref,topic,content,language) values(?,?,?,?,?,'hi')",
                        (out["run_id"], sid, f"p{pno}", "table_header", f"{title or ''} || {text}"))
        con.commit(); con.close()
        out.update(rejected=len(self.rejected_rows), skipped_pages=len(self.skipped_pages), ocr_pages=len(self.ocr_pages))
        return out

    def run(self, src):
        path = src.get("local_file") or self.fetch(src["URLs"])
        return self.load(self.extract(path, src["List of sources"]), src)

if __name__ == "__main__":
    import sys
    if "--check-ocr" in sys.argv: print(check_backends())
