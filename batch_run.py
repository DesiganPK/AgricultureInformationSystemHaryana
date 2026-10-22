"""Download every pending source in docs/source_agent_map.csv and run it through run.py.
Put this file in the repo root (next to run.py) and run:
    python batch_run.py --db haryana.db            # all pending, implemented agents
    python batch_run.py --db haryana.db --only Hindi   # only sources whose name contains 'Hindi' / a district
    python batch_run.py --dry-run                  # list what would be done
Notes
- Downloads run in parallel (8 threads); extraction runs one file at a time (SQLite is single-writer).
- Skips sources whose agent is web_content (not implemented), and URLs that return an HTML page
  instead of a PDF/Excel/CSV (listing pages, API-key pages). Those are listed at the end for manual handling.
- Every run goes through run.py, so each one appends a real row to logs/automation_log.csv.
- Already-downloaded files are not fetched again.
"""
import argparse, csv, os, re, subprocess, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "data", "raw")
MAP = os.path.join(HERE, "docs", "source_agent_map.csv")
IMPLEMENTED = {"structured_data", "digital_pdf", "hindi_ocr"}
DISTRICT_PAGE = "https://esaharyana.gov.in/district-statistical-abstracts-district-wise/"
ALIASES = {"mewat": "nuh", "gurgaon": "gurugram"}          # page name vs. name in the source map; both spellings are tried


def discover_district_links():
    """Read the live listing page and return {district_lowercase: absolute_pdf_url}. Empty dict if the page cannot be read."""
    from urllib.parse import urljoin
    try:
        req = urllib.request.Request(DISTRICT_PAGE, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
    except Exception as e:
        print(f"WARNING: could not read {DISTRICT_PAGE} ({type(e).__name__}: {e}); using the CSV URLs for district files")
        return {}
    links = {}
    for tr in re.findall(r"<tr.*?</tr>", html, re.S | re.I):
        t = re.search(r"District Statistical Abstracts?\s*\(([^)]+)\)", tr, re.I)
        h = re.search(r'href="([^"]+\.pdf[^"]*)"', tr, re.I)
        if t and h:
            links[t.group(1).strip().lower()] = urljoin(DISTRICT_PAGE, h.group(1))
    print(f"district listing page: {len(links)} district PDF links found")
    return links


def is_district_abstract(row):
    return "district statistical abstract:" in row["source_name"].lower()


def live_url(row, links):
    name = row["source_name"].split(":", 1)[1].lower()           # e.g. ' mewat (nuh)'
    for key, url in links.items():
        if key in name or ALIASES.get(key, "\0") in name:
            return url
    return None


def fname(row):
    base = re.sub(r"[^A-Za-z0-9]+", "_", row["source_name"]).strip("_")[:80]
    mod = row["modality"].lower()
    ext = ".xlsx" if "xlsx" in mod or "excel" in mod else ".csv" if mod.startswith("csv") else ".pdf"
    return os.path.join(RAW, base + ext)


def download(item):
    row, path = item
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return row, path, "cached"
    try:
        req = urllib.request.Request(row["url"], headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
    except Exception as e:
        return row, path, f"download failed: {type(e).__name__}: {e}"
    if data[:5] == b"%PDF-" or data[:2] == b"PK" or path.endswith(".csv"):
        if path.endswith(".csv") and data.lstrip()[:1] == b"<":
            return row, path, "got an HTML page, not data"
        open(path, "wb").write(data)
        return row, path, "downloaded"
    return row, path, "got an HTML page, not a PDF/Excel file"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="haryana.db")
    ap.add_argument("--only", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    rows = [r for r in csv.DictReader(open(MAP, newline="", encoding="utf-8-sig"))
            if r["status"] == "pending" and r["agent"] in IMPLEMENTED and a.only.lower() in r["source_name"].lower()]
    links = discover_district_links() if any(is_district_abstract(r) for r in rows) else {}
    for r in rows:
        if is_district_abstract(r) and links:
            u = live_url(r, links)
            if u:
                if u != r["url"]: print(f"  URL refreshed from the listing page: {r['source_name'][-30:]}")
                r["url"] = u                                          # the live page wins over the CSV
            else:
                print(f"  not listed on the page any more, keeping the CSV URL: {r['source_name']}")
    items = [(r, fname(r)) for r in rows]
    print(f"{len(items)} pending sources with an implemented agent")
    if a.dry_run:
        for r, p in items:
            print(f"  {r['agent']:<16} {r['source_name'][:70]}")
        return
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(download, items))
    manual, ok, bad = [], 0, 0
    for row, path, st in got:
        if st in ("cached", "downloaded"):
            cmd = [sys.executable, os.path.join(HERE, "run.py"), "--input", path, "--source-name", row["source_name"], "--db", a.db]
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
