"""Write refreshed URLs (and newly found districts) back into the inventory CSV, keeping a .bak copy and the column layout."""
import csv, os, shutil


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f); return r.fieldnames, list(r)


def _write(path, fields, rows):
    if not os.path.exists(path + ".bak"): shutil.copy2(path, path + ".bak")
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)


def write_urls(path, new_urls):
    """new_urls: {source name: url}. Only the URLs column of those rows changes."""
    fields, rows = _read(path)
    for r in rows:
        if r["List of sources"] in new_urls: r["URLs"] = new_urls[r["List of sources"]]
    _write(path, fields, rows)


def add_districts(path, extra):
    """extra: [(district, url)] found on the listing page but missing from the inventory. Each gets a row copied from an existing
    district-abstract row (same modality, limitations and knowledge), with its own name and URL. Returns the names added."""
    fields, rows = _read(path)
    tmpl = next(r for r in rows if "District Statistical Abstract:" in r["List of sources"])
    added = []
    for d, u in extra:
        name = f"Haryana DESA - District Statistical Abstract: {d}"
        if any(r["List of sources"] == name for r in rows): continue
        new = dict(tmpl, **{"List of sources": name, "URLs": u, "Period Of Interest": ""})
        last = max(i for i, r in enumerate(rows) if "District Statistical Abstract:" in r["List of sources"])
        rows.insert(last + 1, new); added.append(name)
    if added: _write(path, fields, rows)
    return added
