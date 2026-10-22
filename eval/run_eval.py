"""Score the agents against the human-checked references and write eval/phase2_metrics.csv.   python eval/run_eval.py [--db haryana.db]
What it does
  1. re-runs each case file through its agent (extract only, nothing is written to the database),
  2. finds the extracted record at each case's locator (page / row / column), compares it to the reference,
  3. computes M3 (field F1, macro-average over agents), M4 (provenance), M1/M2 (source map), M12 (automation log), M14-M16 (SQL).
A case is scored ONLY if eval/reference/reference.csv has value_ref and verified_by filled. Unchecked cases are listed and left out; they never
count as correct. If no case is checked, M3/M4 are written as 'not computed'. Normalisation rules: eval/README.md.
Field-match rule (protocol): crop, location and period match after normalisation, value exact for integers or within 1% for decimals, unit correct."""
import argparse, csv, glob, json, os, re, sys, collections
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE); sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "db"))
from district_names import canonical

MISSING = {"", "..", ".", "-", "--", "na", "n.a.", "nan", "missing", "not reported"}
UNIT_SYN = {"thousand ha": "000 ha", "'000 ha": "000 ha", "000 hectare": "000 ha", "hectare": "ha", "hectares": "ha", "rs. per quintal": "rs/quintal", "rs per quintal": "rs/quintal",
            "inr/quintal": "rs/quintal", "rs/quintal (unverified)": "rs/quintal", "kg per ha": "kg/ha", "kg/hectare": "kg/ha", "percent": "%", "per cent": "%", "mm.": "mm",
            "'000 tonnes": "000 tonnes", "thousand tonnes": "000 tonnes", "000 tonne": "000 tonnes", "nos": "number", "no.": "number", "numbers": "number"}
CROP_SYN = {"caster seed": "castor seed", "castor": "castor seed", "groundnut": "ground nut", "arhar": "tur arhar", "tur": "tur arhar", "rapeseed & mustard": "rape/mustard"}

def n_district(s): s = (s or "").strip(); return canonical(s) or re.sub(r"\s+", " ", s.lower())
def n_crop(s): s = re.sub(r"\s+", " ", (s or "").strip().lower()); return CROP_SYN.get(s, s)
def n_unit(s): s = re.sub(r"\s+", " ", (s or "").strip().lower()); return UNIT_SYN.get(s, s)
def n_year(s):
    s = re.sub(r"\s+", "", (s or "")).replace("–", "-").replace("&", "-"); s = re.sub(r"\([A-Za-z]\)$", "", s)
    m = re.match(r"^((?:19|20)\d{2})-((?:19|20)\d{2})$", s); return f"{m.group(1)}-{m.group(2)[2:]}" if m else s
def parse_ref(v):
    """None for a missing marker; else float (flag letters such as '1320r' or '2000D' are stripped; commas removed)."""
    t = (v or "").strip().lower()
    if t in MISSING: return None
    t = re.sub(r"(?<=\d)-(?=\d)", ".", t.replace(",", ""))          # the Hindi abstracts' font prints the decimal point as '-' ('34-79' = 34.79)
    return float(re.sub(r"[a-z]+$", "", t))
def same_value(ref, got, ref_text=""):
    """Exact for integers, within 1% (relative) for decimals. Both missing counts as a match; one missing does not."""
    if ref is None or got is None: return ref is None and got is None
    if ref == int(ref) and "." not in ref_text: return got == ref
    return abs(ref - got) <= 0.01 * abs(ref)

