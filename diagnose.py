"""Explain what one extraction run stored.  Usage: python diagnose.py <run_id>   (run ids: select run_id, source_id from extraction_run)"""
import sqlite3, sys
run = int(sys.argv[1]); con = sqlite3.connect("haryana.db")
q = lambda s, *a: con.execute(s, a).fetchall()
print("source:", q("select s.name from extraction_run r join source s using(source_id) where run_id=?", run))
print("rows per page:", q("select page_no, count(*) from observation where run_id=? group by 1 order by 1", run))
print("distinct districts:", q("select count(distinct district_raw) from observation where run_id=?", run)[0][0],
      "| distinct crops:", q("select count(distinct crop_id) from observation where run_id=? and crop_id is not null", run)[0][0],
      "| distinct indicators:", [r[0] for r in q("select distinct indicator from observation where run_id=?", run)])
print("years:", [r[0] for r in q("select distinct year_label from observation where run_id=? order by 1", run)])
print("districts per page:", q("select page_no, count(distinct district_raw) from observation where run_id=? group by 1 order by 1", run))
print("unresolved names (flagged):", [r[0] for r in q("select distinct district_raw from observation where run_id=? and needs_review=1", run)])
print("first and last district seen on each page:")
for (p,) in q("select distinct page_no from observation where run_id=? order by 1", run):
    names = [r[0] for r in q("select district_raw from observation where run_id=? and page_no=? order by obs_id", run, p)]
    seen = list(dict.fromkeys(names)); print("  page", p, ":", seen[:2], "...", seen[-2:], f"({len(seen)} names)")
