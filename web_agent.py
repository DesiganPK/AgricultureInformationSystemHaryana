"""web_content agent: an HTML page (saved file or URL) -> rows in the database.

  tables   every <table> is read cell by cell (colspan / rowspan expanded) and handed to the same generic reader the Excel agent uses
           (structured_agent.parse_generic): header row, district / year / crop columns, and table_mapper for what each value column
           means (rules first; the LLM, if on, for headings the rules do not know). Numbers are copied from the cells by code.
  text     headings and paragraphs are kept as text_fact rows (one per section, with the heading as topic), and every link to a
           data file (.xlsx .xls .csv .pdf) is kept as a text_fact row with topic 'data_link', so a download index page is not lost.
No JavaScript is run: a page that builds its tables in the browser gives text only (reported, not guessed)."""
import os, re, sqlite3
from html.parser import HTMLParser
from urllib.parse import urljoin
import pandas as pd
from structured_agent import StructuredDataAgent, parse_generic

SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "form"}
BLOCK = {"p", "li", "div", "section", "article", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "caption", "dd", "dt", "blockquote"}
DATA_LINK = re.compile(r"\.(xlsx|xls|csv|pdf)(\?|#|$)|bulkDownload", re.I)


class _Page(HTMLParser):
    """Collects tables (as grids of cell text), sections (heading + text) and links."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.sections, self.links = [], [["", []]], []
        self._skip = 0; self._stack = []          # open tables: dict(rows, row, cell, caption, spans)
        self._heading = None; self._caption = None; self._title = None; self.title = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP: self._skip += 1
        if tag == "title": self._title = []
        if self._skip: return
        if tag == "table": self._stack.append(dict(rows=[], row=None, cell=None, caption="", spans={}, heading=self.sections[-1][0]))
        elif tag == "tr" and self._stack: self._stack[-1]["row"] = []
        elif tag in ("td", "th") and self._stack and self._stack[-1]["row"] is not None:
            n = lambda k: max(1, min(int(a.get(k, "1")) if str(a.get(k, "1")).isdigit() else 1, 60))
            self._stack[-1]["cell"] = dict(text=[], colspan=n("colspan"), rowspan=n("rowspan"))
        elif tag == "caption" and self._stack: self._caption = []
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and not self._stack: self._heading = []
        elif tag == "a" and a.get("href"): self.links.append(a["href"])
        if tag in BLOCK and not self._stack and self._heading is None: self.sections[-1][1].append("\n")

    def handle_endtag(self, tag):
        if tag == "title" and self._title is not None: self.title = _clean(" ".join(self._title)); self._title = None
        if tag in SKIP and self._skip: self._skip -= 1; return
        if self._skip: return
        if tag in ("td", "th") and self._stack and self._stack[-1]["cell"] is not None:
            t = self._stack[-1]; c = t["cell"]; t["cell"] = None
            t["row"].append((_clean(" ".join(c["text"])), c["colspan"], c["rowspan"]))
        elif tag == "tr" and self._stack and self._stack[-1]["row"] is not None:
            t = self._stack[-1]; t["rows"].append(t["row"]); t["row"] = None
        elif tag == "caption" and self._caption is not None and self._stack:
            self._stack[-1]["caption"] = _clean(" ".join(self._caption)); self._caption = None
        elif tag == "table" and self._stack:
            t = self._stack.pop(); grid = _expand(t["rows"])
            if grid: self.tables.append(dict(grid=grid, caption=t["caption"] or t["heading"]))
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self._heading is not None:
            self.sections.append([_clean(" ".join(self._heading)), []]); self._heading = None

    def handle_data(self, data):
        if self._title is not None: self._title.append(data); return
        if self._skip: return
        if self._caption is not None: self._caption.append(data)
        elif self._stack:
            if self._stack[-1]["cell"] is not None: self._stack[-1]["cell"]["text"].append(data)
        elif self._heading is not None: self._heading.append(data)
        else: self.sections[-1][1].append(data)


def _clean(s): return re.sub(r"\s+", " ", s.replace("\xa0", " ")).strip()


def _expand(rows):
    """Rows of (text, colspan, rowspan) -> rectangular grid of strings; a spanned cell's text is repeated in every cell it covers."""
    grid, carry = [], {}                    # carry: column -> (text, rows left)
    for row in rows:
        out, j = [], 0; cells = list(row)
        while cells or j in carry:
            if j in carry:
                txt, left = carry[j]; out.append(txt)
                if left > 1: carry[j] = (txt, left - 1)
                else: del carry[j]
                j += 1; continue
            txt, cs, rs = cells.pop(0)
            for _ in range(cs):
                out.append(txt)
                if rs > 1: carry[j] = (txt, rs - 1)
                j += 1
        if any(out): grid.append(out)
    width = max((len(r) for r in grid), default=0)
    return [r + [""] * (width - len(r)) for r in grid]


