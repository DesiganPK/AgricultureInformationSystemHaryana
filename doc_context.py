"""Document-level decisions: what kind of file this is, which agent should take it, and which district / year it is about.
Each decision is made by fixed rules first. The LLM (llm.py) is asked where the rules allow a choice, and its answer is only
used if it passes the checks written here. With the LLM off, everything still works on rules alone."""
import os, re
import llm
from district_names import DISTRICTS, canonical

AGENTS_FOR = {"pdf": ["digital_pdf", "hindi_ocr"], "xlsx": ["structured_data"], "xls": ["structured_data"], "csv": ["structured_data"], "html": ["web_content"]}
LEGACY_WORDS = frozenset(["ftyk", "la[;k", "rglhy", "o\"kZ", "izfr", "{ks=", "tksM", "rFkk", "lkj.kh", "mRiknu", "{ks=Qy", "gtkj", "dqy", "esa", "vkSj", "gSDVs;j",
                          "ljdkjh", "xkao", "mit", "tula[;k", "ds", "dh", "dk", "dks", "ls", "gS", "gSa", "fd0", "eh0", "xzk0", "ek=k", "ftys", "Qlyksa"])
DEVANAGARI = re.compile(r"[ऀ-ॿ]")


def legacy_page(text):
    """True if the page's text layer is legacy-font Hindi (Kruti Dev): Latin-looking words with symbols inside them, or several
    known legacy words. Number-heavy table pages have few words, so the known-word test matters there."""
    toks = [t for t in text.split() if len(t) >= 2 and not any(ch.isdigit() for ch in t)]
    if not toks: return False
    odd = sum(bool(re.search(r"[A-Za-z][\[\];\"”“'’&{}<>^@/+=][A-Za-z]|[A-Za-z]\.[A-Za-z]|[{}=\[\]]", t)) for t in toks if len(t) >= 3)
    known = sum(t in LEGACY_WORDS for t in toks)
    return odd / len(toks) > 0.2 or known >= 3


def profile_pdf(path, max_pages=400):
    """Facts about a PDF that routing needs. No model."""
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages); texts = []
        for p in pdf.pages[:max_pages]:
            try: texts.append(p.extract_text() or "")
            except Exception: texts.append("")
    with_text = sum(len(t.strip()) >= 20 for t in texts)
    legacy = sum(legacy_page(t) for t in texts); uni = sum(len(DEVANAGARI.findall(t)) > 20 for t in texts)
    sample = next((t for t in texts if len(t.strip()) >= 80), texts[0] if texts else "")
    return dict(kind="pdf", file=os.path.basename(path), pages=n, pages_with_text=with_text, legacy_hindi_pages=legacy, unicode_hindi_pages=uni,
                sample=re.sub(r"\s+", " ", sample)[:500], texts=texts)


