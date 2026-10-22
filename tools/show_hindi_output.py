"""Show what the Hindi agent does on one PDF page, in the order the protocol asks for:
   source -> decision -> tool call -> tool result -> output -> 1 hard case -> trace-back.
Usage:  python tools/show_hindi_output.py [pdf] [--page 8] [--html out.html] [--csv out.csv] [--db haryana.db]
Defaults: data/raw/Bhiwani.pdf, page 8 (the item / unit / district / state summary table).
--html writes ONE web page with the real PDF page image on the left and the decoded rows on the right (open it in a browser; best for showing Hindi,
because Windows consoles often cannot draw Devanagari).  --csv writes the page's rows (open in Excel; UTF-8 with BOM so Hindi displays).
Nothing is written to any database; this re-runs the agent's extract step."""
import argparse, base64, csv, html, io, os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import pdfplumber
from haryana_pipeline import load
from pdf_agent import legacy_font_score
from hindi_agent import HindiOCRAgent, line_is_english
from kruti_dev import convert_line

ap = argparse.ArgumentParser(); ap.add_argument("pdf", nargs="?", default=os.path.join(ROOT, "data", "raw", "Bhiwani.pdf"))
ap.add_argument("--page", type=int, default=8); ap.add_argument("--html"); ap.add_argument("--csv"); ap.add_argument("--db"); ap.add_argument("--source-name", default="")
ap.add_argument("--lines", type=int, default=30, help="how many lines to print to the console")
a = ap.parse_args()
bar = lambda t: print(f"\n{'=' * 8} {t} {'=' * (70 - len(t))}")

# ---- 1. source and decision ---------------------------------------------------------------------------------
with pdfplumber.open(a.pdf) as pdf:
    texts = [p.extract_text() or "" for p in pdf.pages]; page = pdf.pages[a.page - 1]
    raw_lines = texts[a.page - 1].split("\n")
    img = page.to_image(resolution=110).original.convert("RGB")
