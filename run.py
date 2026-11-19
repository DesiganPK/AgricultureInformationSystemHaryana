"""One-command entry point:  any PDF / Excel / CSV / saved web page (.html)  ->  rows in a SQLite database.

    python run.py --input <file or URL> [--db demo.db] [--view] [--llm off|auto|ollama|hf|openai] [--source-name NAME] [--url URL]

What happens:
  1. the file is profiled (type, pages with a text layer, legacy-font Hindi pages)
  2. the supervisor picks an agent: fixed rules, or the LLM when one is switched on (its pick is checked; agents can still hand over)
  3. the agent reads the tables; rules + (optionally) the LLM decide what each column means; numbers are copied by code
  4. rows go into the database with page / row / column kept, and one line is appended to logs/automation_log.csv
  5. --view writes <db>_view.html (a browsable page of what is in the database) and opens it
No code or configuration is edited during a run; if you must change something by hand, pass --manual-steps N so the log counts it.
Examples:
  python run.py --input data/raw/Bhiwani.pdf --db demo.db --view
  python run.py --input any_unseen.pdf --db demo.db --llm ollama --view
  python run.py --input data/raw/FHP_HARYANA_98-2001.pdf --source-name "Farm Harvest Prices" --db demo.db"""
import argparse, csv, datetime as dt, hashlib, os, platform, sqlite3, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)

LOG_COLS = ["timestamp_utc", "input_file", "sha256_8", "size_kb", "source_name", "detected_type", "routed_agent", "final_agent", "reroutes",
            "attempts", "status", "rows_inserted", "rejected_rows", "skipped_pages", "manual_interventions", "error", "environment", "notes",
            "router", "router_reason", "llm", "llm_calls", "llm_valid_json", "llm_retries", "llm_accepted", "llm_rejected", "llm_seconds", "llm_tokens",
            "text_pages", "review_rows", "seconds"]
INVENTORY = os.path.join(HERE, "Source_files_Haryana_final.csv")


def detect(path):
    from structured_agent import sniff
    with open(path, "rb") as f: head = f.read(8)
    if head.startswith(b"%PDF"): return "pdf"
    if path.lower().endswith((".html", ".htm")): return "html"        # a saved web page; an HTML error page saved as .pdf / .xlsx is still refused below
    return sniff(path)                              # xlsx / xls / csv, or ValueError for an HTML error page


def source_row(path, kind, name, url):
    """Prefer the Phase 1 inventory row for this file (keeps the real URL as provenance); otherwise build a generic row."""
    from haryana_pipeline import load
    base = os.path.basename(path).lower()
    if os.path.exists(INVENTORY):
        rows = load(INVENTORY)
        by_name = [r for r in rows if name and name.lower() in r["List of sources"].lower()]
        by_file = [r for r in rows if base and base in r["URLs"].lower()]
        hit = by_name[0] if len(by_name) == 1 else (by_file[0] if len(by_file) == 1 else None)      # ambiguous names fall through to a generic row
        if hit:
            hit = dict(hit); hit["match_by_name"] = True              # the same inventory source even if its file URL has changed
            if url: hit["URLs"] = url                                 # a fresher URL (from the live listing page) wins over the CSV
            return hit
    modality = {"pdf": "PDF", "xlsx": "XLSX (Excel)", "xls": "XLSX (Excel)", "csv": "CSV", "html": "HTML"}[kind]
    return {"Team Name": "Team 2", "State": "Haryana", "List of sources": name or os.path.basename(path), "match_by_name": bool(name and url),
            "URLs": url or file_id(path), "Period Of Interest": None, "Modalities": modality, "Limitations": "", "Extracted Knowledge": ""}


def file_id(path):
    """Source identifier for a local file with no URL: its name plus a hash of its bytes, so the same file run from two folders is one source
    (and the duplicate check then stops it being loaded twice), while two different files with the same name stay separate."""
    import hashlib
    with open(path, "rb") as f: h = hashlib.sha256(f.read()).hexdigest()[:16]
    return f"file://{os.path.basename(path)}#sha256={h}"


