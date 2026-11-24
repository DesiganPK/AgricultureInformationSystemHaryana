"""Build docs/source_agent_map.csv from the Phase 1 inventory + the DB (status = what actually ran).
    python tools/make_source_map.py Source_files_Haryana_final.csv haryana_full.db docs/source_agent_map.csv
A database that does not exist yet, or is still empty, is fine: every source is then 'pending'."""
import csv, os, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from haryana_pipeline import route
inv, db, out = sys.argv[1:4]
done = {}
if os.path.exists(db):
    con = sqlite3.connect(db)
    if con.execute("select 1 from sqlite_master where name='extraction_run'").fetchone():
        done = {r[0]: r[1] for r in con.execute(
            "select s.name, count(*) from extraction_run r join source s using(source_id) where r.status='ok' group by 1")}
    con.close()
else:
    print(f"{db} does not exist yet: every source is listed as pending (the batch run creates it)")
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
print(len(rows), "inventory rows,", sum(1 for r in rows if r["List of sources"] in done or any(k.startswith(r["List of sources"] + " | ") for k in done)), "done")