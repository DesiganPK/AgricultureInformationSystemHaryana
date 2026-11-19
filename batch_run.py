"""Download every pending source in docs/source_agent_map.csv and run it through run.py.
Put this file in the repo root (next to run.py) and run:
    python batch_run.py --db haryana.db            # all pending, implemented agents
    python batch_run.py --db haryana.db --only Hindi   # only sources whose name contains 'Hindi' / a district
    python batch_run.py --dry-run                  # list what would be done
    python batch_run.py --db haryana.db --redo --only "Statistical Abstract"   # re-run sources already loaded (see tools/clear_rows.py)
Notes
- Downloads run in parallel (8 threads); extraction runs one file at a time (SQLite is single-writer).
- Links that keep changing: district abstracts take their PDF link from the live listing page
  https://esaharyana.gov.in/district-statistical-abstracts-district-wise/ on every run, and the fresh URL is written back
  into Source_files_Haryana_final.csv (a .bak copy is kept; --no-save-links to skip). An inventory row whose URL is an
  esaharyana listing page (State Statistical Abstract, District Socio-Economic Review) runs once per PDF on that page.
- Web pages go to the web_content agent. For the other agents, URLs that return an HTML page
  instead of a PDF/Excel/CSV are skipped (listing pages, API-key pages). Those are listed at the end for manual handling.
- Every run goes through run.py, so each one appends a real row to logs/automation_log.csv.
- Already-downloaded files are not fetched again.
"""
import argparse, csv, os, re, subprocess, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
MAP = os.path.join(HERE, "docs", "source_agent_map.csv")
IMPLEMENTED = {"structured_data", "digital_pdf", "hindi_ocr", "web_content"}
INVENTORY = os.path.join(HERE, "Source_files_Haryana_final.csv")


def refresh_and_expand(rows, save_links):
    """Fresh URLs from the live listing pages (sources_live), written back to the inventory CSV when save_links is on, then every
    listing-page row expanded to one job per PDF on that page."""
    import sources_live as sl
    changes, missing, extra = sl.refresh(rows, name_key="source_name", url_key="url")
    for n, old, new in changes: print(f"  URL refreshed from the listing page: {n[-45:]}\n    {old}\n -> {new}")
    for n in missing: print(f"  not on the listing page, keeping the CSV URL: {n}")
    for d, u in extra: print(f"  listed on the page but not in the inventory: {d} ({u})  -> python tools/refresh_links.py --add-missing")
    if changes and save_links:
        import refresh_links_lib as rl
        rl.write_urls(INVENTORY, {n: new for n, _, new in changes}); print(f"  {len(changes)} URL(s) saved to {os.path.basename(INVENTORY)}")
    jobs = []
    for r in rows:
        sub = sl.expand(r["source_name"], r["url"]) if r["agent"] != "web_content" else [(r["source_name"], r["url"])]
        if len(sub) != 1 or sub[0][1] != r["url"]: print(f"  listing page expanded: {r['source_name'][:60]} -> {len(sub)} PDF(s)")
        for name, url in sub: jobs.append(dict(r, source_name=name, url=url))
    return jobs


def fname(row):
    base = re.sub(r"[^A-Za-z0-9]+", "_", row["source_name"]).strip("_")[:80]
    mod = row["modality"].lower()
    ext = ".html" if row["agent"] == "web_content" else ".xlsx" if "xlsx" in mod or "excel" in mod else ".csv" if mod.startswith("csv") else ".pdf"
    return os.path.join(RAW, base + ext)


def download(item):
    row, path = item
    stamp = path + ".url"                                         # which URL the cached file came from
    if os.path.exists(path) and os.path.getsize(path) > 1000 and (not os.path.exists(stamp) or open(stamp).read().strip() == row["url"]):
        return row, path, "cached"
    try:
        req = urllib.request.Request(row["url"], headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
    except Exception as e:
        return row, path, f"download failed: {type(e).__name__}: {e}"
    if path.endswith(".html"):                                   # a web page is what the web_content agent expects
        open(path, "wb").write(data); open(stamp, "w").write(row["url"]); return row, path, "downloaded"
    if data[:5] == b"%PDF-" or data[:2] == b"PK" or path.endswith(".csv"):
        if path.endswith(".csv") and data.lstrip()[:1] == b"<":
            return row, path, "got an HTML page, not data"
        open(path, "wb").write(data); open(stamp, "w").write(row["url"])
        return row, path, "downloaded"
    return row, path, "got an HTML page, not a PDF/Excel file"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="haryana.db")
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-save-links", action="store_true", help="do not write refreshed URLs back into Source_files_Haryana_final.csv")
    ap.add_argument("--redo", action="store_true", help="also re-run sources already marked done (after the agents changed). Clear their old rows first: python tools/clear_rows.py --db DB --agent hindi_ocr")
    a = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    rows = [r for r in csv.DictReader(open(MAP, newline="", encoding="utf-8-sig"))
            if (r["status"] == "pending" or a.redo) and r["agent"] in IMPLEMENTED and a.only.lower() in r["source_name"].lower()]
    rows = refresh_and_expand(rows, not a.no_save_links)
    items = [(r, fname(r)) for r in rows]
    print(f"{len(items)} files to run (pending sources with an implemented agent, listing pages expanded)")
    if a.dry_run:
        for r, p in items:
            print(f"  {r['agent']:<16} {r['source_name'][:70]}")
        return
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(download, items))
    manual, ok, bad = [], 0, 0
    for row, path, st in got:
        if st in ("cached", "downloaded"):
            cmd = [sys.executable, os.path.join(HERE, "run.py"), "--input", path, "--source-name", row["source_name"], "--url", row["url"], "--db", a.db]
            r = subprocess.run(cmd, capture_output=True, text=True)
            print((r.stdout.strip().splitlines() or [r.stderr.strip()[-200:]])[-1])
            ok += r.returncode == 0
            bad += r.returncode != 0
        else:
            manual.append((row["source_name"], row["url"], st))
    print(f"\nrun ok: {ok}   run failed: {bad}   need manual download: {len(manual)}")
    for n, u, st in manual:
        print(f"  - {n}\n    {u}\n    {st}")
    print("\nNext: python tools/make_source_map.py   (flip statuses to done), then python eval/run_eval.py --db", a.db)


if __name__ == "__main__":
    main()
