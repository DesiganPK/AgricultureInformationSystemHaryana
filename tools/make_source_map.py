"""Build docs/source_agent_map.csv from the Phase 1 inventory + the DB (status = what actually ran)."""
import csv, sqlite3, sys
sys.path.insert(0, ".")
from haryana_pipeline import route
inv, db, out = (sys.argv[1:4] + ["Source_files_Haryana_final.csv", "haryana.db", "docs/source_agent_map.csv"][len(sys.argv[1:4]):])   # defaults: inventory, db, output
con = sqlite3.connect(db)
done = {r[0]: r[1] for r in con.execute(
    "select s.name, count(*) from extraction_run r join source s using(source_id) where r.status='ok' group by 1")}
IMPLEMENTED = {"structured_data", "digital_pdf", "hindi_ocr"}
rows = list(csv.DictReader(open(inv, newline="", encoding="utf-8-sig")))
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(["source_name", "url", "modality", "agent", "status", "note"])
    for r in rows:
        n, ag = r["List of sources"], route(r)
        kids = [d for d in done if d != n and d.lower().startswith(n.lower())]      # a run named "<this source> - <one document>" = one document of a multi-document source
        if n in done: st, note = "done", "extraction_run ok; rows in observation"
        elif kids: st, note = "partial", f"{len(kids)} document(s) of this source extracted (e.g. {kids[0][-60:]}); the rest not run"
        elif ag in IMPLEMENTED: st, note = "pending", "agent implemented but this source has not been run yet (flip to done once an ok run exists)"
        else: st, note = "pending", f"{ag} agent not implemented"
        w.writerow([n, r["URLs"], r["Modalities"], ag, st, note])
print(len(rows), "inventory rows")