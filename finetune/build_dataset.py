"""Build the fine-tuning data for the AgriAgent LLM.   python finetune/build_dataset.py [--raw data/raw] [--heldout-districts karnal sirsa rewari jind] [--synthetic 1200]

Three tasks, the same three the pipeline asks the model at run time (same system prompt, same user message format):
  map_columns   table title + column headers  ->  crop / measure / unit per column, and what the first column holds
  route         file profile                  ->  which agent
  doc_context   first lines of a document     ->  district and year (or null)

Where the examples come from, and how far each label can be trusted:
  synthetic (label_kind = gold_synthetic)   tables generated from the project's vocabularies with known answers, then damaged the way the
                                            legacy-font decoder and typists damage text (split vowel signs, swapped anusvara, typos).
                                            The answer is known by construction. This is what teaches robustness beyond the rules.
  real files (label_kind = silver_rules)    tables read from the PDFs in data/raw; the label is what the RULES decided. Only tables the rules
                                            resolved fully are kept. These labels are not human-checked: they anchor the model to real layouts,
                                            they do not prove it right. Correct them by hand (or add your checked eval references) to make them gold.

Train / held-out split (needed for the adapter comparison, M18):
  real files are held out by FILE: every table of a held-out file is in heldout.jsonl and none in train.jsonl (sha256 recorded);
  synthetic held-out examples come from a different random seed and are dropped if the same prompt text occurs in train.
  split_manifest.json records the file hashes and the two overlap counts; both must be 0."""
import argparse, glob, hashlib, json, os, random, re, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, ROOT)
os.environ["AGRI_LLM"] = "off"                      # labels come from rules and from construction, never from a model
import table_mapper as tm
import doc_context as dc
from district_names import DISTRICTS

HERE = os.path.dirname(os.path.abspath(__file__))
HI_MEASURE = {"area": ["क्षेत्रफल", "क्षेत्र"], "production": ["उत्पादन"], "yield": ["औसत उपज", "उपज"]}
EN_MEASURE = {"area": ["Area"], "production": ["Production"], "yield": ["Yield", "Average yield", "Avg. Yield"]}
HI_UNIT = {"ha": "हैक्टेयर", "00 ha": "00 हैक्टेयर", "000 ha": "हजार हैक्टेयर", "tonnes": "टन", "00 tonnes": "00 टनों", "000 tonnes": "हजार टन", "kg/ha": "किलोग्राम@हैक्टेयर",
           "quintal/ha": "क्विंटल प्रति हैक्टेयर", "mm": "मि0 मी0", "Rs/quintal": "रुपये प्रति क्विंटल", "number": "संख्या", "%": "प्रतिशत"}
EN_UNIT = {"ha": "hectares", "00 ha": "00 hectares", "000 ha": "'000 hectares", "tonnes": "tonnes", "00 tonnes": "00 tonnes", "000 tonnes": "000 tonnes", "kg/ha": "Kgs. per hectare",
           "quintal/ha": "quintals per hectare", "mm": "mm", "Rs/quintal": "Rs. per quintal", "number": "Number", "%": "percent"}
NON_CROP_HI = ["सरकारी नहरें", "नलकूप", "कूएं", "तालाब", "अन्य साधन", "जोड", "पुरुष", "स्त्रियां", "देहाती", "शहरी", "प्राथमिक विद्यालय", "पशु चिकित्सा अस्पताल", "डाकघर", "पक्की सडकें"]
NON_CROP_EN = ["Government canals", "Tubewells", "Wells", "Tanks", "Other sources", "Total", "Male", "Female", "Rural", "Urban", "Primary schools", "Veterinary hospitals", "Post offices", "Metalled roads"]
ROW_HEAD = {"year": (["वर्ष"], ["Year"]), "tehsil": (["वर्ष@तहसील", "तहसील", "खण्ड", "वर्ष@खण्ड"], ["Year / Tehsil", "Tehsil", "Block"]), "district": (["जिला"], ["District", "District name"]),
            "crop": (["फसल"], ["Crop", "Name of crop"]), "item": (["मद", "विवरण"], ["Item", "Particulars"])}
