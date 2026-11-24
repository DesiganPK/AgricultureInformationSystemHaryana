"""Tests for the LLM layer. No real model is needed: a stub function and a small local HTTP server stand in for the model, so these
tests check the plumbing and the safety checks (what is accepted, what is refused, what happens when the model fails).
They do NOT measure how good Qwen is; that is what finetune/ and eval/adapter_comparison.csv are for."""
import json, os, sqlite3, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
import llm, table_mapper as tm, doc_context as dc
from pdf_agent import DigitalPDFAgent
from haryana_pipeline import Supervisor, AGENTS

TITLE = "Table 5.1 Farm harvest prices of principal crops (Rs. per quintal)"
COLS = [dict(c=2, i=1, header="Paddy"), dict(c=3, i=2, header="Chilly"), dict(c=4, i=3, header="Caster Seed")]


def use_stub(fn):
    os.environ["AGRI_LLM"] = "stub"; llm.set_stub(fn); llm.reset()


def make_pdf(path, title, rows, spans=()):
    doc = SimpleDocTemplate(path, pagesize=A4); st = getSampleStyleSheet()
    t = Table(rows); t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)] + [("SPAN", a, b) for a, b in spans]))
    doc.build([Paragraph(title, st["Normal"]), Spacer(1, 8), t])


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); os.environ["AGRI_LLM_LOG"] = os.path.join(self.d, "llm_calls.jsonl")
        for k in ("AGRI_LLM_POLICY", "AGRI_LLM_MAX_CALLS", "AGRI_LLM_URL", "AGRI_LLM_MODEL"): os.environ.pop(k, None)
    def tearDown(self):
        os.environ["AGRI_LLM"] = "off"; llm.reset()

    # ---- the wrapper
    def test_parse_json_tolerates_wrapping_and_refuses_garbage(self):
        self.assertEqual(llm.parse_json('Here you go:\n```json\n{"a": {"b": "}"}}\n```'), {"a": {"b": "}"}})
        self.assertIsNone(llm.parse_json("I think the answer is wheat")); self.assertIsNone(llm.parse_json("[1, 2]"))
    def test_off_means_no_calls_and_rules_only(self):
        os.environ["AGRI_LLM"] = "off"; llm.reset()
        self.assertIsNone(llm.ask_json("x", "s", "u")); self.assertEqual(llm.STATS["calls"], 0)
        m, _, info = tm.map_columns(TITLE, COLS, row_kind="district")
        self.assertFalse(info["llm_asked"]); self.assertEqual([x["crop"] for x in m], ["Rice", "Chilly", "Caster Seed"])     # unknown headings kept as printed
    def test_invalid_json_is_retried_once_then_rules_are_used(self):
        use_stub(lambda s, u: "sorry, no JSON today")
        m, _, info = tm.map_columns(TITLE, COLS, row_kind="district")
        self.assertEqual((llm.STATS["calls"], llm.STATS["retries"], llm.STATS["valid"]), (2, 1, 0))
        self.assertEqual([x["crop"] for x in m], ["Rice", "Chilly", "Caster Seed"]); self.assertEqual(info["llm_filled"], 0)
        lines = [json.loads(l) for l in open(os.environ["AGRI_LLM_LOG"], encoding="utf-8")]
        self.assertEqual([(l["task"], l["valid_json"], l["retry"]) for l in lines], [("map_columns", False, 0), ("map_columns", False, 1)])
    def test_same_prompt_is_not_sent_twice_and_calls_are_capped(self):
        use_stub(lambda s, u: {"columns": []})
        tm.map_columns(TITLE, COLS, row_kind="district"); tm.map_columns(TITLE, COLS, row_kind="district")
        self.assertEqual((llm.STATS["calls"], llm.STATS["cache_hits"]), (1, 1))
        os.environ["AGRI_LLM_MAX_CALLS"] = "1"
        self.assertIsNone(llm.ask_json("other", "s", "a different prompt")); self.assertEqual(llm.STATS["over_cap"], 1)

    def test_model_that_cannot_load_is_tried_once_then_rules(self):        # 19 Nov: 26 calls each failed to load Qwen2ForCausalLM
        os.environ["AGRI_LLM"] = "hf"; os.environ["AGRI_LLM_QUIET"] = "1"; llm.reset()
        n = [0]
        def broken(*a):
            n[0] += 1; raise ModuleNotFoundError("Could not import module 'Qwen2ForCausalLM'")
        old = llm.CALLERS["hf"]; llm.CALLERS["hf"] = broken
        try:
            for i in range(5): self.assertIsNone(llm.ask_json("map_columns", "s", f"prompt {i}"))
        finally: llm.CALLERS["hf"] = old
        self.assertEqual(n[0], 1); self.assertTrue(llm._STATE["load_failed"].startswith("ModuleNotFoundError"))

    # ---- what is accepted from the model
    def test_known_crops_are_accepted_and_marked(self):
        use_stub(lambda s, u: {"row_label": "district", "columns": [{"c": 3, "crop": "Chillies", "measure": "price", "unit": "Rs/quintal"},
                                                                    {"c": 4, "crop": "castor", "measure": "price", "unit": None}]})
        m, _, info = tm.map_columns(TITLE, COLS, row_kind="district")
        self.assertEqual([(x["crop"], x["measure"], x["unit"]) for x in m], [("Rice", "price", "Rs/quintal"), ("Chillies", "price", "Rs/quintal"), ("Castor", "price", "Rs/quintal")])
        self.assertEqual([x["by"].get("crop") for x in m], [None, "llm", "llm"]); self.assertEqual(info["llm_filled"], 2)      # two crops; measure and unit were already known from the title
    def test_invented_crop_unit_and_column_are_refused(self):
        use_stub(lambda s, u: {"columns": [{"c": 2, "crop": "Dragonfruit", "measure": "happiness", "unit": "mm"}, {"c": 99, "crop": "Wheat"}, "junk"]})
        cols = [dict(c=2, i=1, header="Deep tubewells")]
        m, _, info = tm.map_columns("Table 9 Sources of irrigation, crop season 2011-12", cols, row_kind="year")
        self.assertEqual((m[0]["crop"], m[0]["measure"], m[0]["unit"], m[0]["by"]), (None, None, "unspecified", {}))      # nothing invented got in
        self.assertEqual(len(info["problems"]), 5)
    def test_unit_needs_its_words_printed_even_if_damaged(self):
        self.assertTrue(tm.unit_grounded("tonnes", "उर्वरक (टना ें में)")); self.assertFalse(tm.unit_grounded("mm", "औसत मासिक वर्षा"))
        self.assertTrue(tm.unit_grounded("000 ha", "Area ('000 hectares)")); self.assertFalse(tm.unit_grounded("000 ha", "Area (hectares)"))
    def test_prefer_policy_lets_a_valid_answer_override_rules(self):
        use_stub(lambda s, u: {"columns": [{"c": 2, "crop": "Wheat", "measure": "price", "unit": None}]})
        self.assertEqual(tm.map_columns(TITLE, COLS, row_kind="district")[0][0]["crop"], "Rice")                           # default 'fill': rules win
        os.environ["AGRI_LLM_POLICY"] = "prefer"; llm.reset()
        self.assertEqual(tm.map_columns(TITLE, COLS, row_kind="district")[0][0]["crop"], "Wheat")

    # ---- supervisor and document context
    def test_router_pick_must_be_able_to_open_the_file(self):
        prof = dict(kind="pdf", file="x.pdf", pages=10, pages_with_text=10, legacy_hindi_pages=9, unicode_hindi_pages=0, sample="ftyk lkaf[;dh; lkj.kh")
        use_stub(lambda s, u: {"agent": "hindi_ocr", "reason": "legacy font"})
        self.assertEqual((dc.choose_agent(prof)["agent"], dc.choose_agent(prof)["by"]), ("hindi_ocr", "llm"))
        use_stub(lambda s, u: {"agent": "structured_data", "reason": "looks tabular"})                                     # cannot open a PDF: refused
        d = dc.choose_agent(prof); self.assertEqual((d["agent"], d["by"]), ("hindi_ocr", "rules")); self.assertIn("not usable", d["reason"])
        use_stub(lambda s, u: {"agent": "hindi_ocr"})
        self.assertEqual(dc.choose_agent(dict(kind="xlsx", file="a.xlsx"))["by"], "rules"); self.assertEqual(llm.STATS["calls"], 0)   # one possible agent: no call
    def test_wrong_router_pick_is_corrected_by_the_agents(self):
        p = os.path.join(self.d, "en.pdf")
        make_pdf(p, "Table 2 Area under wheat in Haryana (000 hectares)", [["District", "2019-20", "2020-21"], ["Hisar", "310.5", "305.0"], ["Sirsa", "290.1", "288.4"]])
        use_stub(lambda s, u: {"agent": "hindi_ocr"} if "supervisor" in s else {"columns": []})
        from hindi_agent import HindiOCRAgent
        db = os.path.join(self.d, "t.db"); ag = dict(AGENTS); ag["digital_pdf"] = DigitalPDFAgent(db_path=db, schema="haryana_schema.sql"); ag["hindi_ocr"] = HindiOCRAgent(db_path=db, schema="haryana_schema.sql")
        sup = Supervisor([{"List of sources": "en.pdf", "URLs": "file://" + p, "Modalities": "PDF", "local_file": p}], ag)
        sup.jobs[0].agent = dc.choose_agent(dc.profile_pdf(p))["agent"]; self.assertEqual(sup.jobs[0].agent, "hindi_ocr"); sup.run()
        self.assertEqual((sup.jobs[0].agent, sup.jobs[0].status), ("digital_pdf", "ok"))
    def test_document_district_and_year_are_checked(self):
        text = "Statistical Abstract\nHissar district\n2010-11"
        use_stub(lambda s, u: {"district": "Hissar", "year": "2010-11"}); self.assertEqual(dc.llm_context(text), ("Hisar", "2010-11"))
        use_stub(lambda s, u: {"district": "Lahore", "year": "1999-00"}); self.assertEqual(dc.llm_context(text), (None, None))    # not a Haryana district; year not printed

    # ---- end to end on a PDF
    def _pdf(self):
        p = os.path.join(self.d, "crops.pdf")
        rows = [["District", "Wheat", "", "Chilly", ""], ["", "Area", "Production", "Area", "Production"], ["Hisar", "310.5", "1,400.2", "2.1", "3.0"],
                ["Sirsa", "290.1", "1,322.0", "-", ".."], ["Haryana", "600.6", "2,722.2", "2.1", "3.0"]]
        make_pdf(p, "Table 5.1 Area and production of principal crops (Area in 000 hectares, Production in 000 tonnes)", rows, [((1, 0), (2, 0)), ((3, 0), (4, 0))])
        return p
    def test_numbers_never_come_from_the_model(self):
        p = self._pdf(); key = lambda r: (r["page_no"], r["row_ref"], r["table_ref"])
        os.environ["AGRI_LLM"] = "off"; llm.reset()
        off = DigitalPDFAgent(db_path=os.path.join(self.d, "a.db"), schema="haryana_schema.sql").extract(p, "crops")
        self.assertEqual(len(off), 8)                                                                                       # 2 districts x 4 columns; the state total row is not stored
        self.assertEqual({(r["district_raw"], r["crop"], r["indicator"], r["value_num"], r["unit"]) for r in off if r["crop"] == "Wheat"},
                         {("Hisar", "Wheat", "area", 310.5, "000 ha"), ("Hisar", "Wheat", "production", 1400.2, "000 tonnes"),
                          ("Sirsa", "Wheat", "area", 290.1, "000 ha"), ("Sirsa", "Wheat", "production", 1322.0, "000 tonnes")})
        use_stub(lambda s, u: {"row_label": "district", "columns": [{"c": c["c"], "crop": "Chillies", "measure": "area", "unit": "000 ha", "value": 999} for c in json.loads(u)["columns"]]})
        on = DigitalPDFAgent(db_path=os.path.join(self.d, "b.db"), schema="haryana_schema.sql").extract(p, "crops")
        self.assertEqual({key(r): (r["value_num"], r["value_text"]) for r in off}, {key(r): (r["value_num"], r["value_text"]) for r in on})   # identical values and locators
        chil = [r for r in on if r["crop"] == "Chillies"]
        self.assertEqual(len(chil), 4); self.assertTrue(all(r["needs_review"] == 1 and r["confidence"] == 0.8 for r in chil))
        self.assertTrue(all(r["needs_review"] == 0 for r in on if r["crop"] == "Wheat"))                                    # rule-decided columns are untouched
    def test_pdf_without_tables_still_lands_in_the_database(self):
        p = os.path.join(self.d, "note.pdf"); doc = SimpleDocTemplate(p, pagesize=A4)
        doc.build([Paragraph("Advisory for farmers: sow wheat variety KRL 210 in saline soils before 15 November.", getSampleStyleSheet()["Normal"])])
        db = os.path.join(self.d, "c.db"); os.environ["AGRI_LLM"] = "off"; llm.reset()
        out = DigitalPDFAgent(db_path=db, schema="haryana_schema.sql").run({"List of sources": "note", "URLs": "file://" + p, "local_file": p})
        self.assertEqual((out["inserted"], out["text_pages"]), (0, 1))
        self.assertIn("KRL 210", sqlite3.connect(db).execute("select content from text_fact where topic='page_text'").fetchone()[0])

    # ---- the HTTP path (Ollama protocol), against a local stand-in server
    def test_ollama_protocol(self):
        seen = []
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, obj):
                b = json.dumps(obj).encode(); self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
            def do_GET(self): self._send({"models": [{"name": "qwen2.5:1.5b"}]})
            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); seen.append(req)
                self._send({"message": {"content": '{"agent": "digital_pdf", "reason": "english text"}'}, "prompt_eval_count": 120, "eval_count": 14})
        srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            os.environ.update(AGRI_LLM="auto", AGRI_LLM_URL=f"http://127.0.0.1:{srv.server_port}"); llm.reset()
            self.assertEqual(llm.backend()[:2], ("ollama", "qwen2.5:1.5b"))                                                 # auto finds the running server and the model
            d = dc.choose_agent(dict(kind="pdf", file="x.pdf", pages=3, pages_with_text=3, legacy_hindi_pages=0, unicode_hindi_pages=0, sample="District wise rainfall"))
            self.assertEqual((d["agent"], d["by"]), ("digital_pdf", "llm"))
            self.assertEqual((seen[0]["model"], seen[0]["format"], seen[0]["options"]["temperature"]), ("qwen2.5:1.5b", "json", 0))
            self.assertEqual((llm.STATS["tokens_in"], llm.STATS["tokens_out"]), (120, 14))
        finally: srv.shutdown()
        os.environ.update(AGRI_LLM="auto", AGRI_LLM_URL="http://127.0.0.1:9"); llm.reset()
        self.assertEqual(llm.backend()[0], "off"); self.assertIn("not reachable", llm.describe())                           # no server: rules only, and it says why


if __name__ == "__main__": unittest.main(verbosity=2)
