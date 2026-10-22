"""Copy the spelling aliases from district_names.py into the district_alias table (the schema has the table; the agents resolve names in code).  python db/fill_aliases.py [haryana.db]"""
import sqlite3, sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
from district_names import ALIASES, DISTRICTS
con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "haryana.db")); n = 0
ids = dict(con.execute("select name_en, district_id from district"))
for alias, canon in ALIASES.items():
    if canon in ids: n += con.execute("insert or ignore into district_alias(alias,district_id) values(?,?)", (alias, ids[canon])).rowcount
con.commit(); print("aliases inserted:", n, "| total:", con.execute("select count(*) from district_alias").fetchone()[0])
