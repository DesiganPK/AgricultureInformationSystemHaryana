"""Build docs/source_agent_map.csv from the Phase 1 inventory + the DB (status = what actually ran)."""
import csv, os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from haryana_pipeline import route
# usage: python tools/make_source_map.py [INVENTORY_CSV] [DB] [OUT_CSV]   (defaults: the inventory, haryana_full.db, docs/source_agent_map.csv)
args = sys.argv[1:] + [None] * 3
inv = args[0] or os.path.join(ROOT, "Source_files_Haryana_final.csv")
db = args[1] or os.path.join(ROOT, "haryana_full.db")
out = args[2] or os.path.join(ROOT, "docs", "source_agent_map.csv")
if not os.path.exists(db): sys.exit(f"database not found: {db}  (run batch_run.py first, or pass the DB path)")
con = sqlite3.connect(db)
done = {r[0]: r[1] for r in con.execute(
    "select s.name, count(*) from extraction_run r join source s using(source_id) where r.status='ok' group by 1")}
IMPLEMENTED = {"structured_data", "digital_pdf", "hindi_ocr", "web_content"}
rows = list(csv.DictReader(open(inv, newline="", encoding="utf-8-sig")))
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["source_name", "url", "modality", "agent", "status", "note"])
    for r in rows:
        n, ag = r["List of sources"], route(r)
        parts = [k for k in done if k.startswith(n + " | ")]            # a listing page runs once per PDF: "<name> | <title on the page>"
        if n in done: st, note = "done", "extraction_run ok; rows in observation"
        elif parts: st, note = "done", f"listing page: {len(parts)} PDF(s) loaded"
        elif ag in IMPLEMENTED: st, note = "pending", "agent implemented but this source has not been run yet (flip to done once an ok run exists)"
        else: st, note = "pending", f"{ag} agent not implemented"
        w.writerow([n, r["URLs"], r["Modalities"], ag, st, note])
print(len(rows), "inventory rows")