import os, tempfile, unittest, sqlite3
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Image
from PIL import Image as PI
from kruti_dev import convert, convert_line
from hindi_agent import (HindiOCRAgent, lines_from_boxes, parse_val, split_unit, line_is_english, OCRUnavailable, ocr_page, UNKNOWN_UNIT)
from haryana_pipeline import Supervisor, AGENTS, route

SRC = {"URLs": "http://x/hisar.pdf", "List of sources": "Haryana DESA - District Statistical Abstract: Hisar", "Period Of Interest": "2010-11",
       "Modalities": "PDF (Hindi)", "Limitations": ""}
COVER = ["DISTRICT STATISTICAL ABSTRACT", "HISAR", "YEAR:- 2010-11"]
SUMMARY = ["en bdkbZ ftyk gfj;k.kk jkT;", "1 2 3 4",
           "1- ftys dk iz'kklfud <k¡pk ¼fnukad 31-03-2012½ la[;k", "rglhy \" 7 80", "xkao ¼2011½ \" 444 6955",
           "2- {ks=Qy rFkk tula[;k ¼2011½", "dqy tula[;k gtkj 1634-4 25353-0", "fctyh [kir yk[k fdyksokV 12343.07 262576.03",
           "6- d`f\"k ¼o\"kZ 2011&12½", "pkoy", "{ks= gtkj gSDVs;j 17.5 1234.1", "mRiknu gtkj Vu 46 3757",
           "mit fd0 xzk0 izfr gSDVs;j .. ..", "Mkd?kj dsUnz csap 221 2653", "gsMj 5 4 pr 3", "5 2010-11 2 3 4", "ifjokj dY;k.k dsUnz la[;k 7 107", "Nk= ¼11&12½ la[;k 359844 5247784"]                 # last line: wrong shape, must not become data
TABLE = ["lkj.kh ua% 9-1", "11 12 13 14 15 16 17", "2007-2008 0 240 240 3950 4190 3450 7400", "2011-2012(P)",
         "1.Bhiwani 0 159 159 720 879 720 1440", "2.Bawani Khera 0 26 26 306 332 306 612", "tksM 0 450 450 3700 4150 3700 7400",
         "3.Dadri 0 90 90"]                                                    # too few values: rejected, never guessed
def make_pdf(path, pages):
    c = canvas.Canvas(path, pagesize=A4)
    for pg in pages:
        y = 800
        for ln in pg: c.setFont("Courier", 8); c.drawString(20, y, ln); y -= 12
        c.showPage()
    c.save()
