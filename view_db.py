"""Look at a database in the browser.   python view_db.py demo.db [--out view.html] [--per-source 3000] [--no-open]

Writes ONE self-contained HTML file (no server, no extra packages): totals, one line per source / run, and a table of the stored rows
that can be filtered by source, searched, limited to rows flagged for review, and paged. Each row shows where the value came from
(page, table/column, row), so a value can be traced back to the PDF by hand.
Large databases: at most --per-source rows are embedded for each source (the totals are always for the whole database).
Other ways to look:  DB Browser for SQLite (open the .db file),  or  sqlite3 demo.db "select * from v_trace limit 20"."""
import argparse, html, json, os, sqlite3, sys, webbrowser

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgriAgent database: __NAME__</title><style>
:root{--bg:#f6f7f4;--card:#fff;--ink:#1c2420;--mute:#66706a;--line:#dfe3dc;--accent:#2f6b4f;--warn:#a65a00;--warnbg:#fff4e0}
@media (prefers-color-scheme:dark){:root{--bg:#141815;--card:#1d231f;--ink:#e6ebe6;--mute:#98a39b;--line:#2f3832;--accent:#7fc7a2;--warn:#f0b35a;--warnbg:#3a2c12}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 "Segoe UI","Nirmala UI","Noto Sans Devanagari",system-ui,sans-serif}
main{max-width:1500px;margin:0 auto;padding:20px 16px 60px}h1{font-size:20px;margin:0 0 2px}h2{font-size:15px;margin:26px 0 8px}
.sub{color:var(--mute);margin-bottom:16px}.cards{display:flex;flex-wrap:wrap;gap:10px}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:130px}
.card b{display:block;font-size:20px;font-variant-numeric:tabular-nums}.card span{color:var(--mute);font-size:12px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:5px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}td.w{white-space:normal;min-width:220px;max-width:460px}
th{position:sticky;top:0;background:var(--card);font-weight:600;color:var(--mute);font-size:12px}td.n{text-align:right;font-variant-numeric:tabular-nums}
tr.rev td{background:var(--warnbg)}.tag{display:inline-block;padding:0 6px;border-radius:9px;font-size:11px;border:1px solid var(--line);color:var(--mute)}
.tag.r{color:var(--warn);border-color:var(--warn)}.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
select,input[type=search],button{font:inherit;padding:5px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink);max-width:100%}
input[type=search]{min-width:240px;flex:1}button{cursor:pointer}button:disabled{opacity:.4;cursor:default}.mute{color:var(--mute)}.loc{font-family:Consolas,ui-monospace,monospace;font-size:12px}
</style></head><body><main>
<h1>__NAME__</h1><div class="sub">Built __WHEN__ from <span class="loc">__PATH__</span>. Shaded rows are flagged for review (unit not stated, value read by OCR, or a label supplied by the LLM).</div>
<div class="cards" id="cards"></div>
<h2>Sources and runs</h2><div class="wrap"><table id="src"></table></div>
<h2>Stored rows</h2>
<div class="bar"><select id="fsrc"></select><select id="funit"></select><input type="search" id="q" placeholder="Search district, crop, indicator, value, page (e.g. Rice 2011 p37)">
<label><input type="checkbox" id="rev"> flagged for review only</label></div>
<div class="bar"><button id="prev">&larr; Previous</button><span id="pos" class="mute"></span><button id="next">Next &rarr;</button><span id="note" class="mute"></span></div>
<div class="wrap"><table id="obs"></table></div>
<h2 id="th" hidden>Text kept from pages without tables</h2><div class="wrap" id="tw" hidden><table id="txt"></table></div>
</main><script>
const D = __DATA__;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt = n => Number(n).toLocaleString("en-IN");
document.getElementById("cards").innerHTML = D.cards.map(c => `<div class="card"><b>${esc(c[1])}</b><span>${esc(c[0])}</span></div>`).join("");
document.getElementById("src").innerHTML = "<tr><th>#</th><th>Source</th><th>Agent</th><th>Type</th><th>Rows</th><th>For review</th><th>No unit</th><th>Crop set or coded</th><th>Pages</th><th>Last run</th></tr>" +
  D.sources.map(s => `<tr><td class="n">${s.id}</td><td class="w">${esc(s.name)}</td><td>${esc(s.agent)}</td><td>${esc(s.type)}</td><td class="n">${fmt(s.rows)}</td><td class="n">${fmt(s.review)}</td><td class="n">${fmt(s.nounit)}</td><td class="n">${fmt(s.crop)}</td><td class="n">${s.pages ?? ""}</td><td>${esc(s.last)}</td></tr>`).join("");
const C = D.cols, I = Object.fromEntries(C.map((c, i) => [c, i]));
const fs = document.getElementById("fsrc"), fu = document.getElementById("funit");
fs.innerHTML = `<option value="">All sources (${fmt(D.rows.length)} rows shown)</option>` + D.sources.map(s => `<option value="${s.id}">${esc(s.name.slice(0, 80))}</option>`).join("");
const units = [...new Set(D.rows.map(r => r[I.unit] ?? ""))].sort();
fu.innerHTML = `<option value="__any__">Any unit</option>` + units.map(u => `<option value="${esc(u)}">${esc(u || "(none)")}</option>`).join("");
const hay = D.rows.map(r => [r[I.district], r[I.tehsil], r[I.crop], r[I.year], r[I.indicator], r[I.value], r[I.unit], "p" + r[I.page], r[I.table], r[I.row]].join(" ").toLowerCase());
let view = [], page = 0; const PER = 100;
function apply() {
  const s = fs.value, u = fu.value, words = document.getElementById("q").value.toLowerCase().split(/\\s+/).filter(Boolean), rv = document.getElementById("rev").checked;
  view = [];
  for (let i = 0; i < D.rows.length; i++) { const r = D.rows[i];
    if (s && String(r[I.sid]) !== s) continue; if (u !== "__any__" && (r[I.unit] ?? "") !== u) continue; if (rv && !r[I.review]) continue;
    if (words.length && !words.every(w => hay[i].includes(w))) continue; view.push(i); }
  page = 0; draw();
}
function draw() {
  const pages = Math.max(1, Math.ceil(view.length / PER)); page = Math.min(page, pages - 1);
  const head = "<tr><th>Place</th><th>Crop</th><th>Year</th><th>What</th><th>Value</th><th>Unit</th><th>Where in the source</th><th>Source</th><th>Agent</th><th>Check</th></tr>";
  document.getElementById("obs").innerHTML = head + view.slice(page * PER, page * PER + PER).map(i => { const r = D.rows[i];
    const place = [r[I.district], r[I.tehsil]].filter(Boolean).join(" / ") || '<span class="mute">(state or not stated)</span>';
    const where = [r[I.page] != null ? "page " + r[I.page] : "", r[I.table], r[I.row]].filter(Boolean).join(" · ");
    const chk = r[I.review] ? `<span class="tag r">review${r[I.conf] < 1 ? " " + r[I.conf] : ""}</span>` : '<span class="tag">ok</span>';
    return `<tr class="${r[I.review] ? "rev" : ""}"><td>${place.startsWith("<") ? place : esc(place)}</td><td>${esc(r[I.crop])}</td><td>${esc(r[I.year])}</td><td class="w">${esc(r[I.indicator])}</td><td class="n">${esc(r[I.value])}</td><td>${esc(r[I.unit])}</td><td class="loc">${esc(where)}</td><td>#${r[I.sid]}</td><td>${esc(r[I.agent])}</td><td>${chk}</td></tr>`; }).join("");
  document.getElementById("pos").textContent = view.length ? `rows ${fmt(page * PER + 1)}–${fmt(Math.min(view.length, page * PER + PER))} of ${fmt(view.length)}` : "no rows match";
  document.getElementById("prev").disabled = page === 0; document.getElementById("next").disabled = page >= pages - 1;
}
document.getElementById("note").textContent = D.truncated ? `Showing ${fmt(D.rows.length)} of ${fmt(D.total)} rows (at most ${fmt(D.per)} per source); totals above are for the whole database.` : "";
["fsrc", "funit", "rev"].forEach(id => document.getElementById(id).addEventListener("change", apply));
document.getElementById("q").addEventListener("input", apply);
document.getElementById("prev").onclick = () => { page--; draw(); }; document.getElementById("next").onclick = () => { page++; draw(); };
if (D.texts.length) { document.getElementById("th").hidden = false; document.getElementById("tw").hidden = false;
  document.getElementById("txt").innerHTML = "<tr><th>Source</th><th>Page</th><th>Kind</th><th>Text (start)</th></tr>" + D.texts.map(t => `<tr><td>#${t[0]}</td><td>${esc(t[1])}</td><td>${esc(t[2])}</td><td class="w" style="max-width:900px">${esc(t[3])}</td></tr>`).join(""); }
apply();
</script></body></html>"""


def build(db, out=None, per_source=3000):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    total = con.execute("select count(*) from observation").fetchone()[0]
    sources = []
    for s in con.execute("""select s.source_id id, s.name, s.source_type type, count(o.obs_id) rows, coalesce(sum(o.needs_review),0) review,
            sum(case when u.canonical is null or u.canonical='unspecified' then 1 else 0 end) nounit, sum(case when o.crop_id is not null then 1 else 0 end) crop,
            count(distinct o.page_no) pages from source s left join observation o using(source_id) left join unit u using(unit_id) group by s.source_id order by s.source_id"""):
        r = con.execute("select a.name, r.started_at from extraction_run r join agent a using(agent_id) where r.source_id=? order by r.run_id desc limit 1", (s["id"],)).fetchone()
        sources.append(dict(s) | dict(agent=r[0] if r else "", last=(r[1] or "")[:19].replace("T", " ") if r else "", nounit=s["nounit"] if s["rows"] else 0, pages=s["pages"] or None))
    cols = ["sid", "agent", "page", "table", "row", "district", "tehsil", "crop", "year", "indicator", "value", "unit", "conf", "review"]
    rows = []
    for s in sources:
        for r in con.execute("""select o.source_id, a.name, o.page_no, o.table_ref, o.row_ref, coalesce(d.name_en, o.district_raw), o.tehsil_block, c.name_en, o.year_label, o.indicator,
                o.value_text, u.canonical, o.confidence, o.needs_review, o.value_num from observation o join extraction_run r using(run_id) join agent a using(agent_id)
                left join district d using(district_id) left join crop c using(crop_id) left join unit u using(unit_id) where o.source_id=? order by o.obs_id limit ?""", (s["id"], per_source)):
            r = list(r); n = r.pop()                    # value_num: show the number (1634.4), not the printed form (1634-4 in the legacy font)
            if n is not None: r[10] = (f"{n:.6f}".rstrip("0").rstrip(".") or "0") + ("*" if "*" in (r[10] or "") else "")
            rows.append(r)
    texts = [[t[0], t[1], t[2], (t[3] or "")[:400]] for t in con.execute("select source_id, location_ref, topic, content from text_fact order by fact_id limit 300")]
    n_text = con.execute("select count(*) from text_fact").fetchone()[0]
    review = con.execute("select coalesce(sum(needs_review),0) from observation").fetchone()[0]
    nounit = con.execute("select count(*) from observation o left join unit u using(unit_id) where u.canonical is null or u.canonical='unspecified'").fetchone()[0]
    runs = con.execute("select count(*) from extraction_run").fetchone()[0]
    agents = con.execute("select a.name, count(*) from observation o join extraction_run r using(run_id) join agent a using(agent_id) group by 1 order by 2 desc").fetchall()
    con.close()
    cards = [["rows", f"{total:,}"], ["sources", str(len(sources))], ["runs", str(runs)], ["flagged for review", f"{review:,}"], ["rows with no unit", f"{nounit:,}"], ["text records", f"{n_text:,}"]]
    cards += [[f"rows by {a}", f"{n:,}"] for a, n in agents]
    import datetime as dt
    data = dict(cards=cards, sources=sources, cols=cols, rows=rows, texts=texts, total=total, per=per_source, truncated=len(rows) < total)
    page = (PAGE.replace("__NAME__", html.escape(os.path.basename(db))).replace("__PATH__", html.escape(os.path.abspath(db)))
            .replace("__WHEN__", dt.datetime.now().strftime("%Y-%m-%d %H:%M")).replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))
    out = out or os.path.splitext(os.path.abspath(db))[0] + "_view.html"
    with open(out, "w", encoding="utf-8") as f: f.write(page)
    return out


def open_in_browser(path):
    try: webbrowser.open("file://" + os.path.abspath(path))
    except Exception: pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db"); ap.add_argument("--out"); ap.add_argument("--per-source", type=int, default=3000); ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.db): sys.exit(f"no such database: {a.db}")
    out = build(a.db, a.out, a.per_source); print("wrote", out)
    if not a.no_open: open_in_browser(out)
