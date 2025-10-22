"""structured_data agent: XLSX/CSV -> filter Haryana -> long format -> SQLite (haryana_schema.sql).
All steps are fixed code; no LLM is used.
Supports the ICRISAT-TCI 'area-production' layout (verified on a real download):
  rows 0-5 notes/units, rows 6-9 = crop / season / measure / code header, data from row 10,
  ID columns coded STNAME, DIST, DISTNAME, YEAR; '.' = blank value.
Falls back to a single-row header format ('RICE AREA (1000 ha)') for other files."""
import os, re, ssl, sqlite3, urllib.request, urllib.error, datetime as dt
import pandas as pd
from haryana_pipeline import Agent, AgentNotImplemented
from district_names import canonical, DISTRICTS, LGD_CODES

HERE = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(HERE, "data", "raw"); os.makedirs(RAW_DIR, exist_ok=True)
MISSING_TOKENS = {"", "-", "--", "..", ".", "na", "n.a.", "nan"}
SEASON_MAP = {"kharif": "kharif", "rabi": "rabi", "summer": "zaid", "total": "annual", "whole year": "annual"}
DEFAULT_UNITS = {"area": "000 ha", "production": "000 tonnes", "yield": "kg/ha"}
# production units that differ from the default, per the sheet's own note (row 2 / row 1)
PROD_UNIT_OVERRIDE = {"cotton(lint)": "bales (per sheet note; scale unverified)", "jute": "bales (per sheet note; scale unverified)",
                      "mesta": "bales (per sheet note; scale unverified)", "sugarcane": "000 tonnes (gur equivalent, per sheet note)"}

def sniff(path):
    """Detect file type from its first bytes, not from the file name."""
    with open(path, "rb") as f: head = f.read(2048)
    if head.startswith(b"PK\x03\x04"): return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"): return "xls"
    low = head.lstrip().lower()
    if low.startswith((b"<!doctype", b"<html", b"<?xml")) or b"<html" in low[:500]:
        raise ValueError(f"downloaded file is HTML, not data (blocked or wrong URL): {path}")
    return "csv"

def read_raw(path):
    kind = sniff(path)
    if kind == "xlsx": return pd.read_excel(path, header=None, engine="openpyxl")       # first sheet
    if kind == "xls":  return pd.read_excel(path, header=None, engine="xlrd")           # pip install xlrd
    try: return pd.read_csv(path, header=None)
    except UnicodeDecodeError: return pd.read_csv(path, header=None, encoding="latin-1")

def _s(v): return "" if pd.isna(v) else str(v).strip()
def _year(v):
    try: return str(int(float(v)))
    except (ValueError, TypeError): return _s(v)
def _num(txt):
    if txt.lower() in MISSING_TOKENS: return None
    try: return float(txt.replace(",", ""))
    except ValueError: return None

def parse_icrisat(raw):
    """Multi-row header layout. Returns list of records, or None if layout not recognised."""
    code_row = None
    for i in range(min(25, len(raw))):
        if "STNAME" in [_s(v).upper() for v in raw.iloc[i]]: code_row = i; break
    if code_row is None or code_row < 3: return None
    codes = [_s(v).upper() for v in raw.iloc[code_row]]
    ix = {k: codes.index(k) for k in ("STNAME", "DISTNAME", "YEAR") if k in codes}
    if len(ix) < 3: return None
    meas_r, seas_r, crop_r = code_row - 1, code_row - 2, code_row - 3
    unit_r = crop_r - 1                                               # optional per-column unit override
    start = code_row + 1
    cols = []
    for c in range(raw.shape[1]):
        if c in ix.values() or not codes[c]: continue
        crop, seas, meas = _s(raw.iat[crop_r, c]), _s(raw.iat[seas_r, c]), _s(raw.iat[meas_r, c]).lower()
        if not crop or meas not in DEFAULT_UNITS: continue
        ov = _s(raw.iat[unit_r, c]).strip("() ") if unit_r >= 0 else ""
        unit = ov or (PROD_UNIT_OVERRIDE.get(crop.lower()) if meas == "production" else None) or DEFAULT_UNITS[meas]
        cols.append((c, crop, SEASON_MAP.get(seas.lower()), meas, unit, codes[c]))
    recs = []
    for r in range(start, len(raw)):
        if _s(raw.iat[r, ix["STNAME"]]).lower() != "haryana": continue
        dist, year = _s(raw.iat[r, ix["DISTNAME"]]), _year(raw.iat[r, ix["YEAR"]])
        for c, crop, season, meas, unit, code in cols:
            txt = _s(raw.iat[r, c])
            recs.append(dict(district_raw=dist, crop=crop, season=season, indicator=meas, unit=unit, year=year,
                             value_num=_num(txt), value_text=txt, row_ref=f"r{r + 1}", table_ref=f"col:{code}"))
    return recs

