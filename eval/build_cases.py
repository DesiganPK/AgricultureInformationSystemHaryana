"""Draw the evaluation cases and write the case sheets.  Run once:  python eval/build_cases.py [--db haryana.db] [--per-agent 15]
Writes
  eval/cases/cases.csv            what to look up (locator in the source) - used by run_eval.py
  eval/reference/reference.csv    the same cases with BLANK reference columns: a person fills value_ref / unit_ref from the SOURCE FILE
The agent's extracted value is deliberately NOT written to either sheet, so the person checking is not anchored by it.
Cases are sampled at random (fixed seed) with spread over districts / crops / years; about 1 in 4 is a value the source prints as missing.
A reference counts as checked only when verified_by and value_ref are filled; run_eval.py scores nothing else."""
import argparse, csv, os, random, re, sqlite3, sys
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
ap = argparse.ArgumentParser(); ap.add_argument("--db", default=os.path.join(ROOT, "haryana.db")); ap.add_argument("--per-agent", type=int, default=15); ap.add_argument("--seed", type=int, default=2026)
a = ap.parse_args(); rng = random.Random(a.seed)
con = sqlite3.connect(a.db); con.row_factory = sqlite3.Row
SRC = {   # DB source name -> group, agent, local file (run_eval.py reads eval/sources.json, written below)
 "ICRISAT-TCI District Level Database - Additional Data: Area Production (district and season-wise)": dict(agent="structured_data", group="structured_excel", language="en", file="data/raw/*area*production*"),
 "Ministry of Agriculture (DES) - District-wise Farm Harvest Prices, Haryana": dict(agent="digital_pdf", group="digital_pdf_prices", language="en", file="data/raw/FHP_HARYANA_98-2001.pdf"),
 "IMD Chandigarh - District-wise Haryana monsoon rainfall bulletin": dict(agent="digital_pdf", group="digital_pdf_rainfall", language="en", file="data/raw/monsoon_har.pdf"),
 "Haryana DESA - District Statistical Abstract: Bhiwani": dict(agent="hindi_ocr", group="hindi_pdf", language="hi", file="data/raw/Bhiwani.pdf"),
}
Q = """select o.obs_id, o.page_no, o.row_ref, o.table_ref, o.district_raw, o.tehsil_block, c.name_en crop, o.season, o.year_label, o.indicator,
              o.value_num, u.canonical unit, o.needs_review
       from observation o join source s using(source_id) left join crop c using(crop_id) left join unit u using(unit_id) where s.name=? """
def pick(rows, n, miss, spread):
    """n rows, `miss` of them with a missing value, spread over distinct values of the `spread` key where possible."""
    rng.shuffle(rows); out, seen = [], set()
    for want_missing, k in ((False, n - miss), (True, miss)):
        pool = [r for r in rows if (r["value_num"] is None) == want_missing]; got = 0
        for r in pool:
            if got == k: break
            key = spread(r)
            if key in seen: continue
            seen.add(key); out.append(r); got += 1
        for r in pool:                                   # relax the spread if the pool was too uniform
            if got == k: break
            if r not in out: out.append(r); got += 1
    return out[:n]

cases, per = [], {}
# 1) structured_data: ICRISAT Excel
name = next(k for k in SRC if k.startswith("ICRISAT")); rows = con.execute(Q + "and o.row_ref like 'r%'", (name,)).fetchall()
per[name] = pick([dict(r) for r in rows], a.per_agent, 4, lambda r: (r["district_raw"], r["crop"]))
# 2) digital_pdf: prices (9) + rainfall (6)
name = next(k for k in SRC if "Farm Harvest" in k); rows = [dict(r) for r in con.execute(Q, (name,)).fetchall()]
flagged = [r for r in rows if r["value_num"] is not None and re.search(r"[A-Za-z]$", str(con.execute("select value_text from observation where obs_id=?", (r["obs_id"],)).fetchone()[0]))][:60]
per[name] = pick(rows, 9, 2, lambda r: (r["district_raw"], r["year_label"], r["crop"]))
if flagged and not any(r in per[name] for r in flagged): per[name][-1] = rng.choice(flagged)          # include one value the source flags with a letter (r / D / A)
name = next(k for k in SRC if "IMD" in k); rows = [dict(r) for r in con.execute(Q, (name,)).fetchall()]
per[name] = pick(rows, max(a.per_agent - 9, 1), 0, lambda r: (r["district_raw"], r["indicator"]))
# 3) hindi_ocr: summary rows with a known unit (district column c3 / state column c4) + tehsil-table cells with a unit
name = next(k for k in SRC if "Bhiwani" in k); rows = [dict(r) for r in con.execute(Q, (name,)).fetchall()]
summ = [r for r in rows if r["table_ref"].endswith(("c3", "c4")) and r["unit"] not in (None, "unspecified") and not r["tehsil_block"] and " | col " not in r["indicator"]]
teh = [r for r in rows if " | col " in r["indicator"] and r["unit"] not in (None, "unspecified")]
per[name] = pick(summ, 10, 1, lambda r: (r["indicator"], r["year_label"])) + pick(teh, a.per_agent - 10, 0, lambda r: (r["tehsil_block"], r["year_label"], r["table_ref"]))

