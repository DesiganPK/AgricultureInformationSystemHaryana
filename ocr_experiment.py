"""Hindi OCR experiment: compare tools on ONE PDF page against a hand-typed reference.
Usage:
  python ocr_experiment.py <pdf> <page_number> <reference.txt> [--crop x0,y0,x1,y1] [--tools a,b] [--converter module:function] [--tesseract-cmd path] [--outdir folder] [--nospace]
  --crop     fractions of the page (0-1), e.g. 0,0.15,1,0.6 keeps the table region only
  --tools    subset of: text_layer, tesseract, easyocr, paddle   (default: all that are installed)
  --converter  a Kruti Dev -> Unicode function you found, e.g. mymod:convert; it is applied to the text layer
Reports, per tool: character error rate (lower is better), number recall/precision (do the digits come out right?), seconds.
Raw text of every tool is saved in ocr_out/ so you can read what each tool actually produced."""
import sys, os, re, time, csv, importlib, argparse, unicodedata
from collections import Counter
os.environ["PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT"] = "0"
DEV_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
def norm(s):
    s = unicodedata.normalize("NFC", s).replace("\u200c", "").replace("\u200d", "")      # same letters, same code points
    return re.sub(r"\s+", " ", s.translate(DEV_DIGITS)).strip()

def edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

NOSPACE = {"on": False}
def cer(ref, hyp):
    ref, hyp = norm(ref), norm(hyp)
    if NOSPACE["on"]: ref, hyp = ref.replace(" ", ""), hyp.replace(" ", "")      # ignore word-spacing differences
    return edit_distance(ref, hyp) / max(len(ref), 1)

def number_scores(ref, hyp):
    """Compare the multiset of numbers (digits are what the database needs most)."""
    r, h = Counter(re.findall(r"\d+(?:\.\d+)?", norm(ref))), Counter(re.findall(r"\d+(?:\.\d+)?", norm(hyp)))
    hit = sum((r & h).values())
    return hit / max(sum(r.values()), 1), hit / max(sum(h.values()), 1)

def numbers(text): return re.findall(r"\d+(?:\.\d+)?", norm(text))

def ordered_recall(ref, hyp):
    """Share of the reference numbers that appear in the SAME ORDER in the output (longest common subsequence).
    Stricter than plain recall: a number in the wrong place does not count."""
    r, h = numbers(ref), numbers(hyp)
    prev = [0] * (len(h) + 1)
    for a in r:
        cur = [0]
        for j, b in enumerate(h, 1): cur.append(prev[j - 1] + 1 if a == b else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1] / max(len(r), 1)

def missed_numbers(ref, hyp):
    return list((Counter(numbers(ref)) - Counter(numbers(hyp))).elements())

def union_recall(ref, *hyps):
    """UPPER BOUND on what a hybrid could reach: numbers found by at least one tool."""
    u = Counter()
    for h in hyps: u |= Counter(numbers(h))
    r = Counter(numbers(ref)); return sum((r & u).values()) / max(sum(r.values()), 1)

# ---- tools: each returns text, or raises ImportError/RuntimeError if unavailable ----
def decode_numbers(t):
    """Fixed rules for how this font stores punctuation (observed on pages 15, 45, 46 of the Hisar abstract):
    '-' between digits is a decimal point, a lone '--'/'---' is the '..'/'...' blank marker, '17-' is '17.', '&' between digits is an en dash."""
    t = re.sub(r"(?<=\d)-(?=\d)", ".", t)
    t = re.sub(r"(?<![\w-])(-{2,3})(?![\w-])", lambda m: "." * len(m.group(1)), t)
    t = re.sub(r"(?<=\d)-(?![\w-])", ".", t)
    return re.sub(r"(?<=\d)&(?=\d)", "\u2013", t)
def run_text_layer_decoded(page, img, converter=None):
    return decode_numbers(page.extract_text() or "")
def run_text_layer(page, img, converter=None):
    t = page.extract_text() or ""
    return converter(t) if converter else t
def run_tesseract(page, img, converter=None):
    import pytesseract
    if "hin" not in pytesseract.get_languages(config=""):          # otherwise Tesseract silently falls back to English
        raise RuntimeError("Hindi language data (hin.traineddata) is not installed for Tesseract")
    return pytesseract.image_to_string(img, lang="hin+eng")
_EASY = {}
def _easy_reader():
    import easyocr
    if "r" not in _EASY: _EASY["r"] = easyocr.Reader(["hi", "en"], gpu=False, verbose=False)
    return _EASY["r"]
def run_easyocr(page, img, converter=None):
    import numpy as np
    return "\n".join(_easy_reader().readtext(np.array(img), detail=0))