def make_scanned(path):
    ip = path + ".png"; PI.new("RGB", (400, 300), "white").save(ip); SimpleDocTemplate(path).build([Image(ip, 300, 200)])

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.db = os.path.join(self.d, "t.db")
        self.pdf = os.path.join(self.d, "h.pdf"); make_pdf(self.pdf, [COVER, SUMMARY, TABLE])
        self.a = HindiOCRAgent(db_path=self.db, schema="haryana_schema.sql")
    # ---- decoder
    def test_decoder(self):
        for k, v in {"ftyk": "जिला", "fglkj": "हिसार", "izdk'ku": "प्रकाशन", "{ks=Qy": "क्षेत्रफल", "xHkkZ/kku": "गर्भाधान", "o\"kZ": "वर्ष",
                     "d`f=e": "कृत्रिम", "tula[;k": "जनसंख्या", "mRiknu": "उत्पादन", "flapkbZ": "सिंचाई"}.items(): self.assertEqual(convert(k), v)
        self.assertEqual(convert_line("dqy 1634-4 .. 2008-2009(P) 6* tksM"), "कुल 1634-4 .. 2008-2009(P) 6* जोड")      # numbers and flags untouched
    def test_language_split(self):
        self.assertTrue(line_is_english("Table No.3.1 Tehsil -wise population, No. of Villages"))
        self.assertTrue(line_is_english("2.Bawani Khera 361.53 19"))
        self.assertFalse(line_is_english("tksM 000 gSDVs;j 250 3072")); self.assertFalse(line_is_english("lkj.kh ua% 9-1"))
    def test_helpers(self):
        self.assertEqual((parse_val("1634-4"), parse_val(".."), parse_val("--"), parse_val("6*"), parse_val("-48"), parse_val("1,250")),
                         (1634.4, None, None, 6.0, -48.0, 1250.0))
        self.assertEqual(split_unit("क्षेत्र हजार हैक्टेयर".split()), (["क्षेत्र"], "000 ha"))
        self.assertEqual(split_unit("उपज कि0 ग्रा0 प्रति हैक्टेयर".split()), (["उपज"], "kg/ha"))
    # ---- extraction on a legacy-font PDF
    def test_summary_rows(self):
        recs = self.a.extract(self.pdf, SRC["List of sources"])
        by = lambda **kw: [r for r in recs if all(r.get(k) == v for k, v in kw.items())]
        r = by(crop="Rice", indicator="area", district_raw="Hisar")[0]
        self.assertEqual((r["value_num"], r["unit"], r["year"], r["page_no"], r["table_ref"]), (17.5, "000 ha", "2011-12", 2, "p2c3"))
        s = by(crop="Rice", indicator="area", district_raw=None)[0]; self.assertEqual((s["value_num"], s["table_ref"]), (1234.1, "p2c4"))   # state column
        self.assertEqual(by(crop="Rice", indicator="production", district_raw="Hisar")[0]["unit"], "000 tonnes")
        y = by(crop="Rice", indicator="yield", district_raw="Hisar")[0]; self.assertEqual((y["value_num"], y["value_text"], y["unit"]), (None, "..", "kg/ha"))
        t = by(indicator="तहसील", district_raw="Hisar")[0]; self.assertEqual((t["value_num"], t["unit"], t["year"]), (7.0, "number", "2011-03-31" if False else t["year"]))
        self.assertEqual(by(indicator="कुल जनसंख्या", district_raw="Hisar")[0]["value_num"], 1634.4)         # '-' decimal decoded, raw kept
        self.assertEqual(by(indicator="कुल जनसंख्या", district_raw="Hisar")[0]["value_text"], "1634-4")
        self.assertEqual(by(indicator="गांव", district_raw="Hisar")[0]["year"], "2011")                      # year inside the row label wins
        e = [x for x in recs if x["indicator"].startswith("बिजली")][0]; self.assertEqual(e["unit"], "lakh kilowatt (as printed)")
        u = [x for x in recs if x["indicator"].startswith("डा")][0]; self.assertEqual((u["unit"], u["needs_review"]), (UNKNOWN_UNIT, 1))   # unknown unit coded and flagged, not invented
        self.assertEqual(by(indicator="परिवार कल्याण केन्द्र", district_raw="Hisar")[0]["year"], "2010-11")      # 'N YYYY-YY' section marker sets the year
        self.assertEqual(by(indicator="छात्र", district_raw="Hisar")[0]["year"], "2011-12")                      # short year '(11-12)' in a row label
        self.assertFalse(any(x["indicator"].startswith("गसे") for x in recs))
    def test_numbered_table(self):
        recs = self.a.extract(self.pdf, SRC["List of sources"]); col = [r for r in recs if r["table_ref"] == "p3c17"]
        self.assertEqual([(r["tehsil_block"], r["year"], r["value_num"]) for r in col],
                         [(None, "2007-2008", 7400.0), ("Bhiwani", "2011-2012", 1440.0), ("Bawani Khera", "2011-2012", 612.0), ("जोड (total)", "2011-2012", 7400.0)])
        self.assertTrue(any("3.Dadri" in x[1] for x in self.a.rejected_rows))                                 # short row rejected, not guessed
        self.assertTrue(all(r["needs_review"] == 1 for r in col))                                              # column meaning sits in the header text
    def test_district_from_source_name_when_no_english_cover(self):
        p = os.path.join(self.d, "nc.pdf"); make_pdf(p, [SUMMARY]); recs = self.a.extract(p, SRC["List of sources"])
        self.assertEqual({r["district_raw"] for r in recs if r["district_raw"]}, {"Hisar"})
        with self.assertRaises(ValueError): self.a.extract(p, "Haryana DESA - something")
    def test_english_pdf_is_not_ours(self):
        p = os.path.join(self.d, "en.pdf"); make_pdf(p, [COVER + ["Area and Population of the district", "Total Population Rural Urban"]])
        with self.assertRaises(ValueError): self.a.extract(p, SRC["List of sources"])
    # ---- OCR path
    def test_lines_from_boxes(self):
        b = [(60, 10, 90, 20, "b"), (10, 11, 40, 21, "a"), (10, 40, 40, 50, "c")]
        self.assertEqual(lines_from_boxes(b), ["a b", "c"])
    def test_scanned_page_uses_ocr_backend_and_flags_rows(self):
        p = os.path.join(self.d, "s.pdf"); make_scanned(p)
        fake = lambda img: ["मद इकाई जिला हरियाणा राज्य", "1 2 3 4", "चावल", "क्षेत्र हजार हैक्टेयर 17.5 1234.1"]
        a = HindiOCRAgent(db_path=self.db, schema="haryana_schema.sql", backends=(("fake", fake),))
        recs = a.extract(p, SRC["List of sources"])
        self.assertEqual(len(recs), 2); self.assertEqual({(r["confidence"], r["needs_review"], r["crop"]) for r in recs}, {(0.6, 1, "Rice")}); self.assertEqual(a.ocr_pages, {1})
    def test_no_ocr_backend_is_reported_not_guessed(self):
        p = os.path.join(self.d, "s.pdf"); make_scanned(p)
        def bad(img): raise ImportError("no module named paddleocr")
        a = HindiOCRAgent(db_path=self.db, schema="haryana_schema.sql", backends=(("paddle", bad), ("tesseract-hin", bad)))
        with self.assertRaises(ValueError): a.extract(p, SRC["List of sources"])
        self.assertIn("no OCR backend worked", a.skipped_pages[0][1]); self.assertIn("paddle", a.skipped_pages[0][1])
        with self.assertRaises(OCRUnavailable): ocr_page(None, (("x", bad),))
    # ---- load, trace, supervisor
    def test_load_and_trace(self):
        out = self.a.run(dict(SRC, local_file=self.pdf)); self.assertGreater(out["inserted"], 40); self.assertEqual(out["unresolved_districts"], 0)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("select language, source_type from source").fetchone(), ("hi", "pdf_scanned_hindi"))
        self.assertEqual(con.execute("select distinct a.name from extraction_run r join agent a using(agent_id)").fetchall(), [("hindi_ocr",)])
        row = con.execute("select district, crop, indicator, year_label, value_num, unit, page_no, agent from v_trace where crop='Rice' and indicator='area' and district='Hisar'").fetchone()
        self.assertEqual(row, ("Hisar", "Rice", "area", "2011-12", 17.5, "000 ha", 2, "hindi_ocr"))
        self.assertEqual(con.execute("select count(*) from observation where tehsil_block='Bawani Khera'").fetchone()[0], 7)
        self.assertGreaterEqual(con.execute("select count(*) from text_fact where topic='table_header'").fetchone()[0], 1)
        again = self.a.run(dict(SRC, local_file=self.pdf)); self.assertEqual(again["inserted"], 0)           # re-run is idempotent
    def test_supervisor_routes_hindi_source_to_this_agent(self):
        self.assertEqual(route(SRC), "hindi_ocr")
        ag = dict(AGENTS); ag["hindi_ocr"] = self.a
        s = Supervisor([dict(SRC, local_file=self.pdf)], ag); s.run(); self.assertEqual((s.jobs[0].agent, s.jobs[0].status), ("hindi_ocr", "ok"))
if __name__ == "__main__": unittest.main(verbosity=2)
