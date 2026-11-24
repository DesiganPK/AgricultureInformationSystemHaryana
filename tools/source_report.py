"""Per-source breakdown of M14 (completeness) and M15 (validation), and sources that loaded the same file.

    python tools/source_report.py [haryana_full.db]

Writes docs/source_report.csv. The database-wide M14/M15 in eval/phase2_metrics.csv is one number over every row, so a few
large sources (the State Statistical Abstracts: mostly population, schools, roads, with no crop) decide it. This shows where
the score comes from, and which field is missing.
Same file: two sources whose sha256 (logs/automation_log.csv, last run of each) is equal were extracted from identical bytes,
so one of them points to the wrong PDF or duplicates the other."""
import collections, csv, os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, os.path.join(ROOT, "db"))
from checks import ALLOWED_UNITS, YEAR_OK, plausible

FIELDS = ["crop", "location", "time", "unit", "locator"]


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "haryana_full.db")
    if not os.path.exists(db): sys.exit(f"database not found: {db}")
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    q = """select s.name src, a.name agent, o.page_no, o.table_ref, o.row_ref, o.record_id, o.district_id, o.district_raw, o.crop_id, o.year_label,
           o.unit_id, u.canonical unit, o.indicator, o.value_num, o.missing_reason
           from observation o join source s using(source_id) join extraction_run r using(run_id) join agent a using(agent_id) left join unit u using(unit_id)"""
    S = collections.defaultdict(lambda: dict(agent="", rows=0, complete=0, complete_no_crop=0, valid=0, **{f"miss_{k}": 0 for k in FIELDS}))
    for r in con.execute(q):
        s = S[r["src"]]; s["agent"] = r["agent"]; s["rows"] += 1
        ok = dict(crop=r["crop_id"] is not None, location=r["district_id"] is not None or bool(r["district_raw"]), time=bool(r["year_label"]),
                  unit=(r["unit_id"] is not None and r["unit"] != "unspecified") or r["missing_reason"] is not None,
                  locator=r["page_no"] is not None or bool(r["row_ref"]) or bool(r["table_ref"]) or bool(r["record_id"]))
        for k in FIELDS: s[f"miss_{k}"] += not ok[k]
        s["complete"] += all(ok.values())
        s["complete_no_crop"] += all(v for k, v in ok.items() if k != "crop")
        s["valid"] += (r["unit"] in ALLOWED_UNITS) and bool(YEAR_OK.match(r["year_label"] or "")) and plausible(r["indicator"], r["unit"], r["value_num"]) \
                      and (r["district_id"] is not None or r["district_raw"] is None)

    # last logged sha256 of each source name
    sha = {}
    log = os.path.join(ROOT, "logs", "automation_log.csv")
    if os.path.exists(log):
        for r in csv.DictReader(open(log, newline="", encoding="utf-8-sig")):
            if r.get("source_name") and r.get("sha256_8"): sha[r["source_name"]] = r["sha256_8"]

    out = os.path.join(ROOT, "docs", "source_report.csv")
    cols = ["source", "agent", "rows", "m14", "m14_if_crop_optional", "m15"] + [f"miss_{k}" for k in FIELDS] + ["sha256_8"]
    tot = collections.Counter()
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(cols)
        for name, s in sorted(S.items(), key=lambda kv: -kv[1]["rows"]):
            n = s["rows"]; tot.update(rows=n, complete=s["complete"], complete_no_crop=s["complete_no_crop"], valid=s["valid"])
            w.writerow([name, s["agent"], n, round(s["complete"] / n, 4), round(s["complete_no_crop"] / n, 4), round(s["valid"] / n, 4)]
                       + [round(s[f"miss_{k}"] / n, 4) for k in FIELDS] + [sha.get(name, "")])

    n = tot["rows"] or 1
    print(f"{len(S)} sources, {tot['rows']} rows")
    print(f"database M14 {tot['complete'] / n:.3f} | M14 if crop were optional {tot['complete_no_crop'] / n:.3f} | M15 {tot['valid'] / n:.3f}\n")
    groups = collections.defaultdict(lambda: collections.Counter())
    for name, s in S.items():
        g = name.split(" | ")[0].split(":")[0].replace(" - 2024-25 PDF direct link", "")
        groups[g].update(rows=s["rows"], complete=s["complete"], valid=s["valid"], miss_crop=s["miss_crop"], miss_unit=s["miss_unit"])
    print(f"{'source group':<62} {'rows':>8} {'share':>6} {'M14':>6} {'M15':>6} {'no crop':>8} {'no unit':>8}")
    for g, c in sorted(groups.items(), key=lambda kv: -kv[1]["rows"]):
        r = c["rows"]
        print(f"{g[:62]:<62} {r:>8} {r / n:>6.1%} {c['complete'] / r:>6.3f} {c['valid'] / r:>6.3f} {c['miss_crop'] / r:>8.1%} {c['miss_unit'] / r:>8.1%}")

    by_sha = collections.defaultdict(list)
    for name in S:
        if sha.get(name): by_sha[sha[name]].append(name)
    same = [v for v in by_sha.values() if len(v) > 1]
    print("\nsources extracted from the same file (same sha256):" if same else "\nno two sources share a file")
    for v in same: print("  - " + "\n    ".join(sorted(v)))
    print(f"\nper-source detail: {os.path.relpath(out, ROOT)}")


if __name__ == "__main__":
    main()