# ---- fallback: single-row header like 'RICE AREA (1000 ha)' ----
def find_col(df, *cands):
    for c in df.columns:
        if re.sub(r"[^a-z]", "", str(c).lower()) in cands: return c
    return None
def parse_column(col):
    m = re.match(r"^\s*(.*?)\s*\b(AREA|PRODUCTION|YIELD)\b\s*(?:\((.*?)\))?\s*$", str(col), re.I)
    if not m: return None
    return (m.group(1).strip().title() or None, m.group(2).upper(), (m.group(3) or "").strip())
def parse_simple(raw):
    for i in range(min(15, len(raw))):
        vals = [re.sub(r"[^a-z]", "", str(v).lower()) for v in raw.iloc[i]]
        if "state" in " ".join(vals) or any(v.startswith("dist") for v in vals):
            df = raw.iloc[i + 1:].copy(); df.columns = [str(c).strip() for c in raw.iloc[i]]; hdr = i + 1; break
    else: return []
    st, di, yr = find_col(df, "state", "statename"), find_col(df, "dist", "district", "distname", "districtname"), find_col(df, "year")
    if st is not None: df = df[df[st].astype(str).str.strip().str.lower() == "haryana"]
    data_cols = {c: parse_column(c) for c in df.columns if parse_column(c)}
    recs = []
    for ridx, row in df.iterrows():
        for col, (crop, ind, unit) in data_cols.items():
            txt = _s(row[col])
            recs.append(dict(district_raw=None if di is None else _s(row[di]), crop=crop, season=None, indicator=ind.lower(), unit=unit,
                             year=None if yr is None else _year(row[yr]), value_num=_num(txt), value_text=txt,
                             row_ref=f"r{ridx + hdr + 1}", table_ref=f"col:{col}"))
    return recs

