"""Remove the rows of some sources from a database so they can be extracted again with the current agents.
    python tools/clear_rows.py --db haryana.db --agent hindi_ocr          # every source last read by that agent
    python tools/clear_rows.py --db haryana.db --source "Bhiwani"         # sources whose name contains the text
    add --yes to really delete (without it, the script only lists what it would remove)
Why: re-running a source after the agents changed adds the newly labelled rows NEXT TO the old ones (the labels differ, so they are not
seen as duplicates). Clear first, then re-run:  python batch_run.py --db haryana.db --redo
The source rows themselves are kept; observations, text records and runs of the matching sources are deleted. Make a copy of the .db first."""
import argparse, sqlite3
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--db", required=True); ap.add_argument("--agent"); ap.add_argument("--source"); ap.add_argument("--yes", action="store_true")
a = ap.parse_args()
if not (a.agent or a.source): ap.error("give --agent or --source")
con = sqlite3.connect(a.db)
rows = con.execute("""select s.source_id, s.name, (select ag.name from extraction_run r join agent ag using(agent_id) where r.source_id = s.source_id order by r.run_id desc limit 1) agent,
                      (select count(*) from observation o where o.source_id = s.source_id) n from source s""").fetchall()
hit = [r for r in rows if (not a.agent or r[2] == a.agent) and (not a.source or a.source.lower() in r[1].lower())]
for sid, name, agent, n in hit: print(f"  source {sid}: {n:>7} rows | {agent} | {name[:80]}")
print(f"{len(hit)} sources, {sum(r[3] for r in hit)} rows" + ("" if a.yes else "   (nothing deleted; add --yes to delete)"))
if a.yes and hit:
    ids = [r[0] for r in hit]; q = ",".join("?" * len(ids))
    for t in ("observation", "text_fact", "eval_result"):
        try:
            if t == "eval_result": con.execute(f"delete from eval_result where run_id in (select run_id from extraction_run where source_id in ({q}))", ids)
            else: con.execute(f"delete from {t} where source_id in ({q})", ids)
        except sqlite3.Error as e: print("  skipped", t, e)
    con.execute(f"delete from extraction_run where source_id in ({q})", ids); con.commit(); con.execute("vacuum")
    print("deleted. Now re-run those sources (python batch_run.py --db ... --redo, or run.py per file).")
