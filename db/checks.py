"""Database metrics straight from SQL.  python db/checks.py [haryana.db]
M14 mandatory-field completeness | M15 validation pass rate | M16 duplicate rate.  The rules are declared in db/dictionary.md and nowhere else.
Strict reading of the protocol: a field counts only if it is filled, or (for the value) coded in missing_reason.  Nothing is waved through."""
import re, sqlite3, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWED_UNITS = {"000 ha", "ha", "000 tonnes", "kg/ha", "Rs/quintal", "mm", "%", "number", "000", "lakh", "km", "sq km", "per sq km", "000 bales", "quintal",
                 "000 litres", "000 nuts", "Nuts/ha", "cm"}
YEAR_OK = re.compile(r"^(?:19|20)\d{2}(?:-\d{2,4})?$|^(?:19|20)\d{2}$")
def plausible(ind, unit, v):
    """Value in a plausible range for its indicator/unit (None = nothing to check)."""
    if v is None: return True
    if ind == "rainfall_departure": return -100 <= v <= 1000
    if unit == "%": return -100 <= v <= 1000
    if unit == "mm": return 0 <= v <= 6000
    if unit == "Rs/quintal": return 0 <= v <= 100000
    if unit == "kg/ha": return 0 <= v <= 100000
    return v >= 0
def run(db):
    con = sqlite3.connect(db); con.row_factory = sqlite3.Row
    n = con.execute("select count(*) from observation").fetchone()[0]
    rows = con.execute("""select o.obs_id, o.run_id, o.source_id, s.source_type, a.name agent, o.page_no, o.table_ref, o.row_ref, o.record_id, o.district_id, o.district_raw,
        o.crop_id, o.year_label, o.unit_id, u.canonical unit, o.indicator, o.value_num, o.missing_reason
        from observation o join source s using(source_id) join extraction_run r using(run_id) join agent a using(agent_id) left join unit u using(unit_id)""")
    f = dict(source=0, source_type=0, agent=0, run_id=0, locator=0, crop=0, location=0, time=0, unit=0); complete = 0; valid = 0
    for r in rows:
        ok = dict(source=r["source_id"] is not None, source_type=bool(r["source_type"]), agent=bool(r["agent"]), run_id=r["run_id"] is not None,
                  locator=r["page_no"] is not None or bool(r["row_ref"]) or bool(r["table_ref"]) or bool(r["record_id"]),
                  crop=r["crop_id"] is not None, location=r["district_id"] is not None or bool(r["district_raw"]),
                  time=bool(r["year_label"]), unit=(r["unit_id"] is not None and r["unit"] != "unspecified") or r["missing_reason"] is not None)   # 'unspecified' is a placeholder, not a unit
        for k, v in ok.items(): f[k] += v
        complete += all(ok.values())
        v_ok = (r["unit"] in ALLOWED_UNITS) and bool(YEAR_OK.match(r["year_label"] or "")) and plausible(r["indicator"], r["unit"], r["value_num"]) \
               and (r["district_id"] is not None or r["district_raw"] is None)          # a named place must resolve to a canonical district; no place = state level
        valid += v_ok
    dup = con.execute("""select coalesce(sum(c-1),0) from (select count(*) c from observation group by source_id, coalesce(page_no,-1), coalesce(table_ref,''), coalesce(row_ref,''),
        coalesce(crop_id,-1), coalesce(district_id,-1), coalesce(district_raw,''), coalesce(tehsil_block,''), coalesce(season,''), coalesce(year_label,''), indicator, coalesce(as_of,'') having c>1)""").fetchone()[0]
    return dict(rows=n, m14=complete / n, m14_by_field={k: v / n for k, v in f.items()}, m15=valid / n, m16=dup / n, complete=complete, valid=valid, dup=dup)
if __name__ == "__main__":
    r = run(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "haryana.db"))
    print(f"rows {r['rows']}\nM14 mandatory-field completeness {r['m14']:.3f} ({r['complete']}/{r['rows']})\n  by field: " + ", ".join(f"{k} {v:.3f}" for k, v in r["m14_by_field"].items()))
    print(f"M15 validation pass rate {r['m15']:.3f} ({r['valid']}/{r['rows']})\nM16 duplicate rate {r['m16']:.4f} ({r['dup']}/{r['rows']})")
