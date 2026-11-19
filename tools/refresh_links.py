"""Refresh the file links in the inventory from the live listing pages, before a batch run or whenever downloads start failing.

    python tools/refresh_links.py                  # show what would change
    python tools/refresh_links.py --save           # write fresh URLs into Source_files_Haryana_final.csv (a .bak copy is kept)
    python tools/refresh_links.py --save --add-missing   # also add districts that the listing page has and the CSV lacks
Then:  python tools/make_source_map.py Source_files_Haryana_final.csv haryana.db docs/source_agent_map.csv
       python batch_run.py --db haryana.db
Listing pages read: https://esaharyana.gov.in/district-statistical-abstracts-district-wise/ (and the State Statistical Abstract
listing for the "PDF direct link" row). Every other source keeps its URL."""
import argparse, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
import sources_live as sl, refresh_links_lib as rl
from haryana_pipeline import load

ap = argparse.ArgumentParser(); ap.add_argument("--csv", default=os.path.join(ROOT, "Source_files_Haryana_final.csv"))
ap.add_argument("--save", action="store_true"); ap.add_argument("--add-missing", action="store_true"); a = ap.parse_args()
rows = load(a.csv)
changes, missing, extra = sl.refresh(rows)
print(f"{len(changes)} URL(s) changed, {len(missing)} district(s) not found on the page, {len(extra)} district(s) on the page but not in the CSV")
for n, old, new in changes: print(f"  changed  {n}\n           {old}\n        -> {new}")
for n in missing: print(f"  missing  {n} (kept its CSV URL)")
for d, u in extra: print(f"  new      {d}: {u}")
for r in rows:
    if sl.is_listing_url(r["URLs"]) and "HTML" not in r["Modalities"]:
        jobs = sl.expand(r["List of sources"], r["URLs"])
        print(f"  listing  {r['List of sources'][:70]}: {len(jobs)} PDF(s) on the page (batch_run.py runs each)")
if a.save:
    if changes: rl.write_urls(a.csv, {n: new for n, _, new in changes})
    if a.add_missing and extra: print("  added:", ", ".join(rl.add_districts(a.csv, extra)))
    print("saved to", a.csv, "(previous version kept as .bak)")
elif changes or extra: print("dry run: add --save to write these into the CSV")
