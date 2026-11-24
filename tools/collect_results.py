"""Bundle the results of your runs into one small zip to send back for improving the report, slides, poster and manual.

    python tools/collect_results.py --db haryana_full.db
    python tools/collect_results.py --db haryana_full.db --rules-db rules.db --llm-db llm.db    # also compare a rules-only and a model run

Writes results_bundle_<date>.zip in the project folder. It holds only small text files, never the database itself:
  summary.txt                 per-source rows, rejected rows, review flags, units missing; M14/M15/M16; run and model-call totals
  per_source.csv              the same per source, one row each
  checks.txt                  output of db/checks.py
  compare.txt                 output of tools/compare_dbs.py (only with --rules-db and --llm-db)
  automation_log.csv          one row per run.py run (status, rows, errors, model calls, seconds)
  llm_calls_summary.csv       model calls per task: count, valid JSON, mean seconds (the raw log is not included)
  failures.csv                every run that did not end OK, with its error
  source_agent_map.csv, phase2_metrics.csv, case_results.csv, reference.csv, adapter_comparison.csv  (whichever exist)
  git_log.txt                 recent commits, so it is clear which code produced these results
Nothing is uploaded anywhere; you choose whether to send the zip."""
import argparse, collections, csv, datetime as dt, io, json, os, sqlite3, subprocess, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPY = {"docs/source_agent_map.csv": "source_agent_map.csv", "eval/phase2_metrics.csv": "phase2_metrics.csv",
        "eval/results/case_results.csv": "case_results.csv", "eval/reference/reference.csv": "reference.csv",
        "eval/adapter_comparison.csv": "adapter_comparison.csv", "eval/results/adapter_cases.csv": "adapter_cases.csv",
        "logs/automation_log.csv": "automation_log.csv", "Source_files_Haryana_final.csv": "Source_files_Haryana_final.csv"}


def run(cmd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")   # Hindi text in the output: never decode it as cp1252 on Windows
    try: r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
    except Exception as e: return f"could not run {' '.join(cmd)}: {type(e).__name__}: {e}"
    return ((r.stdout or "") + ("\n" + (r.stderr or "") if r.returncode else "")).strip()


def per_source(db):
    con = sqlite3.connect(db)
    q = """select s.source_id, s.name, coalesce(a.name,''), count(o.obs_id),
                  coalesce(sum(o.needs_review),0), coalesce(sum(o.value_num is null),0),
                  coalesce(sum(u.canonical is null or u.canonical='unspecified'),0), count(distinct o.page_no), s.url
           from source s left join observation o using(source_id) left join unit u using(unit_id)
           left join extraction_run r on r.run_id = (select max(run_id) from extraction_run x where x.source_id = s.source_id)
           left join agent a on a.agent_id = r.agent_id group by s.source_id order by s.source_id"""
    rows = con.execute(q).fetchall()
    text = con.execute("select source_id, count(*) from text_fact group by 1").fetchall() if con.execute(
        "select 1 from sqlite_master where name='text_fact'").fetchone() else []
    tf = dict(text); con.close()
    return [dict(source_id=r[0], source=r[1], agent=r[2], rows=r[3], flagged_for_review=r[4], coded_missing=r[5],
                 no_unit=r[6], pages_with_rows=r[7], text_records=tf.get(r[0], 0), url=r[8]) for r in rows]


def llm_summary(path):
    if not os.path.exists(path): return []
    agg = collections.defaultdict(lambda: [0, 0, 0.0, 0, collections.Counter()])
    with open(path, encoding="utf-8") as f:
        for line in f:
            try: d = json.loads(line)
            except ValueError: continue
            a = agg[(d.get("task", ""), d.get("backend", ""), d.get("adapter", ""))]
            a[0] += 1; a[1] += bool(d.get("valid_json")); a[2] += float(d.get("seconds") or 0); a[3] += bool(d.get("error"))
            if d.get("error") or not d.get("valid_json"): a[4][str(d.get("error") or "no valid JSON: " + str(d.get("output", ""))[:120])[:200]] += 1
    return [dict(task=t, backend=b, adapter=ad, calls=n, valid_json=v, errors=e, mean_seconds=round(s / n, 2),
                 top_problems=" || ".join(f"{c}x {m}" for m, c in why.most_common(3)))
            for (t, b, ad), (n, v, s, e, why) in sorted(agg.items())]


def csv_text(rows):
    if not rows: return ""
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows); return buf.getvalue()


