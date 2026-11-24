"""Collect every number the Phase 3 report, poster and slides need into one small zip.

    python tools/export_for_report.py [haryana_full.db]

Writes phase3_export.zip in the project folder (a few MB at most; the database itself is not included).
Inside: db_stats.json (rows by agent / source / district / year / indicator, text pages, coverage), the per-source report,
the metrics file, the source map, the inventory, the manifest, the evaluation references, the adapter comparison and the logs."""
import collections, csv, io, json, os, sqlite3, subprocess, sys, zipfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def q(con, sql, *a):
    return [list(r) for r in con.execute(sql, a)]


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "haryana_full.db")
    if not os.path.exists(db): sys.exit(f"database not found: {db}")
    print("1/3 per-source report ..."); subprocess.run([sys.executable, os.path.join(ROOT, "tools", "source_report.py"), db], check=False)
    con = sqlite3.connect(db)
    print("2/3 database statistics ...")
    v = "from observation o join source s using(source_id) join extraction_run r using(run_id) join agent a using(agent_id)"
    stats = dict(
        db_file=os.path.basename(db), db_size_mb=round(os.path.getsize(db) / 1e6, 1),
        observations=q(con, "select count(*) from observation")[0][0],
        text_facts=q(con, "select count(*) from text_fact")[0][0],
        sources=q(con, "select count(*) from source")[0][0],
        runs_by_status=q(con, "select status, count(*) from extraction_run group by 1"),
        rows_by_agent=q(con, f"select a.name, count(*), count(distinct o.source_id) {v} group by 1 order by 2 desc"),
        rows_by_source=q(con, f"select s.name, s.source_type, s.language, a.name, count(*), sum(o.needs_review), min(o.year_label), max(o.year_label) {v} group by s.source_id order by 5 desc"),
        text_by_source=q(con, "select s.name, count(*) from text_fact t join source s using(source_id) group by 1 order by 2 desc"),
        rows_by_district=q(con, "select coalesce(d.name_en, '(state level / unresolved)'), count(*) from observation o left join district d using(district_id) group by 1 order by 2 desc"),
        districts_in_reference=q(con, "select count(*) from district")[0][0],
        districts_covered=q(con, "select count(distinct district_id) from observation where district_id is not null")[0][0],
        rows_by_indicator=q(con, "select indicator, count(*) from observation group by 1 order by 2 desc limit 60"),
        rows_by_year=q(con, "select year_label, count(*) from observation group by 1 order by 1"),
        crops=q(con, "select c.name_en, count(*) from observation o join crop c using(crop_id) group by 1 order by 2 desc"),
        units=q(con, "select coalesce(u.canonical, '(none)'), count(*) from observation o left join unit u using(unit_id) group by 1 order by 2 desc limit 40"),
        missing_reasons=q(con, "select coalesce(missing_reason, '(value present)'), count(*) from observation group by 1"),
        needs_review=q(con, "select needs_review, count(*) from observation group by 1"),
        sample_trace=q(con, "select * from v_trace where district is not null and crop is not null and value_num is not null order by random() limit 25"),
    )
    con.close()
    print("3/3 packing ...")
    files = ["docs/source_report.csv", "eval/phase2_metrics.csv", "eval/results/case_results.csv", "eval/adapter_comparison.csv",
             "eval/cases/cases.csv", "eval/reference/reference.csv", "docs/source_agent_map.csv", "Source_files_Haryana_final.csv",
             "data/manifest.csv", "logs/automation_log.csv", "logs/llm_calls.jsonl", "finetune/split_manifest.json"]
    out = os.path.join(ROOT, "phase3_export.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("db_stats.json", json.dumps(stats, indent=1, ensure_ascii=False, default=str))
        for f in files:
            p = os.path.join(ROOT, f)
            if os.path.exists(p) and os.path.getsize(p) < 30e6: z.write(p, f)
            else: print(f"  skipped (missing or too large): {f}")
    print(f"\nwrote {out} ({os.path.getsize(out) / 1e6:.1f} MB): upload this file in the chat")


if __name__ == "__main__":
    main()