def merge_header(grid):
    """Several header rows (a crop spanning 'Area' and 'Production') -> one header row per column: 'Rice Area', 'Rice Production'."""
    num = re.compile(r"^[-+]?[\d,]*\.?\d+%?$")
    k = 0
    while k < len(grid) and not any(num.match(c.strip()) for c in grid[k]): k += 1
    if k <= 1 or k == len(grid): return grid
    head = [" ".join(dict.fromkeys(grid[i][j] for i in range(k) if grid[i][j])) for j in range(len(grid[0]))]
    return [head] + grid[k:]


def parse_html(text):
    p = _Page(); p.feed(text); p.close()
    return p


class WebContentAgent(StructuredDataAgent):
    name = "web_content"; tool = "html.parser+pandas"; source_type = "html"

    def extract(self, path, base_url=""):
        with open(path, "rb") as f: raw = f.read()
        m = re.search(rb"charset=[\"']?([A-Za-z0-9_-]+)", raw[:3000])
        try: text = raw.decode(m.group(1).decode() if m else "utf-8", "replace")
        except LookupError: text = raw.decode("utf-8", "replace")
        page = parse_html(text)
        recs = []; self.rejected_rows = []; self.skipped_pages = []; self.sections = []; self.links = []
        for k, t in enumerate(page.tables, 1):
            grid = merge_header(t["grid"])
            if len(grid) < 2 or len(grid[0]) < 2: continue                           # layout tables
            frame = pd.DataFrame(([[t["caption"]] + [""] * (len(grid[0]) - 1)] if t["caption"] else []) + grid)
            try: got = parse_generic(frame)
            except Exception as e: got = []; self.skipped_pages.append((k, f"table {k}: {type(e).__name__}: {e}"))
            if not got: self.skipped_pages.append((k, f"table {k}: no district / year / crop column with numeric values")); continue
            for r in got:
                r["page_no"] = None; r["table_ref"] = f"table{k}|{r['table_ref']}"; r["row_ref"] = f"table{k}:{r['row_ref']}"
                r["crop_na"] = not r.get("crop")
            recs += got
        for head, parts in page.sections:
            body = re.sub(r"[ \t]*\n[ \t\n]*", "\n", _clean_keep_lines("".join(parts)))
            if len(body) >= 40: self.sections.append((head or page.title or "page", body))
        seen = set()
        for href in page.links:
            if DATA_LINK.search(href):
                u = urljoin(base_url, href) if base_url.startswith("http") else href
                if u not in seen: seen.add(u); self.links.append(u)
        if not recs and not self.sections and not self.links: raise ValueError("no table, text or data link found in the page (it may be built by JavaScript)")
        return recs

    def run(self, src):
        url = src["URLs"]
        path = src.get("local_file") or self.fetch(url)
        recs = self.extract(path, url)
        out = self.load(recs, src)
        con = sqlite3.connect(self.db_path)
        sid = con.execute("select source_id from extraction_run where run_id=?", (out["run_id"],)).fetchone()[0]
        for i, (head, body) in enumerate(self.sections, 1):
            con.execute("insert into text_fact(run_id,source_id,location_ref,topic,content,language) values(?,?,?,?,?,?)",
                        (out["run_id"], sid, f"section{i}", head[:200], body[:8000], "en"))
        for i, u in enumerate(self.links, 1):
            con.execute("insert into text_fact(run_id,source_id,location_ref,topic,content,language) values(?,?,?,?,?,?)", (out["run_id"], sid, f"link{i}", "data_link", u, "en"))
        con.commit(); con.close()
        out["text_pages"] = len(self.sections); out["data_links"] = len(self.links)
        return out


def _clean_keep_lines(s): return "\n".join(x for x in (re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in s.split("\n")) if x)