def main():
    try: sys.stdout.reconfigure(errors="replace")                           # a Windows console that cannot show a character prints ? instead of failing
    except AttributeError: pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="the database you built (for example haryana_full.db)")
    ap.add_argument("--rules-db"); ap.add_argument("--llm-db")
    ap.add_argument("--out"); a = ap.parse_args()
    db = a.db if os.path.isabs(a.db) else os.path.join(ROOT, a.db)
    if not os.path.exists(db): sys.exit(f"database not found: {db}")
    out = a.out or os.path.join(ROOT, f"results_bundle_{dt.date.today():%Y%m%d}.zip")
    files = {}

    src = per_source(db)
    files["per_source.csv"] = csv_text(src)
    checks = run([sys.executable, "db/checks.py", db]); files["checks.txt"] = checks
    log_rows = list(csv.DictReader(open(os.path.join(ROOT, "logs/automation_log.csv"), encoding="utf-8-sig"))) \
        if os.path.exists(os.path.join(ROOT, "logs/automation_log.csv")) else []
    fails = [{k: r.get(k, "") for k in ("timestamp_utc", "input_file", "source_name", "routed_agent", "final_agent", "status", "error")}
             for r in log_rows if r.get("status") != "ok"]
    files["failures.csv"] = csv_text(fails) or "no failed runs in the log\n"
    by_hash = collections.defaultdict(set)                 # the same file loaded under different source names = a download or naming bug
    latest = {}                                            # only each source's latest ok run: older runs (before a fix, other databases) are history
    for r in log_rows:
        if r.get("status") == "ok" and r.get("sha256_8"): latest[r.get("source_name", "")] = r["sha256_8"]
    in_db = {s["source"] for s in src}
    for name, h in latest.items():
        if name in in_db: by_hash[h].add(name)
    dups = [dict(sha256_8=h, sources=len(n), names=" || ".join(sorted(n))[:2000]) for h, n in by_hash.items() if len(n) > 1]
    files["duplicate_files.csv"] = csv_text(dups) or "no file was loaded under more than one source name\n"
    llm = llm_summary(os.path.join(ROOT, "logs/llm_calls.jsonl")); files["llm_calls_summary.csv"] = csv_text(llm) or "no model calls logged\n"
    if a.rules_db and a.llm_db:
        files["compare.txt"] = run([sys.executable, "tools/compare_dbs.py", a.rules_db, a.llm_db, "--csv",
                                    os.path.join(ROOT, "llm_changes.csv")])
        if os.path.exists(os.path.join(ROOT, "llm_changes.csv")): COPY["llm_changes.csv"] = "llm_changes.csv"
    for rel, name in COPY.items():
        p = os.path.join(ROOT, rel)
        if os.path.exists(p) and os.path.getsize(p) < 20_000_000: files[name] = open(p, encoding="utf-8-sig", errors="replace").read()
    files["git_log.txt"] = run(["git", "log", "--oneline", "-n", "20"])

    tot = lambda k: sum(s[k] for s in src)
    lines = [f"AgriAgent results bundle, {dt.datetime.now():%Y-%m-%d %H:%M}", f"database: {os.path.basename(db)} ({os.path.getsize(db) // 1_000_000} MB)", "",
             f"sources in database: {len(src)}   with rows: {sum(s['rows'] > 0 for s in src)}   with text only: {sum(s['rows'] == 0 and s['text_records'] > 0 for s in src)}",
             f"rows: {tot('rows')}   flagged for review: {tot('flagged_for_review')}   coded missing: {tot('coded_missing')}   no unit: {tot('no_unit')}", "",
             "quality (db/checks.py):", *["  " + l for l in checks.splitlines()[-6:]], "",
             f"WARNING: {sum(d['sources'] for d in dups)} runs loaded only {len(dups)} distinct files under different names (see duplicate_files.csv)" if dups else "duplicate files: none",
             f"runs logged: {len(log_rows)}   ok: {sum(r.get('status') == 'ok' for r in log_rows)}   failed: {len(fails)}",
             f"model calls logged: {sum(r['calls'] for r in llm)}   valid JSON: {sum(r['valid_json'] for r in llm)}", "",
             "rows by agent:"]
    by_agent = collections.Counter(); [by_agent.__setitem__(s["agent"], by_agent[s["agent"]] + s["rows"]) for s in src]
    lines += [f"  {k or '(no run)'}: {v}" for k, v in by_agent.most_common()]
    lines += ["", "sources with 0 rows:"] + [f"  {s['source'][:90]}" for s in src if s["rows"] == 0] + [""]
    lines += ["included files: " + ", ".join(sorted(files))]
    files["summary.txt"] = "\n".join(lines) + "\n"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in sorted(files.items()): z.writestr(name, text)
    print(files["summary.txt"]); print(f"wrote {out} ({os.path.getsize(out) // 1024} KB). Send this zip back to update the deliverables.")


if __name__ == "__main__": main()