legacy_pages = sum(legacy_font_score(t) > 0.2 for t in texts if len(t) > 50)
bar("1. SOURCE")
print(f"file: {os.path.basename(a.pdf)} | {len(texts)} pages | text layer on {sum(len(t) > 20 for t in texts)} pages (so this is not a scanned file)")
bar("2. DECISION (fixed code, no model)")
need = max(1, -(-len(texts) * 15 // 100))
print(f"digital_pdf agent counted {legacy_pages} of {len(texts)} pages in legacy Kruti Dev font (needs >= {need}) -> hand off to hindi_ocr")
print("hindi_ocr then decides per page: no text layer -> OCR; legacy font -> decode; English -> skip (digital_pdf's job)")

# ---- 3-4. tool call and result -------------------------------------------------------------------------------
agent = HindiOCRAgent(); recs = agent.extract(a.pdf, a.source_name)
rows = [r for r in recs if r["page_no"] == a.page]
decoded = [ln.strip() if line_is_english(ln) else convert_line(ln, frozenset()) for ln in raw_lines]
by_line = {}
for r in rows: by_line.setdefault(r["row_ref"], []).append(r)
def describe(rs):
    out = []
    for r in rs:
        col = r["table_ref"].split("c")[-1]; who = r["district_raw"] or "Haryana state"
        if "|" in r["indicator"]: out.append(f"col {col}: {r['value_text']}")
        else: out.append(f"{who}: {r['value_text']}")
    r0 = rs[0]; unit = r0["unit"]; crop = f"{r0['crop']} / " if r0["crop"] else ""
    ind = r0["indicator"].split("|")[0].strip()[:40]
    return f"{crop}{ind} | {r0['year']} | {' ; '.join(out)} | unit {unit}"
bar(f"3. TOOL CALL  extract(page {a.page})   4. TOOL RESULT  (raw text-layer line -> decoded Hindi -> stored)")
for i, (raw, dec) in enumerate(zip(raw_lines, decoded), 1):
    if i > a.lines: print(f"  ... ({len(raw_lines) - a.lines} more lines)"); break
    rs = by_line.get(f"line{i}")
    print(f"line {i:>2}  RAW : {raw[:78]}\n         HINDI: {dec[:78]}" + (f"\n         STORED: {describe(rs)}" if rs else ""))

# ---- 5. output ----------------------------------------------------------------------------------------------
bar("5. OUTPUT")
print(f"page {a.page}: {len(rows)} rows stored | whole document: {len(recs)} rows, {len(agent.rejected_rows)} rejected, {len(agent.skipped_pages)} pages skipped")
for r in rows[:10]:
    print(f"  {r['district_raw'] or 'Haryana state':<14} {r['crop'] or '-':<8} {r['year']:<8} {r['indicator'][:34]:<36} {r['value_text']:>8}  {r['unit']}")

# ---- 6. hard case -------------------------------------------------------------------------------------------
bar("6. HARD / ERROR CASE: rows the agent refuses to guess")
for pno, txt in agent.rejected_rows[:4]: print(f"  rejected, page {pno}: {txt}")
print("  why: wrong number of values for the numbered columns, or a value such as '461/10' that is a pair, not one number")
for pno, why in agent.skipped_pages[:3]: print(f"  skipped page {pno}: {why}")

# ---- 7. trace-back ------------------------------------------------------------------------------------------
bar("7. TRACE-BACK of one stored value")
r = next((x for x in rows if x["value_num"] is not None), None)
inv = next((x for x in load(os.path.join(ROOT, "Source_files_Haryana_final.csv")) if "Bhiwani" in x["List of sources"]), {"URLs": "(not in inventory)"})
if r:
    print(f"  value {r['value_text']} ({r['indicator'][:30]}, {r['year']}) -> {os.path.basename(a.pdf)}, PDF page {r['page_no']}, {r['row_ref']}, column {r['table_ref']}\n  source URL: {inv['URLs']}")
if a.db and r and os.path.exists(a.db):
    con = sqlite3.connect(a.db)
    q = con.execute("select district,crop,year_label,indicator,value_num,unit,page_no,url from v_trace where page_no=? and indicator=? and value_num=? limit 1", (r["page_no"], r["indicator"], r["value_num"])).fetchone()
    print("  database v_trace row:", q)

# ---- optional files -----------------------------------------------------------------------------------------
if a.csv:
    with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["page", "line", "column", "district", "crop", "year", "indicator", "value_text", "value_num", "unit", "needs_review"])
        for x in rows: w.writerow([x["page_no"], x["row_ref"], x["table_ref"], x["district_raw"] or "(Haryana state)", x["crop"] or "", x["year"], x["indicator"], x["value_text"], x["value_num"], x["unit"], x.get("needs_review", 0)])
    print("\nwrote", a.csv)
if a.html:
    buf = io.BytesIO(); img.save(buf, "PNG"); b64 = base64.b64encode(buf.getvalue()).decode()
    trs = []
    for i, (raw, dec) in enumerate(zip(raw_lines, decoded), 1):
        rs = by_line.get(f"line{i}")
        trs.append(f"<tr><td class=n>{i}</td><td class=raw>{html.escape(raw)}</td><td class=hi>{html.escape(dec)}</td><td>{html.escape(describe(rs)) if rs else ''}</td></tr>")
    page_html = f"""<!doctype html><meta charset=utf-8><title>Hindi agent output</title><style>
body{{font:14px/1.4 'Nirmala UI','Noto Sans Devanagari',Segoe UI,Arial,sans-serif;margin:16px;color:#222}} h1{{font-size:18px}} .wrap{{display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap}}
img{{max-width:560px;border:1px solid #ccc}} table{{border-collapse:collapse;font-size:13px}} td{{border:1px solid #ddd;padding:3px 6px;vertical-align:top}}
.raw{{font-family:Consolas,monospace;color:#777}} .hi{{font-size:15px}} .n{{color:#999}} .sum{{margin:6px 0 12px}}</style>
<h1>{html.escape(os.path.basename(a.pdf))}: page {a.page}</h1>
<div class=sum>{len(rows)} rows stored from this page &middot; document total {len(recs)} rows, {len(agent.rejected_rows)} rejected (never guessed), {len(agent.skipped_pages)} pages skipped &middot; {legacy_pages} of {len(texts)} pages are legacy-font Hindi</div>
<div class=wrap><div><b>The PDF page</b><br><img src="data:image/png;base64,{b64}"></div>
<div><b>Raw text layer &rarr; decoded Hindi &rarr; stored values</b><table><tr><td>#</td><td>raw (legacy font)</td><td>decoded</td><td>stored</td></tr>{''.join(trs)}</table></div></div>"""
    open(a.html, "w", encoding="utf-8").write(page_html); print("\nwrote", a.html, "(open it in a browser)")