HI_DISTRICT = {v: k for k, v in dc.HI_DISTRICTS.items()}
TEHSILS = ["Bhiwani", "Bawani Khera", "Dadri", "Loharu", "Siwani", "Tosham", "Hansi", "Narnaund", "Assandh", "Gharaunda", "Indri", "Nilokheri"]


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def example(task, system, user, answer, kind, source="synthetic", source_sha=""):
    return dict(id="", task=task, label_kind=kind, source=source, source_sha256=source_sha,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}, {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)}])


# ------------------------------------------------------------------ damage (what real text looks like after decoding / typing)
def damage_hi(s, rng):
    out = []
    for w in s.split(" "):
        r = rng.random()
        if r < 0.10 and re.search("[ा-ौ]", w):                       # a space before a vowel sign: 'टना ें'
            i = [m.start() for m in re.finditer("[ा-ौं]", w)]; k = rng.choice(i); w = w[:k] + " " + w[k:]
        elif r < 0.18: w = re.sub("([ा-ौ])(ं)", r"\2\1", w, count=1)   # anusvara before the vowel sign: 'गेहंू'
        elif r < 0.23: w = w.replace("ो", "ाे", 1)                # 'ो' written as 'ा' + 'े'
        elif r < 0.27: w = w.replace("़", "")                                # nukta dropped
        elif r < 0.30 and len(w) > 4: k = rng.randrange(1, len(w) - 1); w = w[:k] + w[k + 1:]   # one character lost
        out.append(w)
    return " ".join(out)


def damage_en(s, rng):
    out = []
    for w in s.split(" "):
        r = rng.random()
        if len(w) > 4 and w.isalpha():
            if r < 0.06: k = rng.randrange(1, len(w) - 1); w = w[:k] + w[k + 1:]                       # letter dropped
            elif r < 0.10: k = rng.randrange(1, len(w) - 1); w = w[:k] + w[k] + w[k:]                  # letter doubled
            elif r < 0.14: k = rng.randrange(2, len(w) - 1); w = w[:k] + " " + w[k:]                   # word split across lines
            elif r < 0.20: w = w.upper()
        out.append(w)
    return " ".join(out)