def where(meta, r):
    g = meta["group"]; col = re.search(r"c(\d+)$", r["table_ref"] or "")
    if g == "structured_excel":
        return (f"Excel file, first sheet: Excel row {r['row_ref'][1:]} (district '{r['district_raw']}', year {r['year_label']}); column whose code in the header row is "
                f"'{r['table_ref'].split(':',1)[1]}'. Crop / season / measure are in the header rows above it. Unit: see the sheet's notes and header.")
    if g == "digital_pdf_prices":
        return f"PDF page {r['page_no']}: district '{r['district_raw']}' (rows continue under the district name), year {r['year_label']}, crop column '{r['crop']}' (printed column number {col.group(1) if col else '?'}). Unit: table title."
    if g == "digital_pdf_rainfall":
        return f"PDF page {r['page_no']}: district row '{r['district_raw']}', column '{r['indicator'].replace('rainfall_','').upper()}'. Report date is at the top of the bulletin."
    if " | col " in r["indicator"]:
        n = r["indicator"].rsplit(" | col ", 1)[1].strip(); t = r["indicator"].split("|")[0].strip()[:90]
        return f"PDF viewer page {r['page_no']}: table '{t}'; row '{r['tehsil_block'] or r['year_label']}' (year block {r['year_label']}); printed column number {n}. Unit: from the table title."
    side = "the DISTRICT column (Bhiwani)" if r["table_ref"].endswith("c3") else "the HARYANA STATE column"
    return f"PDF viewer page {r['page_no']}: row labelled (Hindi) '{r['indicator']}' in the section for year {r['year_label']}; read {side}. Unit: next to the label."

import json
json.dump({k: v for k, v in SRC.items()}, open(os.path.join(HERE, "sources.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
CASE_COLS = ["case_id", "agent", "source_group", "language", "source_name", "file", "page_no", "row_ref", "table_ref", "district", "tehsil", "crop", "season", "year", "indicator"]
REF_EXTRA = ["where_to_look", "value_ref", "unit_ref", "identity_ok", "locator_ok", "district_corrected", "crop_corrected", "year_corrected", "verified_by", "verified_on", "notes"]
rows_c, rows_r, n = [], [], 0
for name, picked in per.items():
    meta = SRC[name]
    for r in picked:
        n += 1; cid = f"{meta['agent'][:4].upper()}-{n:02d}"
        base = dict(case_id=cid, agent=meta["agent"], source_group=meta["group"], language=meta["language"], source_name=name, file=meta["file"], page_no=r["page_no"] or "",
                    row_ref=r["row_ref"], table_ref=r["table_ref"], district=r["district_raw"] or "", tehsil=r["tehsil_block"] or "", crop=r["crop"] or "", season=r["season"] or "",
                    year=r["year_label"] or "", indicator=r["indicator"])
        rows_c.append(base); rows_r.append({**base, "where_to_look": where(meta, r), **{k: "" for k in REF_EXTRA if k != "where_to_look"}})
os.makedirs(os.path.join(HERE, "cases"), exist_ok=True); os.makedirs(os.path.join(HERE, "reference"), exist_ok=True)
for path, cols, rows in ((os.path.join(HERE, "cases", "cases.csv"), CASE_COLS, rows_c), (os.path.join(HERE, "reference", "reference.csv"), CASE_COLS + REF_EXTRA, rows_r)):
    with open(path, "w", newline="", encoding="utf-8-sig") as f: w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(rows)
import collections
print(len(rows_c), "cases:", dict(collections.Counter(r["agent"] for r in rows_c)), "| groups:", dict(collections.Counter(r["source_group"] for r in rows_c)))