def route_rules(profile, legacy_share=0.15):
    """Fixed routing. Returns (agent, reason)."""
    k = profile["kind"]
    if k != "pdf": return AGENTS_FOR[k][0], f"{k} file"
    n = max(profile["pages"], 1)
    if profile["pages_with_text"] == 0: return "hindi_ocr", "no page has a text layer (scanned): OCR path"
    if profile["legacy_hindi_pages"] >= max(1, -(-n * int(legacy_share * 100) // 100)): return "hindi_ocr", f"{profile['legacy_hindi_pages']} of {n} pages are legacy-font Hindi"
    if profile["pages_with_text"] < 0.5 * n: return "hindi_ocr", f"only {profile['pages_with_text']} of {n} pages have a text layer"
    return "digital_pdf", f"text layer on {profile['pages_with_text']} of {n} pages, {profile['legacy_hindi_pages']} legacy-font pages"


ROUTER_SYSTEM = ("You are the supervisor of a data pipeline for Haryana (India) agriculture statistics. Choose the agent for one input file. "
                 "Agents: \"structured_data\" reads Excel and CSV files; \"digital_pdf\" reads PDFs whose text layer is normal English text; "
                 "\"hindi_ocr\" reads PDFs that are scanned images (no text layer) or whose text layer is Hindi in a legacy font, which looks like "
                 "meaningless Latin letters with symbols inside words (for example: ftyk lkaf[;dh; lkj.kh {ks=Qy). "
                 "Answer with one JSON object only: {\"agent\": \"...\", \"reason\": \"<short>\"}")


def router_prompt(profile):
    import json
    return json.dumps({k: profile[k] for k in ("kind", "file", "pages", "pages_with_text", "legacy_hindi_pages", "unicode_hindi_pages", "sample") if k in profile}, ensure_ascii=False)


def choose_agent(profile):
    """Supervisor decision. Returns dict(agent, by, reason, rule_agent). The LLM may only pick an agent that can open this file type;
    a wrong pick is still caught later, because each agent checks the content itself and hands the file over (Reroute)."""
    rule_agent, why = route_rules(profile)
    allowed = AGENTS_FOR[profile["kind"]]
    out = dict(agent=rule_agent, by="rules", reason=why, rule_agent=rule_agent)
    if len(allowed) < 2 or not llm.enabled(): return out
    obj = llm.ask_json("route", ROUTER_SYSTEM, router_prompt(profile), max_tokens=120, doc=profile.get("file", ""))
    pick = obj.get("agent") if isinstance(obj, dict) else None
    if pick in allowed:
        llm.note(True); out.update(agent=pick, by="llm", reason=str(obj.get("reason", ""))[:160] + ("" if pick == rule_agent else f" (rules said {rule_agent})"))
    else:
        llm.note(False); out["reason"] = why + (f"; LLM answer {pick!r} not usable" if obj is not None else "; LLM gave no usable answer")
    return out


# ------------------------------------------------------------------ district / year of a document
HI_DISTRICTS = {"अंबाला": "Ambala", "अम्बाला": "Ambala", "भिवानी": "Bhiwani", "चरखीदादरी": "Charkhi Dadri", "फरीदाबाद": "Faridabad", "फतेहाबाद": "Fatehabad",
    "गुरुग्राम": "Gurugram", "गुडगांव": "Gurugram", "गुड़गांव": "Gurugram", "हिसार": "Hisar", "झज्जर": "Jhajjar", "जींद": "Jind", "जीन्द": "Jind", "कैथल": "Kaithal",
    "करनाल": "Karnal", "कुरुक्षेत्र": "Kurukshetra", "महेंद्रगढ़": "Mahendragarh", "महेन्द्रगढ़": "Mahendragarh", "नूंह": "Nuh", "मेवात": "Nuh", "पलवल": "Palwal",
    "पंचकूला": "Panchkula", "पानीपत": "Panipat", "रेवाड़ी": "Rewari", "रोहतक": "Rohtak", "सिरसा": "Sirsa", "सोनीपत": "Sonipat", "यमुनानगर": "Yamunanagar"}
EDITION = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*[-–&]\s*(\d{2}|(?:19|20)\d{2})(?!\d)")


def district_from_name(*names):
    """District named in the file name or the source name ('PDF_bhiwani_crop.pdf', 'District Statistical Abstract: Hisar')."""
    for nm in names:
        words = re.findall(r"[A-Za-z]+", os.path.splitext(os.path.basename(nm or ""))[0])
        for n in (2, 1):
            for i in range(len(words) - n + 1):
                d = canonical(" ".join(words[i:i + n]))
                if d: return d
    return None


def district_from_text(text):
    """The one district an English text is about: named at least twice and clearly more often than any other. None if it is not clear
    (a state-level table names every district)."""
    low = text.lower(); counts = {}
    for d in DISTRICTS:
        n = len(re.findall(r"\b" + re.escape(d.lower()) + r"\b", low))
        if n: counts[d] = n
    if not counts: return None
    best = sorted(counts.items(), key=lambda kv: -kv[1])
    if best[0][1] >= 2 and (len(best) == 1 or best[0][1] >= 3 * best[1][1]) and len(counts) <= 3: return best[0][0]
    return None


CONTEXT_SYSTEM = ("You read the first lines of a statistics document from Haryana, India. Say which single district the whole document is about and "
                  "its reference year. Districts: " + ", ".join(DISTRICTS) + ". Use null when the document covers the whole state or many districts, "
                  "or when the text does not say. Answer with one JSON object only: {\"district\": \"<name or null>\", \"year\": \"<YYYY-YY or null>\"}")


def llm_context(sample_text, doc=""):
    """Ask the LLM for the district / year. Accepted only if the district is one of the 22 Haryana districts and the year's digits are printed in the text."""
    if not llm.enabled() or not sample_text.strip(): return None, None
    obj = llm.ask_json("doc_context", CONTEXT_SYSTEM, sample_text[:1500], max_tokens=80, doc=doc)
    if not isinstance(obj, dict): llm.note(False); return None, None
    d = canonical(obj["district"]) if isinstance(obj.get("district"), str) else None
    y = obj.get("year") if isinstance(obj.get("year"), str) else None
    if y:
        m = EDITION.search(y) or re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", y)
        y = None if not m or m.group(1) not in sample_text else re.sub(r"\s*[–&]\s*", "-", m.group(0)).replace(" ", "")
    llm.note(bool(d or y))
    return d, y