# ------------------------------------------------------------------ synthetic tables (gold)
def synth_table(rng, noisy=True):
    hi = rng.random() < 0.6; crops = list(tm.CROP_ALIASES)
    def alias(name):
        al = [a for a in tm.CROP_ALIASES[name] if bool(re.search("[ऀ-ॿ]", a)) == hi] or tm.CROP_ALIASES[name]
        a = rng.choice(al); return a if hi else a.title()
    kind = rng.choices(["apy", "one_measure", "price", "non_crop", "mixed"], [4, 3, 1.5, 2.5, 1])[0]
    year = f"{rng.randint(1995, 2022)}-{rng.randint(0, 99):02d}"; tno = f"{rng.randint(2, 27)}-{rng.randint(1, 6)}" if hi else f"{rng.randint(2, 27)}.{rng.randint(1, 6)}"
    row_kind = rng.choice(["year", "tehsil", "district", "tehsil"])
    lab = rng.choice(ROW_HEAD[row_kind][0 if hi else 1]); cols = []; gold = []; note_units = {}
    pre = (f"सारणी नं: {tno} " if hi else f"Table No.{tno} ")
    def unit_txt(u): return (HI_UNIT if hi else EN_UNIT)[u]
    def meas_txt(m): return rng.choice((HI_MEASURE if hi else EN_MEASURE)[m])
    if kind == "apy":
        chosen = rng.sample(crops, rng.randint(1, 4)); measures = rng.choice([["area", "production", "yield"], ["area", "production"], ["area", "yield"]])
        stated = rng.random() < 0.8
        note_units = {"area": rng.choice(["ha", "00 ha", "000 ha"]), "production": rng.choice(["tonnes", "00 tonnes", "000 tonnes"]), "yield": rng.choice(["kg/ha", "quintal/ha"])} if stated else {}
        base = "जिले में मुख्य फसलों के अधीन क्षेत्र] उत्पादन तथा प्रति हैक्टेयर औसत उपज" if hi else "Area, production and average yield per hectare of principal crops in the district"
        note = ""
        if stated:
            parts = [f"{meas_txt(m)} {unit_txt(note_units[m])} में" if hi else f"{meas_txt(m)} in {unit_txt(note_units[m])}" for m in measures]
            note = " (" + ("] ".join(parts[:-1]) + " तथा " + parts[-1] if hi and len(parts) > 1 else ", ".join(parts)) + ")"
        title = pre + base + note
        for c in chosen:
            head = alias(c)
            for m in measures:
                cols.append(f"{head} > {meas_txt(m)}"); gold.append(dict(crop=c, measure=m, unit=note_units.get(m)))
    elif kind == "one_measure":
        m = rng.choice(["area", "production", "yield"]); u = rng.choice({"area": ["ha", "00 ha", "000 ha"], "production": ["tonnes", "000 tonnes"], "yield": ["kg/ha", "quintal/ha"]}[m])
        stated = rng.random() < 0.8
        title = pre + (f"जिले में फसलों के अधीन {meas_txt(m)}" if hi else f"{meas_txt(m)} of principal crops") + (f" ({unit_txt(u)} में)" if hi and stated else f" ({unit_txt(u)})" if stated else "")
        for c in rng.sample(crops, rng.randint(3, 8)): cols.append(alias(c)); gold.append(dict(crop=c, measure=m, unit=u if stated else None))
    elif kind == "price":
        stated = rng.random() < 0.85
        title = pre + ("जिले में मुख्य फसलों के फसल कटाई मूल्य" if hi else "Farm harvest prices of principal crops") + (f" ({unit_txt('Rs/quintal')})" if stated else "")
        for c in rng.sample(crops, rng.randint(3, 9)): cols.append(alias(c)); gold.append(dict(crop=c, measure="price", unit="Rs/quintal" if stated else None))
    elif kind == "non_crop":
        pool = NON_CROP_HI if hi else NON_CROP_EN; topic = rng.choice(["irrigation", "count"])
        if topic == "irrigation":
            u = rng.choice(["ha", "00 ha", "000 ha"]); stated = rng.random() < 0.8
            title = pre + ("जिले में सिंचाई अधीन निवल क्षेत्र" if hi else "Net area irrigated by source") + (f" ({unit_txt(u)} में)" if hi and stated else f" ({unit_txt(u)})" if stated else "")
            for h in rng.sample(pool[:6], rng.randint(3, 6)): cols.append(h); gold.append(dict(crop=None, measure="other", unit=u if stated else None))
        else:
            title = pre + ("जिले में संस्थाओं की संख्या" if hi else "Number of institutions in the district")
            for h in rng.sample(pool[5:], rng.randint(3, 7)): cols.append(h); gold.append(dict(crop=None, measure="count", unit="number"))
    else:                                                                       # crop columns next to a percentage / non-crop column
        u = rng.choice(["ha", "000 ha"]); title = pre + ("जिले में फसलों के अधीन क्षेत्रफल" if hi else "Area under crops") + (f" ({unit_txt(u)} में)" if hi else f" ({unit_txt(u)})")
        for c in rng.sample(crops, rng.randint(2, 4)): cols.append(alias(c)); gold.append(dict(crop=c, measure="area", unit=u))
        cols.append("कुल क्षेत्र पर प्रतिशतता" if hi else "Percentage to total area"); gold.append(dict(crop=None, measure="percent", unit="%"))
    if rng.random() < 0.25: title += f" ({'वर्ष ' if hi else 'Year '}{year})"
    dmg = damage_hi if hi else damage_en
    if noisy:
        title = dmg(title, rng); cols = [" > ".join(dmg(p, rng) for p in c.split(" > ")) for c in cols]; lab_shown = lab
    start = rng.choice([2, 2, 2, rng.randint(5, 40)])
    colspec = [dict(c=start + i, header=h) for i, h in enumerate(cols)]
    labels = {"year": [f"{y}-{(y + 1) % 100:02d}" for y in range(2007, 2011)], "tehsil": [f"{i + 1}.{t}" for i, t in enumerate(rng.sample(TEHSILS, 4))],
              "district": rng.sample(DISTRICTS, 4), "crop": [alias(c) for c in rng.sample(crops, 4)], "item": rng.sample(NON_CROP_HI if hi else NON_CROP_EN, 4)}[row_kind]
    # a unit is part of the answer only if its words survived in the text the model sees (the pipeline refuses ungrounded units)
    ev = title + " " + " ".join(cols)
    answer = dict(row_label=row_kind, columns=[dict(c=cs["c"], crop=g["crop"], measure=g["measure"], unit=g["unit"] if g["unit"] and tm.unit_grounded(g["unit"], ev + " " + cs["header"]) else None)
                                                for cs, g in zip(colspec, gold)])
    return example("map_columns", tm.SYSTEM_PROMPT, tm.mapping_prompt(title, colspec, lab, labels), answer, "gold_synthetic")


