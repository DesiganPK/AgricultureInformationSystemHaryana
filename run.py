"""One-command entry point.   python run.py --input <file> [--source-name NAME] [--url URL] [--db haryana.db]
Detects what the file is (Excel / CSV / PDF), routes it with the supervisor's fixed rules, lets agents hand off
(digital_pdf -> hindi_ocr for legacy-font or scanned PDFs), loads rows into the database and appends one row to
logs/automation_log.csv.  No code or configuration is edited during a run; if you must change something by hand,
pass --manual-steps N so the log counts it (the protocol treats it as a manual intervention).
Examples:
  python run.py --input data/raw/FHP_HARYANA_98-2001.pdf --source-name "Farm Harvest Prices"
  python run.py --input data/raw/Bhiwani.pdf --source-name "Haryana DESA - District Statistical Abstract: Bhiwani"
  python run.py --input some_unseen.pdf            (name is taken from the file; a Hindi abstract needs the district in --source-name)"""
import argparse, csv, datetime as dt, hashlib, os, platform, sqlite3, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from haryana_pipeline import Supervisor, AGENTS, load, route
from structured_agent import StructuredDataAgent, sniff
from pdf_agent import DigitalPDFAgent
from hindi_agent import HindiOCRAgent

LOG_COLS = ["timestamp_utc", "input_file", "sha256_8", "size_kb", "source_name", "detected_type", "routed_agent", "final_agent", "reroutes",
            "attempts", "status", "rows_inserted", "rejected_rows", "skipped_pages", "manual_interventions", "error", "environment", "notes"]
INVENTORY = os.path.join(HERE, "Source_files_Haryana_final.csv")

def detect(path):
    with open(path, "rb") as f: head = f.read(8)
    if head.startswith(b"%PDF"): return "pdf"
    return sniff(path)                              # xlsx / xls / csv, or ValueError for an HTML error page

def source_row(path, kind, name, url):
    """Prefer the Phase 1 inventory row for this file (keeps the real URL as provenance); otherwise build a generic row."""
    base = os.path.basename(path).lower()
    if os.path.exists(INVENTORY):
        rows = load(INVENTORY)
        by_name = [r for r in rows if name and name.lower() in r["List of sources"].lower()]
        by_file = [r for r in rows if base and base in r["URLs"].lower()]
        hit = by_name[0] if len(by_name) == 1 else (by_file[0] if len(by_file) == 1 else None)      # ambiguous names fall through to a generic row
        if hit: return dict(hit)
    modality = {"pdf": "PDF", "xlsx": "XLSX (Excel)", "xls": "XLSX (Excel)", "csv": "CSV"}[kind]
    return {"Team Name": "Team 2", "State": "Haryana", "List of sources": name or os.path.basename(path),
            "URLs": url or "file://" + os.path.abspath(path), "Period Of Interest": None, "Modalities": modality, "Limitations": "", "Extracted Knowledge": ""}

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True); ap.add_argument("--source-name"); ap.add_argument("--url")
    ap.add_argument("--db", default=os.path.join(HERE, "haryana.db")); ap.add_argument("--log", default=os.path.join(HERE, "logs", "automation_log.csv"))
    ap.add_argument("--manual-steps", type=int, default=0, help="hand edits you had to make for this input (counts against the automation rate)")
    ap.add_argument("--notes", default="")
    a = ap.parse_args(argv)
    row = dict(timestamp_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), input_file=a.input, manual_interventions=a.manual_steps,
               environment=f"python {platform.python_version()} / {platform.system()}", notes=a.notes, rows_inserted="", rejected_rows="", skipped_pages="",
               reroutes="", attempts="", final_agent="", routed_agent="", detected_type="", sha256_8="", size_kb="", source_name=a.source_name or "", error="")
    status = "failed"; code = 1
    try:
        if not os.path.exists(a.input): raise FileNotFoundError(a.input)
        raw = open(a.input, "rb").read(); row["sha256_8"] = hashlib.sha256(raw).hexdigest()[:8]; row["size_kb"] = len(raw) // 1024
        kind = detect(a.input); row["detected_type"] = kind
        src = source_row(a.input, kind, a.source_name, a.url); src["local_file"] = os.path.abspath(a.input); row["source_name"] = src["List of sources"]
        agents = dict(AGENTS)
        agents["structured_data"] = StructuredDataAgent(db_path=a.db); agents["digital_pdf"] = DigitalPDFAgent(db_path=a.db); agents["hindi_ocr"] = HindiOCRAgent(db_path=a.db)
        sup = Supervisor([src], agents); job = sup.jobs[0]; row["routed_agent"] = job.agent; sup.run()
        row["final_agent"], row["attempts"] = job.agent, job.attempts; row["reroutes"] = sum(l.startswith("rerouted") for l in job.log)
        ag = agents.get(job.agent); row["rejected_rows"] = len(getattr(ag, "rejected_rows", []) or []); row["skipped_pages"] = len(getattr(ag, "skipped_pages", []) or [])
        status = {"ok": "ok", "pending_implementation": "pending_implementation"}.get(job.status, "failed")
        if status != "ok": row["error"] = (job.error or "; ".join(job.log[-1:]) or job.status)[:300]
        if os.path.exists(a.db):
            con = sqlite3.connect(a.db)
            try: row["rows_inserted"] = con.execute("select count(*) from observation o join source s using(source_id) where s.url=?", (src["URLs"],)).fetchone()[0]
            except sqlite3.Error: pass
            con.close()
        code = 0 if status == "ok" else 1
    except Exception as e:
        row["error"] = f"{type(e).__name__}: {e}"[:300]
    row["status"] = status
    os.makedirs(os.path.dirname(a.log), exist_ok=True); new = not os.path.exists(a.log)
    with open(a.log, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, LOG_COLS); (new and w.writeheader()); w.writerow({k: row.get(k, "") for k in LOG_COLS})
    print(f"{status.upper()}: {row['source_name']} | agent {row['routed_agent']}->{row['final_agent']} | rows {row['rows_inserted']} | "
          f"rejected {row['rejected_rows']} | skipped pages {row['skipped_pages']}" + (f" | {row['error']}" if row["error"] else ""))
    return code

if __name__ == "__main__": sys.exit(main())
