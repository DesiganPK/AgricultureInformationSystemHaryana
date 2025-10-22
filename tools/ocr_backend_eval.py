"""Compare OCR backends on pages of a Hindi PDF against the exact text-layer result (digits in a Kruti Dev text layer are exact).
Usage: python tools/ocr_backend_eval.py Bhiwani.pdf "Haryana DESA - District Statistical Abstract: Bhiwani" [--pages 6,7,8,9,35] [--backends tesseract-hin,paddle]
Writes eval/ocr_backend_comparison.csv (one row per backend per page + an ALL row). A backend that cannot run is recorded as such, never skipped silently.
Paddle needs: pip install paddlepaddle paddleocr, and internet on the first run (it downloads its Hindi/Devanagari model)."""
import sys, os, csv, time, argparse, tempfile, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pdfplumber, hindi_agent as H
ap = argparse.ArgumentParser(); ap.add_argument("pdf"); ap.add_argument("source_name")
ap.add_argument("--pages", default="6,7,8,9,35"); ap.add_argument("--backends", default="tesseract-hin,paddle,easyocr"); ap.add_argument("--out", default="eval/ocr_backend_comparison.csv")
a = ap.parse_args(); pages = [int(p) for p in a.pages.split(",")]
tmp = tempfile.mkdtemp(); db = os.path.join(tmp, "x.db"); schema = os.path.join(os.path.dirname(H.__file__), "haryana_schema.sql")
with pdfplumber.open(a.pdf) as pdf: imgs = [pdf.pages[p - 1].to_image(resolution=300).original.convert("RGB") for p in pages]
scan = os.path.join(tmp, "scan.pdf"); imgs[0].save(scan, save_all=True, append_images=imgs[1:], resolution=300)
truth = H.HindiOCRAgent(db_path=db, schema=schema).extract(a.pdf, a.source_name)
tr = collections.defaultdict(collections.Counter)
for r in truth:
    if r["page_no"] in pages: tr[pages.index(r["page_no"]) + 1][r["value_text"]] += 1
fn = dict(H.BACKENDS); rows = []
for name in a.backends.split(","):
    if name not in fn: continue
    ag = H.HindiOCRAgent(db_path=db, schema=schema, backends=((name, fn[name]),)); H._BROKEN.clear(); t = time.time()
    try: ocr = ag.extract(scan, a.source_name)
    except Exception as e:
        why = ag.skipped_pages[0][1] if ag.skipped_pages else f"{type(e).__name__}: {e}"      # the backend's own error, not the generic 'no rows'
        rows.append([name, "ALL", "", "", "", "", "", f"not run: {why[:200]}"]); print(name, "not run:", why[:200]); continue
    secs = time.time() - t; oc = collections.defaultdict(collections.Counter)
    for r in ocr: oc[r["page_no"]][r["value_text"]] += 1
    H_ = T_ = O_ = 0
    for i, p in enumerate(pages, 1):
        h = sum((tr[i] & oc[i]).values()); nt, no = sum(tr[i].values()), sum(oc[i].values()); H_ += h; T_ += nt; O_ += no
        rows.append([name, p, nt, no, h, round(h / max(nt, 1), 3), round(h / max(no, 1), 3), "ok"])
    rows.append([name, "ALL", T_, O_, H_, round(H_ / max(T_, 1), 3), round(H_ / max(O_, 1), 3), f"ok, {secs:.0f}s total"])
    print(name, "recall", rows[-1][5], "precision", rows[-1][6])
os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
with open(a.out, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["backend", "page", "truth_values", "ocr_values", "matched", "recall", "precision", "status"]); w.writerows(rows)
print("wrote", a.out)