def load_csv(p): return list(csv.DictReader(open(p, newline="", encoding="utf-8-sig"))) if os.path.exists(p) else []
def extract_all(sources, cases):
    """Run each needed file through its agent once. Returns {source_name: ({locator: record}, None) or (None, reason)}."""
    from structured_agent import StructuredDataAgent
    from pdf_agent import DigitalPDFAgent
    from hindi_agent import HindiOCRAgent
    mk = dict(structured_data=StructuredDataAgent, digital_pdf=DigitalPDFAgent, hindi_ocr=HindiOCRAgent); out = {}
    for name in sorted({c["source_name"] for c in cases}):
        meta = sources[name]; hits = glob.glob(os.path.join(ROOT, meta["file"]))
        if not hits: out[name] = (None, f"source file not found: {meta['file']} (put it there to score these cases)"); continue
        try:
            ag = mk[meta["agent"]](); recs = ag.extract(hits[0]) if meta["agent"] == "structured_data" else ag.extract(hits[0], name)
            out[name] = ({(str(r.get("page_no") or ""), r["row_ref"], r["table_ref"]): r for r in recs}, None)
        except Exception as e: out[name] = (None, f"agent failed: {type(e).__name__}: {str(e)[:120]}")
    return out

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default=os.path.join(ROOT, "haryana.db")); a = ap.parse_args(argv)
    sources = json.load(open(os.path.join(HERE, "sources.json"), encoding="utf-8"))
    cases = load_csv(os.path.join(HERE, "cases", "cases.csv")); refs = {r["case_id"]: r for r in load_csv(os.path.join(HERE, "reference", "reference.csv"))}
    ex = extract_all(sources, cases); res = []
    for c in cases:
        ref = refs.get(c["case_id"], {}); st = dict(case_id=c["case_id"], agent=c["agent"], group=c["source_group"], language=c["language"], status="", extracted_value="", reference_value="", detail="")
        if not (ref.get("value_ref", "").strip() and ref.get("verified_by", "").strip()): st["status"] = "unverified"; res.append(st); continue
        recs, why = ex[c["source_name"]]
        if recs is None: st["status"], st["detail"] = "source_unavailable", why; res.append(st); continue
        def expected(key, corr):          # identity_ok == N means the checker found the sheet's label wrong: use the corrected label if given
            return (ref.get(corr) or c[key]) if ref.get("identity_ok", "").upper() == "N" else c[key]
        exp_district, exp_crop, exp_year = expected("district", "district_corrected"), expected("crop", "crop_corrected"), expected("year", "year_corrected")
        rec = recs.get((c["page_no"], c["row_ref"], c["table_ref"]))
        try: rv = parse_ref(ref["value_ref"])
        except ValueError: st["status"], st["detail"] = "bad_reference", f"cannot read value_ref {ref['value_ref']!r}"; res.append(st); continue
        st["reference_value"] = ref["value_ref"]
        if rec is None: st["status"] = "not_extracted"; res.append(st); continue
        st["extracted_value"] = rec["value_text"]
        who = c["tehsil"] if c["tehsil"] else ""
        ident = n_district(rec["district_raw"]) == n_district(exp_district) and n_crop(rec["crop"]) == n_crop(exp_crop) and n_year(rec["year"]) == n_year(exp_year) \
                and n_crop(rec.get("tehsil_block") or "") == n_crop(who)
        if not ident: st["status"] = "wrong_identity"; st["detail"] = f"got {rec['district_raw']}/{rec['crop']}/{rec['year']}"
        elif not same_value(rv, rec["value_num"], ref["value_ref"]): st["status"] = "wrong_value"
        elif n_unit(rec["unit"]) != n_unit(ref.get("unit_ref")): st["status"] = "wrong_unit"; st["detail"] = f"got {rec['unit']!r}, reference {ref.get('unit_ref')!r}"
        else: st["status"] = "correct"
        st["locator_ok"] = (ref.get("locator_ok") or "").upper(); res.append(st)
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "case_results.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, ["case_id", "agent", "group", "language", "status", "reference_value", "extracted_value", "detail", "locator_ok"]); w.writeheader(); w.writerows(res)
    # ---- metrics
    M = []                       # (id, metric, scope, value, numerator, denominator, status, notes)
    def add(i, m, s, v, nu="", de="", st="measured", note=""): M.append((i, m, s, "" if v is None else round(v, 4), nu, de, st, note))
    def f1(rs):
        scored = [r for r in rs if r["status"] in ("correct", "wrong_value", "wrong_unit", "wrong_identity", "not_extracted")]
        if not scored: return None
        cor = sum(r["status"] == "correct" for r in scored); got = sum(r["status"] != "not_extracted" for r in scored); n = len(scored)
        P, R = cor / got if got else 0.0, cor / n; return dict(P=P, R=R, F1=(2 * P * R / (P + R) if P + R else 0.0), cor=cor, got=got, n=n)
    agents = sorted({r["agent"] for r in res}); per_agent = {}
    for ag in agents:
        s = f1([r for r in res if r["agent"] == ag]); per_agent[ag] = s
        if s: add("M3", "Field-level F1 (eval set)", ag, s["F1"], s["cor"], s["n"], note=f"P={s['P']:.3f} R={s['R']:.3f}; precision is measured on checked fields only")
        else: add("M3", "Field-level F1 (eval set)", ag, None, st="not_computed", note="no verified references for this agent yet")
    done = [s["F1"] for s in per_agent.values() if s]
    add("M3", "Field-level F1 (eval set), macro-average over agents", "end_to_end", sum(done) / len(done) if done else None, st="measured" if done else "not_computed",
        note=f"{len(done)} of {len(agents)} agents have verified cases" )
    for g in sorted({r["group"] for r in res}):
        s = f1([r for r in res if r["group"] == g]); add("M3", "Field-level F1 (eval set)", g, s["F1"] if s else None, s["cor"] if s else "", s["n"] if s else "", "measured" if s else "not_computed")
    ne = f1([r for r in res if r["language"] != "en"]); add("M3", "Non-English source group F1 (bonus rule)", "non_english", ne["F1"] if ne else None, st="measured" if ne else "not_computed")
    lo = [r for r in res if r.get("locator_ok") in ("Y", "N")]
    add("M4", "Provenance accuracy (locators confirmed by the checker)", "end_to_end", (sum(r["locator_ok"] == "Y" for r in lo) / len(lo)) if lo else None, sum(r["locator_ok"] == "Y" for r in lo) if lo else "", len(lo) if lo else "",
        st="measured" if lo else "not_computed", note="own check of page/row/column; the evaluator re-opens the source")
    mp = load_csv(os.path.join(ROOT, "docs", "source_agent_map.csv"))
    if mp:
        add("M1", "Source mapping coverage", "end_to_end", sum(bool(r["agent"]) for r in mp) / len(mp), sum(bool(r["agent"]) for r in mp), len(mp))
        add("M2", "Implementation coverage (done or partial)", "end_to_end", sum(r["status"] in ("done", "partial") for r in mp) / len(mp), sum(r["status"] in ("done", "partial") for r in mp), len(mp),
            note="regenerate the map with tools/make_source_map.py after new runs")
    lg = load_csv(os.path.join(ROOT, "logs", "automation_log.csv"))
    if lg:
        auto = [r for r in lg if r["status"] == "ok" and str(r["manual_interventions"]) in ("0", "")]
        add("M12", "Automation rate", "end_to_end", len(auto) / len(lg), len(auto), len(lg), note="from logs/automation_log.csv; includes sandbox runs, see the notes column")
        add("M11", "End-to-end runs reaching 'complete' (fault injection is shown live)", "end_to_end", sum(r["status"] == "ok" for r in lg) / len(lg), sum(r["status"] == "ok" for r in lg), len(lg),
            st="partial", note="completion only; correctness is M3; fault-injection result is not measured here")
    if os.path.exists(a.db):
        import checks; d = checks.run(a.db)
        add("M14", "Mandatory field completeness", "database", d["m14"], d["complete"], d["rows"], note="strict: 'unspecified' unit and empty crop/location count as missing")
        add("M15", "Validation pass rate", "database", d["m15"], d["valid"], d["rows"], note="rules in db/dictionary.md; LGD-code rule not applied (LGD codes not loaded)")
        add("M16", "Duplicate rate", "database", d["m16"], d["dup"], d["rows"])
    for i, m, why in (("M5", "Surprise-item F1", "only measured in the live session"), ("M6", "Tool selection accuracy", "not measured: agents are fixed code, no reference trajectories written yet"),
                      ("M7", "Tool input accuracy", "not measured: as M6"), ("M8", "Trajectory match", "not measured: as M6"), ("M9", "Task completion", "not measured: needs schema-valid output check per case"),
                      ("M10", "Groundedness", "not measured: values are copied from source cells; see M3/M4"), ("M13", "Redundant call ratio", "not measured: no LLM/tool-call trace"),
                      ("M17", "Live trace-back", "evaluator picks rows live; v_trace view supports it"), ("M18", "Adapter comparison", "not run: no LoRA/QLoRA adapter trained yet")):
        add(i, m, "end_to_end", None, st="not_measured", note=why)
    with open(os.path.join(HERE, "phase2_metrics.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(["metric_id", "metric", "scope", "value", "numerator", "denominator", "status", "notes"]); w.writerows(M)
    c = collections.Counter(r["status"] for r in res)
    print("cases:", len(res), dict(c))
    for m in M:
        if m[0] in ("M3", "M4", "M12", "M14", "M15", "M16"): print(f"  {m[0]:<4} {m[2]:<22} {m[3]!s:<8} {m[6]}")
    print("wrote eval/phase2_metrics.csv and eval/results/case_results.csv")
if __name__ == "__main__": main()