def write_log(path, row):
    """Append one row; if the file was written by an older version with fewer columns, rewrite it once with the new header."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    old = []
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            if rd.fieldnames != LOG_COLS: old = list(rd)
            else: old = None
    if old is None:
        with open(path, "a", newline="", encoding="utf-8-sig") as f: csv.DictWriter(f, LOG_COLS).writerow({k: row.get(k, "") for k in LOG_COLS})
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, LOG_COLS); w.writeheader()
        for r in old + [row]: w.writerow({k: r.get(k, "") for k in LOG_COLS})


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True); ap.add_argument("--source-name"); ap.add_argument("--url")
    ap.add_argument("--db", default=os.path.join(HERE, "haryana.db")); ap.add_argument("--log", default=os.path.join(HERE, "logs", "automation_log.csv"))
    ap.add_argument("--llm", choices=["auto", "off", "ollama", "hf", "openai"], help="LLM backend (default: AGRI_LLM, else auto = Ollama if it is running, otherwise rules only)")
    ap.add_argument("--model", help="model name for the backend (default qwen2.5:1.5b for Ollama, Qwen/Qwen2.5-1.5B-Instruct for hf)")
    ap.add_argument("--adapter", help="folder of a fine-tuned LoRA adapter (hf backend)")
    ap.add_argument("--llm-max-calls", type=int, help="most model calls for this file (default 60); the rest of the file runs on rules. On a CPU each call can take 10 to 60 s")
    ap.add_argument("--policy", choices=["fill", "always", "prefer"], help="fill: ask the LLM only where rules leave a gap (default); always: ask for every table; prefer: LLM overrides rules")
    ap.add_argument("--view", action="store_true", help="write <db>_view.html after the run and open it in the browser")
    ap.add_argument("--manual-steps", type=int, default=0, help="hand edits you had to make for this input (counts against the automation rate)")
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    for flag, env in ((a.llm, "AGRI_LLM"), (a.model, "AGRI_LLM_MODEL"), (a.adapter, "AGRI_LLM_ADAPTER"), (a.policy, "AGRI_LLM_POLICY"), (None if a.llm_max_calls is None else str(a.llm_max_calls), "AGRI_LLM_MAX_CALLS")):
        if flag: os.environ[env] = flag
    import llm, doc_context as dc
    from haryana_pipeline import Supervisor, AGENTS
    from structured_agent import StructuredDataAgent
    from pdf_agent import DigitalPDFAgent
    from hindi_agent import HindiOCRAgent
    from web_agent import WebContentAgent
    llm.reset(); t0 = time.time()
    row = dict(timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), input_file=a.input, manual_interventions=a.manual_steps,
               environment=f"python {platform.python_version()} / {platform.system()}", notes=a.notes, source_name=a.source_name or "", llm=llm.describe())
    status = "failed"; code = 1
    print(llm.describe())
    try:
        if a.input.lower().startswith(("http://", "https://")):                 # a URL: download it once into data/raw, keep the URL as provenance
            a.url = a.url or a.input; got = StructuredDataAgent().fetch(a.input)
            with open(got, "rb") as f: first = f.read(600).lstrip().lower()
            if (first.startswith(b"<!doctype html") or first.startswith(b"<html")) and not got.lower().endswith((".html", ".htm")):
                os.replace(got, got + ".html"); got += ".html"
            a.input = got
        if not os.path.exists(a.input): raise FileNotFoundError(a.input)
        raw = open(a.input, "rb").read(); row["sha256_8"] = hashlib.sha256(raw).hexdigest()[:8]; row["size_kb"] = len(raw) // 1024
        kind = detect(a.input); row["detected_type"] = kind
        src = source_row(a.input, kind, a.source_name, a.url); src["local_file"] = os.path.abspath(a.input); row["source_name"] = src["List of sources"]
        profile = dc.profile_pdf(a.input) if kind == "pdf" else dict(kind=kind, file=os.path.basename(a.input))
        decision = dc.choose_agent(profile)                                    # the supervisor's decision
        row["router"], row["router_reason"] = decision["by"], decision["reason"][:200]
        print(f"supervisor -> {decision['agent']}  (decided by {decision['by']}: {decision['reason']})")
        agents = dict(AGENTS)
        agents["structured_data"] = StructuredDataAgent(db_path=a.db); agents["digital_pdf"] = DigitalPDFAgent(db_path=a.db); agents["hindi_ocr"] = HindiOCRAgent(db_path=a.db); agents["web_content"] = WebContentAgent(db_path=a.db)
        sup = Supervisor([src], agents); job = sup.jobs[0]; job.agent = decision["agent"]; row["routed_agent"] = job.agent; sup.run()
        for l in job.log: print("  " + l)
        row["final_agent"], row["attempts"] = job.agent, job.attempts; row["reroutes"] = sum(l.startswith("rerouted") for l in job.log)
        ag = agents.get(job.agent); row["rejected_rows"] = len(getattr(ag, "rejected_rows", []) or []); row["skipped_pages"] = len(getattr(ag, "skipped_pages", []) or [])
        status = {"ok": "ok", "pending_implementation": "pending_implementation"}.get(job.status, "failed")
        if status != "ok": row["error"] = (job.error or "; ".join(job.log[-1:]) or job.status)[:300]
        if os.path.exists(a.db):
            con = sqlite3.connect(a.db)
            try:
                q = "from observation o join source s using(source_id) where s.url=?"
                row["rows_inserted"] = con.execute("select count(*) " + q, (src["URLs"],)).fetchone()[0]
                row["review_rows"] = con.execute("select count(*) " + q + " and o.needs_review=1", (src["URLs"],)).fetchone()[0]
                row["text_pages"] = con.execute("select count(*) from text_fact t join source s using(source_id) where s.url=? and (t.topic='page_text' or t.location_ref like 'section%')", (src["URLs"],)).fetchone()[0]
            except sqlite3.Error: pass
            con.close()
        code = 0 if status == "ok" else 1
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"[:300]
    S = llm.STATS
    row.update(status=status, llm_calls=S["calls"], llm_valid_json=S["valid"], llm_retries=S["retries"], llm_accepted=S["accepted"], llm_rejected=S["rejected"],
               llm_seconds=round(S["seconds"], 2), llm_tokens=S["tokens_in"] + S["tokens_out"], seconds=round(time.time() - t0, 1))
    write_log(a.log, row)
    if S["calls"]: print(f"LLM calls {S['calls']} (valid JSON {S['valid']}, retries {S['retries']}, answers passing checks {S['accepted']}, failing {S['rejected']}, {S['seconds']:.1f} s, {row['llm_tokens']} tokens)")
    if S["calls"]: print(f"  column labels: {S['labels_filled']} filled by the model where rules left a gap; where rules had a label the model agreed {S['labels_agree']} times, differed {S['labels_differ']} (rules kept unless --policy prefer)")
    if S["calls"] and not S["valid"] and llm.STATS.get("first_error"):
        print(f"  every model call failed; first error: {llm.STATS['first_error']}\n  (rows above come from the rules alone; full log: logs/llm_calls.jsonl)")
    if row.get("text_pages"): print(f"{row['text_pages']} page(s) / section(s) without table rows: their text was stored in text_fact")
    if a.view and os.path.exists(a.db):
        try:
            import view_db
            out = view_db.build(a.db); print("database view:", out); view_db.open_in_browser(out)
        except Exception as e: print(f"could not build the view ({type(e).__name__}: {e}); run: python view_db.py {a.db}")
    elif status == "ok": print(f"to look at the database: python view_db.py {a.db}")
    print(f"{status.upper()}: {row['source_name']} | agent {row.get('routed_agent', '')}->{row.get('final_agent', '')} | rows {row.get('rows_inserted', '')} | "
          f"rejected {row.get('rejected_rows', '')} | skipped pages {row.get('skipped_pages', '')}" + (f" | {row['error']}" if row.get("error") else ""))
    return code


if __name__ == "__main__": sys.exit(main())