def run_paddle(page, img, converter=None):
    import numpy as np
    from paddleocr import PaddleOCR                      # API differs between versions: untested by the author
    ocr = PaddleOCR(lang="hi"); out = ocr.ocr(np.array(img))
    lines = []
    for block in out or []:
        for item in block or []:
            lines.append(item[1][0] if isinstance(item, (list, tuple)) else str(item))
    return "\n".join(lines)
WARM = {"easyocr": _easy_reader}          # model loading happens before the timer starts
TOOLS = {"text_layer": run_text_layer, "text_layer_numdecode": run_text_layer_decoded, "tesseract": run_tesseract, "easyocr": run_easyocr, "paddle": run_paddle}

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("pdf"); ap.add_argument("page", type=int); ap.add_argument("reference")
    ap.add_argument("--crop"); ap.add_argument("--tools"); ap.add_argument("--converter")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--outdir", default="ocr_out", help="folder for raw outputs and results csv (use a different one per page)")
    ap.add_argument("--nospace", action="store_true", help="ignore spaces when computing character error rate (helps on jammed table headers)")
    ap.add_argument("--tesseract-cmd", help=r"full path, e.g. \"C:\Program Files\Tesseract-OCR\tesseract.exe\" if tesseract is not on PATH")
    a = ap.parse_args(argv)
    import pdfplumber
    NOSPACE["on"] = a.nospace
    if a.tesseract_cmd:
        import pytesseract; pytesseract.pytesseract.tesseract_cmd = a.tesseract_cmd
    ref = open(a.reference, encoding="utf-8-sig").read()
    converter = None
    if a.converter:
        mod, fn = a.converter.split(":"); converter = getattr(importlib.import_module(mod), fn)
    os.makedirs(a.outdir, exist_ok=True)
    with pdfplumber.open(a.pdf) as pdf:
        page = pdf.pages[a.page - 1]
        if a.crop:
            x0, y0, x1, y1 = [float(v) for v in a.crop.split(",")]
            page = page.crop((x0 * page.width, y0 * page.height, x1 * page.width, y1 * page.height))
        img = page.to_image(resolution=a.dpi).original.convert("RGB")
        img.save(os.path.join(a.outdir, f"page{a.page}.png"))
        names = a.tools.split(",") if a.tools else list(TOOLS)
        results = []; texts = {}
        for name in names:
            label = name + ("+converter" if converter and name == "text_layer" else "")
            try:
                if name in WARM: WARM[name]()
                t0 = time.time()
                text = TOOLS[name](page, img, converter)
            except ImportError as e:
                results.append((label, None, None, None, None, f"not installed ({e})", None)); continue
            except Exception as e:
                results.append((label, None, None, None, None, f"failed: {type(e).__name__}: {e}"[:150], None)); continue
            secs = time.time() - t0
            open(os.path.join(a.outdir, f"{label}.txt"), "w", encoding="utf-8").write(text)
            rec, prec = number_scores(ref, text); texts[label] = text
            results.append((label, cer(ref, text), rec, prec, secs, "ok", ordered_recall(ref, text)))
    print(f"\npage {a.page} of {a.pdf}   reference: {len(norm(ref))} characters, {len(numbers(ref))} numbers\n")
    print(f"{'tool':<22}{'CER':>8}{'num recall':>12}{'in-order':>10}{'num precision':>15}{'sec (no load)':>15}  status")
    f3 = lambda v, d=3: "" if v is None else format(v, f".{d}f")
    for label, c, r, p, sec, st, o in results:
        print(f"{label:<22}{f3(c):>8}{f3(r, 2):>12}{f3(o, 2):>10}{f3(p, 2):>15}{f3(sec, 1):>15}  {st}")
    ok = [r[0] for r in results if r[5] == "ok"]
    if ok: print("\nreference numbers each tool MISSED (first 12):")
    for l in ok: print(f"  {l:<22}{missed_numbers(ref, texts[l])[:12]}")
    for i in range(len(ok)):
        for j in range(i + 1, len(ok)):
            print(f"upper bound if numbers came from {ok[i]} + {ok[j]}: recall {union_recall(ref, texts[ok[i]], texts[ok[j]]):.2f}")
    with open(os.path.join(a.outdir, "ocr_results.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["tool", "cer", "number_recall", "number_precision", "seconds", "status", "ordered_number_recall", "pdf", "page"])
        for row in results: w.writerow(list(row[:6]) + [row[6]] + [a.pdf, a.page])
    print(f"\nraw outputs and ocr_results.csv are in {a.outdir}/")
    return results

if __name__ == "__main__": main()
