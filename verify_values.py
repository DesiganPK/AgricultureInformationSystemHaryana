"""Spot-check stored values against the original Excel cell (direct value check for the evaluation).
Usage: python verify_values.py [n_samples]   (default 200; uses haryana.db and the cached file in data/raw)"""
import sqlite3, random, sys, glob, os
import pandas as pd
from structured_agent import read_raw, _s, _num

def verify(db, xlsx, n=200, seed=1):
    raw = read_raw(xlsx)
    code_row = next(i for i in range(25) if "STNAME" in [_s(v).upper() for v in raw.iloc[i]])
    codes = [_s(v).upper() for v in raw.iloc[code_row]]
    dcol, ycol = codes.index("DISTNAME"), codes.index("YEAR")
    con = sqlite3.connect(db)
    rows = con.execute("""select obs_id, table_ref, row_ref, district_raw, year_label, value_num, missing_reason
                          from observation""").fetchall()
    random.Random(seed).shuffle(rows); sample = rows[:n]; fails = []
    for obs_id, tref, rref, dist, year, val, miss in sample:
        r, c = int(rref[1:]) - 1, codes.index(tref.split(":", 1)[1].upper())
        cell = _s(raw.iat[r, c]); exp = _num(cell)
        ok = (_s(raw.iat[r, dcol]) == dist and str(int(float(raw.iat[r, ycol]))) == year
              and ((exp is None and val is None and miss) or (exp is not None and val is not None and abs(exp - val) < 1e-9)))
        if not ok: fails.append((obs_id, tref, rref, dist, year, val, cell))
    print(f"checked {len(sample)} of {len(rows)} stored values: {len(sample)-len(fails)} match, {len(fails)} mismatch "
          f"({(len(sample)-len(fails))/max(len(sample),1):.1%})")
    for f in fails[:10]: print("MISMATCH:", f)
    return len(sample), len(fails)

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    files = [f for f in glob.glob(os.path.join("data", "raw", "*area*production*"))] or glob.glob(os.path.join("data", "raw", "*"))
    verify("haryana.db", files[0], n)
