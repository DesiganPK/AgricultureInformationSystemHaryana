"""Write db/dump/haryana_dump.sql.gz from a database.   python tools/make_dump.py haryana.db
Reload with:  gunzip -c db/dump/haryana_dump.sql.gz | sqlite3 restored.db     (or: python tools/make_dump.py --restore restored.db)"""
import gzip, os, sqlite3, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); OUT = os.path.join(ROOT, "db", "dump", "haryana_dump.sql.gz")
if len(sys.argv) == 3 and sys.argv[1] == "--restore":
    con = sqlite3.connect(sys.argv[2]); con.executescript(gzip.open(OUT, "rt", encoding="utf-8").read()); con.commit()
    print("restored", con.execute("select count(*) from observation").fetchone()[0], "rows into", sys.argv[2])
else:
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "haryana.db"); os.makedirs(os.path.dirname(OUT), exist_ok=True)
    con = sqlite3.connect(db)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        for line in con.iterdump(): f.write(line + "\n")
    print("wrote", OUT, f"({os.path.getsize(OUT) // 1024} KB) from", db, "|", con.execute("select count(*) from observation").fetchone()[0], "rows")
