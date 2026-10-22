"""digital_pdf agent: text-layer PDF -> district rows -> SQLite via the shared loader. Fixed code, no LLM.
Two extraction strategies, chosen per source (SOURCE_CONFIG) and verified on real files:
  'lines'  - borderless tables read line by line (DES farm harvest prices: district, year, 13 crop columns)
  'ruled'  - ruled tables read with pdfplumber (IMD monsoon bulletin: DISTRICT / ACTUAL / NORMAL / DEPARTURE)
Decisions made in code (not by a model):
  * no page of the document has a text layer      -> Reroute to hindi_ocr
  * page text looks like legacy-font Hindi (Kruti Dev etc.) -> Reroute to hindi_ocr
  * row has the wrong number of values            -> row rejected and counted (never guessed)
  * '..' / '-' -> missing; '1320r' -> value 1320, raw text (with flag) kept
  * pages with no table rows (maps, charts)       -> skipped and logged in self.skipped_pages"""
import re, datetime as dt
import pdfplumber
from haryana_pipeline import Reroute
from structured_agent import StructuredDataAgent, MISSING_TOKENS, _s
from district_names import canonical

SOURCE_CONFIG = [
    ("Farm Harvest Prices", dict(layout="lines", indicator="farm_harvest_price", unit="Rs/quintal (unverified)",
                                 header_stubs={"Tur", "Sugar", "Ground", "Caster"})),
    ("rainfall bulletin",   dict(layout="ruled", indicator_prefix="rainfall_", as_of=True, closed_districts=True,
                                 header_keys={"actual": "actual", "normal": "normal", "departure": "departure"},
                                 units={"actual": "mm", "normal": "mm", "departure": "%"})),
]
DEFAULT_CFG = dict(layout="ruled", indicator="value", unit=None)
SKIP_ROW = re.compile(r"total|haryana|state|all districts|average|source|note|chandigarh", re.I)
VAL = re.compile(r"^\s*([-+]?\d[\d,]*\.?\d*)\s*([A-Za-z]{0,2})\s*$")
YEAR = re.compile(r"\b((?:19|20)\d{2}(?:\s*[-–]\s*\d{2,4})?)\b")
ROWYEAR = re.compile(r"(?:19|20)\d{2}-\d{2}")
DATE = re.compile(r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+((?:19|20)\d{2})")
UNIT = re.compile(r"\(\s*Rs\.?\s*per\s*(\w+)\s*\)", re.I)

def parse_value(txt):
    t = txt.strip()
    if t.lower() in MISSING_TOKENS: return None, ""
    m = VAL.match(t)
    return (float(m.group(1).replace(",", "")), m.group(2)) if m else (None, "")

def legacy_font_score(text):
    """Share of word-like tokens containing punctuation typical of Kruti Dev/DevLys text ('[', ';', quotes inside words)."""
    toks = [t for t in text.split() if len(t) >= 3 and not any(ch.isdigit() for ch in t)]
    if not toks: return 0.0
    bad = [t for t in toks if re.search(r"[A-Za-z][\[\];\"”“'’&{}<>^@/+][A-Za-z]|[A-Za-z]\.[A-Za-z]", t)]
    return len(bad) / len(toks)

def merge_dots(tokens):
    out, i = [], 0
    while i < len(tokens):
        if tokens[i] == "." and i + 1 < len(tokens) and tokens[i + 1] == ".": out.append(".."); i += 2
        else: out.append(tokens[i]); i += 1
    return out

def is_header(row):
    if re.match(r"\s*(district|name|dist\.?)\b", row[0], re.I): return True
    return not any(parse_value(c)[0] is not None for c in row[1:])

class DigitalPDFAgent(StructuredDataAgent):
    name = "digital_pdf"; tool = "pdfplumber"; source_type = "pdf_digital"
    def fetch(self, url):
        path = super().fetch(url)
        with open(path, "rb") as f:
            if not f.read(5).startswith(b"%PDF"): raise ValueError(f"not a PDF (blocked or wrong URL?): {path}")
        return path
    def extract(self, path, source_name=""):
        cfg = next((c for k, c in SOURCE_CONFIG if k.lower() in source_name.lower()), DEFAULT_CFG)
        self.skipped_pages, self.rejected_rows = [], []
        recs = []
        with pdfplumber.open(path) as pdf:
            texts = [p.extract_text() or "" for p in pdf.pages]
            if sum(len(p.chars) >= 20 for p in pdf.pages) == 0:
                raise Reroute("hindi_ocr", "no page has a text layer (scanned)")
            sample = " ".join(texts[:5])
            legacy_pages = sum(legacy_font_score(t) > 0.2 for t in texts if len(t) > 50)      # judged page by page over the WHOLE document
            if legacy_pages >= max(1, -(-len(texts) * 15 // 100)) or legacy_font_score(sample) > 0.2:    # >= 15% of pages (rounded up), at least 1
                raise Reroute("hindi_ocr", f"legacy-font text layer ({legacy_pages} of {len(texts)} pages)")
            as_of = None
            if cfg.get("as_of"):
                m = DATE.search(sample)
                if m: as_of = dt.datetime.strptime(" ".join(m.groups()), "%d %B %Y").date().isoformat()
            state = {"header": None}
            for pno, page in enumerate(pdf.pages, 1):
                got = (self._lines(pno, texts[pno-1], cfg, recs) if cfg["layout"] == "lines"
                       else self._ruled(pno, page, texts[pno-1], cfg, state, recs, as_of))
                if not got: self.skipped_pages.append(pno)
        if not recs: raise ValueError("no district rows found (layout not recognised?)")
        return recs
    # ---- strategy 1: borderless, line by line -------------------------------------------------
    def _lines(self, pno, text, cfg, recs):
        lines = text.split("\n"); crops = None; nums = None; district = None; n = 0
        m = UNIT.search(text); unit = f"Rs/{m.group(1)}" if m else cfg["unit"]
        for i, ln in enumerate(lines):
            if ln.lower().startswith("district name") and "year" in ln.lower():
                base = ln.split("Year", 1)[1].split()
                cont = lines[i + 1].split() if i + 1 < len(lines) and not lines[i + 1].startswith("(") else []
                crops, k = [], 0
                for b in base:                                   # a stub ('Tur', 'Rape/', 'Sann-') takes the next word of line 2
                    if (b in cfg["header_stubs"] or b.endswith(("/", "-"))) and k < len(cont):
                        crops.append(f"{b}{cont[k]}" if b.endswith(("/", "-")) else f"{b} {cont[k]}"); k += 1
                    else: crops.append(b)
                numline = next((x for x in lines[i + 1:i + 4] if x.startswith("(1)")), "")        # the PDF's own column numbers
                nums = [int(x) for x in re.findall(r"\((\d+)\)", numline)][2:]
                if len(nums) != len(crops): nums = list(range(3, 3 + len(crops)))
                continue
            m = re.match(r"^(?:(?P<name>.+?)\s+)?(?P<yr>" + ROWYEAR.pattern + r")\s+(?P<vals>.+)$", ln)
            if not (m and crops): continue
            if m.group("name"): district = m.group("name").strip()
            vals = merge_dots(m.group("vals").split())
            if district is None or len(vals) != len(crops):
                self.rejected_rows.append((pno, ln)); continue
            if SKIP_ROW.search(district): continue
            for c, (crop, v) in enumerate(zip(crops, vals)):
                num, _ = parse_value(v)
                recs.append(dict(district_raw=district, crop=crop, season=None, indicator=cfg["indicator"], unit=unit,
                                 year=m.group("yr"), value_num=num, value_text=v, page_no=pno, row_ref=f"line{i+1}",
                                 table_ref=f"p{pno}c{nums[c]}"))
                n += 1
        return n
    # ---- strategy 2: ruled tables ------------------------------------------------------------
    def _ruled(self, pno, page, text, cfg, state, recs, as_of):
        ym = YEAR.search(text); year = re.sub(r"\s+", "", ym.group(1)) if ym else None
        n = 0
        for tno, table in enumerate(page.extract_tables(), 1):
            rows = [[_s(c).replace("\n", " ") for c in r] for r in table if r]
            keep = [j for j in range(max(len(r) for r in rows)) if any(j < len(r) and r[j] for r in rows)]   # drop ghost columns
            rows = [[r[j] if j < len(r) else "" for j in keep] for r in rows]
            rows = [r for r in rows if any(r)]
            if not rows or len(rows[0]) < 2: continue
            hidx = next((i for i, r in enumerate(rows) if sum(bool(c) for c in r) >= 2 and is_header(r)), None)
            if hidx is not None: header, body, start = rows[hidx], rows[hidx + 1:], hidx + 2; state["header"] = header
            elif state["header"]: header, body, start = state["header"], rows, 1          # continuation page
            else: continue
            for ridx, row in enumerate(body, start):
                dist = row[0]
                if not dist or SKIP_ROW.search(dist) or not any(row[1:]): continue
                if cfg.get("closed_districts") and canonical(dist) is None:
                    self.rejected_rows.append((pno, dist[:60])); continue      # debris / non-district rows: logged, not stored
                for cidx, cell in enumerate(row[1:], 1):
                    if cidx >= len(header) or not header[cidx]: continue
                    hname = header[cidx].strip()
                    if cfg.get("header_keys"):                                  # only accept known column headings
                        hname = next((v for k, v in cfg["header_keys"].items() if k in hname.lower()), None)
                        if hname is None: continue
                    ind = cfg.get("indicator_prefix", "") + hname.lower() if "indicator_prefix" in cfg else cfg["indicator"]
                    unit = cfg.get("units", {}).get(hname.lower(), cfg.get("unit"))
                    num, _ = parse_value(cell)
                    recs.append(dict(district_raw=dist, crop=None if "indicator_prefix" in cfg else hname, season=None,
                                     indicator=ind, unit=unit, year=year, as_of=as_of, value_num=num, value_text=cell,
                                     page_no=pno, row_ref=f"row{ridx}", table_ref=f"p{pno}t{tno}c{cidx}"))
                    n += 1
        return n
    def run(self, src):
        path = src.get("local_file") or self.fetch(src["URLs"])
        return self.load(self.extract(path, src["List of sources"]), src)