class StructuredDataAgent(Agent):
    name = "structured_data"; tool = "pandas+openpyxl"; source_type = None   # None = infer xlsx/csv from URL
    def __init__(self, db_path=os.path.join(HERE, "haryana.db"), schema=os.path.join(HERE, "haryana_schema.sql")):
        self.db_path, self.schema = db_path, schema
    def fetch(self, url_or_path):                                   # tool 1
        if os.path.exists(url_or_path): return url_or_path
        dest = os.path.join(RAW_DIR, re.sub(r"[^A-Za-z0-9._-]", "_", url_or_path)[-120:])
        if not os.path.exists(dest):
            req = urllib.request.Request(url_or_path, headers={"User-Agent": "Mozilla/5.0"})
            try:
                resp = urllib.request.urlopen(req, timeout=120)
            except urllib.error.URLError as e:
                if not isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError): raise
                if os.environ.get("ALLOW_INSECURE_DOWNLOAD") != "1":
                    raise RuntimeError("SSL certificate could not be verified for " + url_or_path + ". Either download the file "
                        "yourself and pass it as src['local_file'], or (public documents only) set ALLOW_INSECURE_DOWNLOAD=1.") from e
                resp = urllib.request.urlopen(req, timeout=120, context=ssl._create_unverified_context())   # opt-in, logged by caller
            with resp as r, open(dest, "wb") as f: f.write(r.read())
        return dest
    def extract(self, path):                                        # tool 2 (pure, testable)
        raw = read_raw(path)
        recs = parse_icrisat(raw)
        if recs is None: recs = parse_simple(raw)
        if not recs: raise ValueError("no Haryana records parsed: layout not recognised or no Haryana rows")
        return recs
    def load(self, recs, src):                                      # tool 3
        con = sqlite3.connect(self.db_path); con.execute("PRAGMA foreign_keys=ON")
        if not con.execute("select 1 from sqlite_master where name='observation'").fetchone():
            con.executescript(open(self.schema, encoding="utf-8").read())
        for d in DISTRICTS:
            con.execute("insert or ignore into district(name_en,lgd_code) values(?,?)", (d, LGD_CODES.get(d)))
        con.execute("insert or ignore into agent(name) values(?)", (self.name,))
        aid = con.execute("select agent_id from agent where name=?", (self.name,)).fetchone()[0]
        con.execute("insert or ignore into source(name,url,source_type,language,period_start) values(?,?,?,?,?)",
                    (src["List of sources"], src["URLs"], self.source_type or ("xlsx" if "xls" in src["URLs"].lower() or "bulkDownload" in src["URLs"] else "csv"),
                     getattr(self, "language", "en"), src.get("Period Of Interest")))
        sid = con.execute("select source_id from source where url=?", (src["URLs"],)).fetchone()[0]
        run_id = con.execute("insert into extraction_run(agent_id,source_id,started_at,tool,tool_version,status) values(?,?,?,?,?,'ok')",
                  (aid, sid, dt.datetime.now(dt.timezone.utc).isoformat(), self.tool, pd.__version__)).lastrowid
        dcache, ccache, ucache = {}, {}, {}
        def did_of(raw):
            if raw not in dcache:
                canon = canonical(raw) if raw else None
                row = con.execute("select district_id from district where name_en=?", (canon,)).fetchone() if canon else None
                dcache[raw] = row[0] if row else None
            return dcache[raw]
        def cid_of(n):
            if not n: return None
            if n not in ccache:
                con.execute("insert or ignore into crop(name_en) values(?)", (n,))
                ccache[n] = con.execute("select crop_id from crop where name_en=?", (n,)).fetchone()[0]
            return ccache[n]
        def uid_of(u):
            if not u: return None
            if u not in ucache:
                con.execute("insert or ignore into unit(raw_label,canonical) values(?,?)", (u, u))
                ucache[u] = con.execute("select unit_id from unit where raw_label=?", (u,)).fetchone()[0]
            return ucache[u]
        ins = dup = unresolved = 0
        for r in recs:
            did = did_of(r["district_raw"]); review = int(did is None and bool(r["district_raw"])); unresolved += review
            miss = None if r["value_num"] is not None else ("dash_in_source" if r["value_text"] else "not_reported")
            try:
                con.execute("""insert into observation(run_id,source_id,page_no,table_ref,row_ref,district_id,district_raw,crop_id,season,
                    year_label,indicator,value_num,value_text,unit_id,missing_reason,confidence,needs_review,as_of,tehsil_block)
                    values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (run_id, sid, r.get("page_no"), r["table_ref"], r["row_ref"], did, r["district_raw"], cid_of(r["crop"]), r["season"], r["year"],
                     r["indicator"], r["value_num"], r["value_text"], uid_of(r["unit"]), miss, r.get("confidence", 1.0), max(review, int(r.get("needs_review", 0))), r.get("as_of"), r.get("tehsil_block"))); ins += 1
            except sqlite3.IntegrityError: dup += 1
        con.commit(); con.close()
        return dict(run_id=run_id, inserted=ins, duplicates=dup, unresolved_districts=unresolved)
    def run(self, src):
        url = src["URLs"]
        if "data.gov.in" in url: raise AgentNotImplemented("data.gov.in API needs account + key; not implemented")
        if "download index page" in src["List of sources"]: return
        return self.load(self.extract(src.get("local_file") or self.fetch(url)), src)