def synth_route(rng):
    pages = rng.randint(1, 200); kind = rng.choice(["english", "legacy", "scanned", "mixed", "partly_scanned"])
    en = rng.choice(["District wise rainfall in Haryana during Monsoon", "Farm harvest prices of principal crops in India", "Statistical Abstract of Haryana Area and Population", "Table 4.2 Area under principal crops"])
    lg = rng.choice(["ftyk lkaf[;dh; lkjka'k lkj.kh u%a 9-2 ftys esa eq[; Qlyksa ds v/khu {ks=", "o\"kZ@rglhy pkoy Tokj cktjk eDdh {ks=Qy mRiknu vkSlr mit", "lkj.kh ua % 7-4 ftys esa ifjokj dY;k.k fpfdRlky;ksa dh la[;k"])
    if kind == "english": prof = dict(pages_with_text=pages, legacy_hindi_pages=0, sample=en)
    elif kind == "legacy": prof = dict(pages_with_text=pages, legacy_hindi_pages=max(1, int(pages * rng.uniform(0.3, 1))), sample=lg)
    elif kind == "scanned": prof = dict(pages_with_text=0, legacy_hindi_pages=0, sample="")
    elif kind == "mixed": prof = dict(pages_with_text=pages, legacy_hindi_pages=max(1, int(pages * rng.uniform(0.16, 0.6))), sample=rng.choice([en, lg]))
    else: prof = dict(pages_with_text=int(pages * rng.uniform(0, 0.45)), legacy_hindi_pages=0, sample=en)
    prof.update(kind="pdf", file=rng.choice(["abstract.pdf", "bulletin.pdf", "DSA_2012.pdf", "table.pdf", "scan0001.pdf"]), pages=pages, unicode_hindi_pages=0)
    agent, why = dc.route_rules(prof)
    return example("route", dc.ROUTER_SYSTEM, dc.router_prompt(prof), dict(agent=agent, reason=why), "gold_synthetic")


def synth_context(rng):
    d = rng.choice(DISTRICTS); y = rng.randint(1998, 2022); ed = f"{y}-{(y + 1) % 100:02d}"; mode = rng.choice(["en", "hi", "state", "none"])
    if mode == "en": text = f"DISTRICT STATISTICAL ABSTRACT\n{d.upper() if rng.random() < .5 else d}\nYEAR: {ed}\nDistrict Statistical Office, {d}"; ans = dict(district=d, year=ed)
    elif mode == "hi": text = f"जिला सांख्यिकीय सारांश\n{HI_DISTRICT.get(d, d)}\n{ed}\nजिला सांख्यिकीय कार्यालय {HI_DISTRICT.get(d, d)}"; ans = dict(district=d, year=ed)
    elif mode == "state": text = f"STATISTICAL ABSTRACT OF HARYANA {ed}\nDepartment of Economic and Statistical Affairs\nDistricts: " + ", ".join(rng.sample(DISTRICTS, 6)); ans = dict(district=None, year=ed)
    else: text = "Table 3 Area under principal crops\nYear Rice Wheat Bajra\n" + " ".join(str(rng.randint(10, 900)) for _ in range(6)); ans = dict(district=None, year=None)
    return example("doc_context", dc.CONTEXT_SYSTEM, text, ans, "gold_synthetic")


