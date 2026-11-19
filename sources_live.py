"""Live links for sources whose file URLs keep changing.

The Haryana DESA site (esaharyana.gov.in) keeps its PDFs on an S3 host (cdnbbsr.s3waas.gov.in) under upload paths that change when a
file is replaced. The listing pages on esaharyana.gov.in stay put, so we read the listing page at run time and take the PDF link
from there instead of trusting the URL stored in the inventory CSV.

  district abstracts   https://esaharyana.gov.in/district-statistical-abstracts-district-wise/
                       one table row per district: "District Statistical Abstracts (Bhiwani)" | "View" -> .pdf
  any other listing    an inventory row whose URL is an esaharyana page (not a file) is EXPANDED: every PDF linked on the page
                       becomes its own job, named "<inventory name> | <title on the page>"
  one file of a list   "State Statistical Abstract ... - 2024-25 PDF direct link": the PDF on its listing whose title holds 2024-25

Nothing here guesses: a district not found on the page keeps its CSV URL and is reported; a page that cannot be read leaves every URL
as it is. Used by batch_run.py (every run) and tools/refresh_links.py (writes the fresh URLs back into the CSV)."""
import html as htmllib, os, re, ssl, urllib.request
from urllib.parse import urljoin, urlparse

DISTRICT_PAGE = "https://esaharyana.gov.in/district-statistical-abstracts-district-wise/"
STATE_ABSTRACT_PAGE = "https://esaharyana.gov.in/document-category/state-statistical-abstract-of-haryana-english"
LISTING_HOSTS = ("esaharyana.gov.in",)
UA = {"User-Agent": "Mozilla/5.0 (AgriAgent source refresh)"}
_CACHE = {}


def get(url, timeout=60):
    """Fetch a page as text. An SSL failure is retried without verification only if ALLOW_INSECURE_DOWNLOAD=1 (public pages only)."""
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.read().decode("utf-8", "ignore")
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLCertVerificationError) and os.environ.get("ALLOW_INSECURE_DOWNLOAD") == "1":
            with urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as r: return r.read().decode("utf-8", "ignore")
        raise


def _text(fragment):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def parse_listing(page, base):
    """[(title, absolute pdf url)] from a listing page. Table rows first (title cell + 'View' link), else every link to a .pdf with
    its own text, or the text just before it when the link only says View / Download."""
    out, seen = [], set()
    for tr in re.findall(r"<tr\b.*?</tr>", page, re.S | re.I):
        hrefs = re.findall(r'href\s*=\s*["\']([^"\']+?\.pdf[^"\']*)["\']', tr, re.I)
        if not hrefs: continue
        cells = re.findall(r"<t[dh]\b.*?</t[dh]>", tr, re.S | re.I)
        title = next((_text(c) for c in cells if _text(c) and not re.search(r"\.pdf|^\s*(view|download)", c, re.I)), "") or _text(tr)
        u = urljoin(base, htmllib.unescape(hrefs[0]))
        if u not in seen: seen.add(u); out.append((title, u))
    if out: return out
    for m in re.finditer(r'<a\b[^>]*href\s*=\s*["\']([^"\']+?\.pdf[^"\']*)["\'][^>]*>(.*?)</a>', page, re.S | re.I):
        u = urljoin(base, htmllib.unescape(m.group(1))); t = _text(m.group(2))
        if not t or re.fullmatch(r"(?i)(view|download|click here|pdf)(\s*\(.*\))?", t):
            t = _text(page[max(0, m.start() - 400):m.start()])[-120:]
        if u not in seen: seen.add(u); out.append((t, u))
    return out


def listing(url, max_pages=10):
    """All (title, pdf) on a listing page, following WordPress-style pagination (/page/2/ ...). Cached per run."""
    if url in _CACHE: return _CACHE[url]
    items, pages = [], [url]
    first = get(url)
    items += parse_listing(first, url)
    for n in range(2, max_pages + 1):
        nxt = url.rstrip("/") + f"/page/{n}/"
        if nxt not in first and f"/page/{n}" not in first: break
        try: items += parse_listing(get(nxt), nxt)
        except Exception: break
    _CACHE[url] = items
    return items


def is_listing_url(url):
    """An esaharyana page (not a file): its PDFs must be discovered."""
    p = urlparse(url)
    return p.netloc.endswith(LISTING_HOSTS) and not p.path.lower().endswith((".pdf", ".xlsx", ".xls", ".csv"))


def _district(text):
    from district_names import canonical
    for part in re.findall(r"\(([^)]+)\)", text) + [text.split(":", 1)[-1], text]:
        for w in re.split(r"[^A-Za-z]+", part):
            d = canonical(w) if len(w) > 2 else None
            if d: return d
    return None


def district_links():
    """{canonical district: pdf url} from the district listing page; {} if the page cannot be read."""
    try: items = listing(DISTRICT_PAGE)
    except Exception as e:
        print(f"WARNING: could not read {DISTRICT_PAGE} ({type(e).__name__}: {e}); district abstracts keep their CSV URLs"); return {}
    out = {}
    for title, u in items:
        d = _district(title)
        if d and d not in out: out[d] = u
    return out


def is_district_abstract(name): return "district statistical abstract:" in name.lower()


def refresh(rows, name_key="List of sources", url_key="URLs"):
    """Fix URLs in place. Returns (changes, missing, extra): changes = [(name, old, new)], missing = inventory districts not on the page,
    extra = [(district, url)] listed on the page but not in the inventory (a new or forgotten district)."""
    changes, missing = [], []
    needs_districts = any(is_district_abstract(r[name_key]) for r in rows)
    links = district_links() if needs_districts else {}
    have = set()
    for r in rows:
        name = r[name_key]
        if is_district_abstract(name) and links:
            d = _district(name)
            new = links.get(d)
            if d: have.add(d)
            if new and new != r[url_key]: changes.append((name, r[url_key], new)); r[url_key] = new
            elif not new: missing.append(name)
        m = re.search(r"((?:19|20)\d{2}\s*-\s*\d{2,4})\s*PDF direct link", name, re.I)
        if m and "state statistical abstract" in name.lower():
            try:
                year = re.sub(r"\s", "", m.group(1))
                hit = next((u for t, u in listing(STATE_ABSTRACT_PAGE) if year in re.sub(r"\s", "", t)), None)
                if hit and hit != r[url_key]: changes.append((name, r[url_key], hit)); r[url_key] = hit
            except Exception as e:
                print(f"WARNING: could not read {STATE_ABSTRACT_PAGE} ({type(e).__name__}); keeping the CSV URL for {name[:60]}")
    extra = sorted((d, u) for d, u in links.items() if d not in have) if links else []
    return changes, missing, extra


def expand(name, url):
    """Jobs for one inventory row: [(job name, file url)]. A listing page becomes one job per PDF on it; anything else stays one job."""
    if not is_listing_url(url): return [(name, url)]
    try: items = listing(url)
    except Exception as e:
        print(f"WARNING: could not read listing {url} ({type(e).__name__}: {e})"); return []
    jobs = []
    for title, u in items:
        label = re.sub(r"\s*(Accessible Version\s*:?)?\s*View\s*\(.*?\)\s*$", "", title).strip() or os.path.basename(urlparse(u).path)
        jobs.append((f"{name} | {label[:80]}", u))
    return jobs
