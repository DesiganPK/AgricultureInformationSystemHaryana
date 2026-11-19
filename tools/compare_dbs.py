"""What did the LLM change?  Compare two databases built from the same file (one with --llm off, one with the model on).
Usage:  python tools/compare_dbs.py rules.db llm.db [--csv changes.csv]
Rows are matched by source, page, table, row, place and year. Values are never changed by the model, so only labels are compared:
crop, unit and indicator. Every changed row is listed in the CSV so you can check the model's labels against the PDF."""
import argparse, collections, csv, sqlite3, sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

Q = """select s.url, o.page_no, o.table_ref, o.row_ref, coalesce(o.district_raw,''), coalesce(o.tehsil_block,''), coalesce(o.year_label,''),
              coalesce(c.name_en,''), coalesce(u.canonical,'unspecified'), o.indicator, o.value_text, o.value_num
       from observation o join source s using(source_id) left join crop c using(crop_id) left join unit u using(unit_id)"""


def rows(db):
    out = {}
    for r in sqlite3.connect(db).execute(Q):
        out[r[:7]] = dict(crop=r[7], unit=r[8], indicator=r[9], value=r[10], num=r[11])
    return out


ap = argparse.ArgumentParser(); ap.add_argument("a", help="rules-only database"); ap.add_argument("b", help="database built with the LLM on")
ap.add_argument("--csv", default="llm_changes.csv"); a = ap.parse_args()
A, B = rows(a.a), rows(a.b)
both = A.keys() & B.keys()
print(f"{a.a}: {len(A)} rows | {a.b}: {len(B)} rows | matched: {len(both)} | only in first: {len(A.keys() - B.keys())} | only in second: {len(B.keys() - A.keys())}")
if not both: sys.exit("no rows matched: were both databases built from the same file with the same version of the project?")
changed, kinds, value_diff = [], collections.Counter(), 0
for k in sorted(both, key=lambda k: (k[0], k[1] or 0, k[2] or "", k[3] or "")):
    x, y = A[k], B[k]
    if (x["num"], x["value"]) != (y["num"], y["value"]): value_diff += 1
    d = [f for f in ("crop", "unit", "indicator") if x[f] != y[f]]
    if d:
        for f in d:
            kind = f"{f}: " + ("filled a gap" if x[f] in ("", "unspecified", "(not crop-specific)") else "changed a rule label" if y[f] not in ("", "unspecified") else "removed a label")
            kinds[kind] += 1
        changed.append((k, x, y, d))
print(f"values different: {value_diff} (should be 0: the model never supplies numbers)")
print(f"rows with a different label: {len(changed)}")
for kind, n in kinds.most_common(): print(f"  {kind}: {n}")
print("\nexamples (page | column | first db -> second db):")
seen = set()
for k, x, y, d in changed:
    sig = (k[2], tuple((f, x[f], y[f]) for f in d))
    if sig in seen: continue
    seen.add(sig)
    print(f"  p{k[1]} {str(k[2])[:28]:<28} " + "; ".join(f"{f}: {x[f] or '-'} -> {y[f] or '-'}" for f in d))
    if len(seen) >= 15: break
with open(a.csv, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["page", "table_ref", "row_ref", "place", "year", "value", "crop_rules", "crop_llm", "unit_rules", "unit_llm", "indicator_rules", "indicator_llm"])
    for k, x, y, d in changed: w.writerow([k[1], k[2], k[3], k[4], k[6], y["value"], x["crop"], y["crop"], x["unit"], y["unit"], x["indicator"], y["indicator"]])
print(f"\nall {len(changed)} changed rows written to {a.csv} (open in Excel; check a few against the PDF page)")