# ------------------------------------------------------------------ real files (silver)
def real_examples(path):
    """Every ruled table of a PDF that the rules resolved completely -> one map_columns example; plus one route example for the file."""
    import pdfplumber
    from hindi_agent import decode_cell, page_language
    h = sha(path); out = []; name = os.path.basename(path)
    prof = dc.profile_pdf(path); agent, why = dc.route_rules(prof)
    out.append(example("route", dc.ROUTER_SYSTEM, dc.router_prompt(prof), dict(agent=agent, reason=why), "silver_rules", name, h))
    notes = {}
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            text = prof["texts"][pno - 1] if pno - 1 < len(prof["texts"]) else ""
            hindi = dc.legacy_page(text) and page_language(text) == "hi"
            try: grids = tm.grid_tables(page, decode_cell if hindi else None, allow_borderless=not hindi)
            except Exception: continue
            for g in grids:
                st = tm.structure(g, hindi); cols = tm.columns_of(st)
                if not cols or not st["header"]: continue
                title = tm.with_carried_note(tm.short_title(st["title"]), notes); lab = tm.header_path(st["header"], 0)
                labels = [r[0] for r in st["body"] if r[0]]
                rk = tm.row_kind_rules(lab, labels, hindi); m = tm.map_rules(title, cols)
                if rk is None or any(x["unit"] == "unspecified" or x["measure"] is None or x["by"] for x in m): continue      # keep only tables the rules resolved fully
                ans = dict(row_label=rk, columns=[dict(c=x["c"], crop=x["crop"], measure=x["measure"], unit=x["unit"]) for x in m])
                out.append(example("map_columns", tm.SYSTEM_PROMPT, tm.mapping_prompt(title, cols, lab, [l for l in labels if not tm.is_year(l)][:4]), ans, "silver_rules", f"{name} p{pno}", h))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", default=os.path.join(ROOT, "data", "raw")); ap.add_argument("--synthetic", type=int, default=1200, help="synthetic training examples")
    ap.add_argument("--heldout-synthetic", type=int, default=200); ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--heldout-districts", nargs="*", default=["karnal", "sirsa", "rewari", "jind"], help="real files whose name contains one of these words are held out (never trained on)")
    a = ap.parse_args()
    files = sorted(glob.glob(os.path.join(a.raw, "*.pdf")))
    held_files = [f for f in files if any(d in os.path.basename(f).lower() for d in a.heldout_districts)]
    train_files = [f for f in files if f not in held_files]
    def synth(n, seed):
        rng = random.Random(seed); out = []
        for i in range(n):
            r = rng.random()
            out.append(synth_table(rng) if r < 0.8 else synth_route(rng) if r < 0.9 else synth_context(rng))
        return out
    train = synth(a.synthetic, a.seed); held = synth(a.heldout_synthetic, a.seed + 1000)
    for f in train_files:
        try: train += real_examples(f)
        except Exception as e: print(f"  skipped {os.path.basename(f)}: {type(e).__name__}: {e}")
    for f in held_files:
        try: held += real_examples(f)
        except Exception as e: print(f"  skipped {os.path.basename(f)}: {type(e).__name__}: {e}")
    key = lambda e: hashlib.sha256((e["task"] + e["messages"][1]["content"]).encode()).hexdigest()
    seen, uniq = set(), []
    for e in train:                                                    # the same table printed on two pages (or in two files) is one example
        k = key(e)
        if k not in seen: seen.add(k); uniq.append(e)
    train = uniq; before = len(held); hseen = set(); h2 = []
    for e in held:
        k = key(e)
        if k not in seen and k not in hseen: hseen.add(k); h2.append(e)
    held = h2
    for name, rows in (("train", train), ("heldout", held)):
        for i, e in enumerate(rows): e["id"] = f"{name}-{i:05d}"
        with open(os.path.join(HERE, f"{name}.jsonl"), "w", encoding="utf-8") as f:
            for e in rows: f.write(json.dumps(e, ensure_ascii=False) + "\n")
    tf = {os.path.basename(f): sha(f) for f in train_files}; hf = {os.path.basename(f): sha(f) for f in held_files}
    count = lambda rows: {f"{t}/{k}": sum(e["task"] == t and e["label_kind"] == k for e in rows) for t in ("map_columns", "route", "doc_context") for k in ("gold_synthetic", "silver_rules")}
    man = dict(seed=a.seed, train_examples=len(train), heldout_examples=len(held), train_counts=count(train), heldout_counts=count(held), train_files=tf, heldout_files=hf,
               overlap_files_by_sha256=len(set(tf.values()) & set(hf.values())), overlap_examples_by_prompt_hash=len({key(e) for e in train} & {key(e) for e in held}),
               heldout_dropped_as_duplicates_of_train=before - len(held),
               note="silver_rules labels are the rules' own decisions on real tables and are not human-checked; gold_synthetic labels are known by construction.")
    json.dump(man, open(os.path.join(HERE, "split_manifest.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print(json.dumps({k: v for k, v in man.items() if k not in ("train_files", "heldout_files")}, indent=1, ensure_ascii=False))
    print("train files:", list(tf) or "none", "\nheld-out files:", list(hf) or "NONE: the held-out set is synthetic only. Put other districts' PDFs in data/raw and re-run to hold real files out.")


if __name__ == "__main__": main()
