import os, tempfile, unittest, sqlite3
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Image
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from PIL import Image as PI
from pdf_agent import DigitalPDFAgent, parse_value, legacy_font_score, merge_dots
from haryana_pipeline import Supervisor, Agent, AGENTS, Reroute
DES = {"URLs":"http://x/FHP.pdf","List of sources":"Ministry of Agriculture (DES) - District-wise Farm Harvest Prices, Haryana","Period Of Interest":"1998-2001"}
IMD = {"URLs":"http://x/monsoon.pdf","List of sources":"IMD Chandigarh - District-wise Haryana monsoon rainfall bulletin","Period Of Interest":"2024-2026"}
HDR = ["2.7. FARM HARVEST PRICES OF PRINCIPAL CROPS IN INDIA. STATE HARYANA DISTRICT-WISE", "(Rs. per quintal)",
       "District name Year Paddy Jowar Bajra Maize Ragi Wheat Barley Gram Tur Sugar Potato Tobacco Ground", "Arhar Raw nut",
       "(1) (2) (3) (4) (5) (6) (7) (8) (9) (10) (11) (12) (13) (14) (15)"]
P1 = ["AMBALA 1998-99 1320r .. .. 550 .. 550 470 .. .. 1075 250 .. ..",
      "1999-00 525 340 350 500 .. 590 400 1200 .. 1000 200 .. ..",
      "2000-01 1128r .. .. 510 .. 575 .. .. .. 1000 550 .. ..",
      "KARNAL 2000-01 525 .. 600 700 .. . . 580 .. 1800 .. 1000 .. ..",       # lone dots in the Ragi column
      "PANIPAT 1998-99 1800r .. ..",                                           # damaged row: too few values
      "TOTAL HARYANA 1998-99 1 2 3 4 5 6 7 8 9 10 11 12 13"]
P2 = ["MOHINDERGARH 1998-99 500 .. .. .. .. 550 .. .. .. .. .. .. ..", "SIRSA 1998-99 1900r .. 500 .. .. 550 470 1250 .. .. .. .. 1525"]
HDR2 = ["2.7. FARM HARVEST PRICES OF PRINCIPAL CROPS IN INDIA. STATE HARYANA DISTRICT-WISE (Contd...)", "(Rs. per quintal)",
        "District name Year Rape/ Sesamum Linseed Caster Toria Cotton Jute Sann- Pepper Ginger Chilly Turmeric Mesta", "Mustard Seed hemp",
        "(1) (2) (16) (17) (18) (19) (20) (21) (22) (23) (24) (25) (26) (27) (28)"]
P3 = ["AMBALA 1998-99 1250 .. .. .. .. .. .. .. .. .. 3000 .. ..", "1999-00 950 .. .. .. 920 1750D .. .. .. .. .. .. .."]
def make_des(path):
    c = canvas.Canvas(path, pagesize=landscape(A4))
    for hdr, page in ((HDR, P1), (HDR, P2), (HDR2, P3)):
        y = 560
        for ln in hdr + page: c.setFont("Courier", 7); c.drawString(20, y, ln); y -= 12
        c.showPage()
    c.save()
def make_imd(path):
    st = getSampleStyleSheet(); G = TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.black),("SPAN",(1,0),(7,0))])
    rows = [["","District wise rainfall in Haryana during Monsoon- 2026","","","","","",""],
            ["","DISTRICT","","ACTUAL","","NORMAL","","DEPARTURE"],
            ["","AMBALA","","426.2","","820.7","","-48"], ["","CHARKHI DADRI","","346.2","","400","","-13"],
            ["","CHANDIGARH","","721.2","","844.9","","-15"], ["","HISAR","","228.9","","299.9","","-24"]]
    rows2 = [["","Month","","actual(mm)","","fall aagctauianl s(tm imts) nor","","% departure"],
             ["","June","","30.5","","55.0","","-45"], ["","Season (Jun-Sep)","","322.5","","430.7","","-25"],
             ["","eceived 389.8 mm and June was in 1918","","","","","","x"]]
    SimpleDocTemplate(path).build([Paragraph("Date: Thursday, 1 October 2026. BRIEF SOUTHWEST MONSOON REPORT", st["Normal"]), Table(rows, style=G),
                                   Paragraph("Month wise", st["Normal"]), Table(rows2, style=TableStyle([("GRID",(0,0),(-1,-1),0.5,colors.black)]))])
def make_text(path, lines):
    c = canvas.Canvas(path); y = 800
    for ln in lines: c.setFont("Courier", 10); c.drawString(30, y, ln); y -= 14
    c.save()
def make_scanned(path):
    ip = path + ".png"; PI.new("RGB", (400, 300), "white").save(ip); SimpleDocTemplate(path).build([Image(ip, 300, 200)])

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.a = DigitalPDFAgent(db_path=os.path.join(self.d, "t.db"), schema="haryana_schema.sql")
        self.des = os.path.join(self.d, "des.pdf"); make_des(self.des)
    def test_helpers(self):
        self.assertEqual((parse_value("1320r"), parse_value(".."), parse_value("-48"), parse_value("1,250")), ((1320.0,"r"), (None,""), (-48.0,""), (1250.0,"")))
        self.assertEqual(merge_dots(["1", ".", ".", "..", "."]), ["1", "..", "..", "."])
    def test_legacy_font_detector(self):
        hisar = ("ftyk Lakkf[;dh; lkjka\"k | fglkj | 2010&11 | ftyk lkaf[;dh; dk;kZy; | fglkj vkeq[k | izLrqr laLdj.k ftyk lakf[;dh; lkjka'k fglkj dk chlokaW izdk'ku gSAblesa")
        self.assertGreater(legacy_font_score(hisar), 0.2)
        self.assertEqual(legacy_font_score(" ".join(HDR + P1)), 0.0)
        self.assertEqual(legacy_font_score("Mathematical Reasoning Under Token Budgets across Indic Languages"), 0.0)
    def test_des_lines(self):
        recs = self.a.extract(self.des, DES["List of sources"])
        self.assertEqual(len(recs), 39 + 13 + 13 + 13 + 26)            # Ambala 3x13, Karnal, Mohindergarh, Sirsa, + second crop block (2 rows)
        self.assertEqual(self.a.rejected_rows, [(1, P1[4])])             # Panipat row rejected, not guessed
        r = [x for x in recs if x["district_raw"]=="AMBALA" and x["year"]=="1998-99" and x["crop"]=="Paddy"][0]
        self.assertEqual((r["value_num"], r["value_text"], r["unit"], r["page_no"]), (1320.0, "1320r", "Rs/quintal", 1))
        self.assertEqual({x["crop"] for x in recs}, {"Paddy","Jowar","Bajra","Maize","Ragi","Wheat","Barley","Gram","Tur Arhar","Sugar Raw","Potato","Tobacco","Ground nut",
            "Rape/Mustard","Sesamum","Linseed","Caster Seed","Toria","Cotton","Jute","Sann-hemp","Pepper","Ginger","Chilly","Turmeric","Mesta"})
        refs = {(x["crop"], x["table_ref"]) for x in recs}
        self.assertIn(("Paddy","p1c3"), refs); self.assertIn(("Rape/Mustard","p3c16"), refs); self.assertIn(("Mesta","p3c28"), refs)   # PDF's own column numbers
        self.assertEqual(len([x for x in recs if x["district_raw"]=="AMBALA" and x["year"]=="2000-01"]), 13)  # district carried to year rows
        k = [x for x in recs if x["district_raw"]=="KARNAL"]; self.assertEqual(len(k), 13)                    # lone dots repaired
        self.assertFalse(any("TOTAL" in x["district_raw"] for x in recs))
    def test_des_load_and_trace(self):
        out = self.a.load(self.a.extract(self.des, DES["List of sources"]), DES)
        self.assertEqual(out["unresolved_districts"], 0)               # MOHINDERGARH resolves via alias
        con = sqlite3.connect(self.a.db_path)
        row = con.execute("select district,crop,year_label,value_num,page_no,agent from v_trace where district='Ambala' and crop='Paddy' and year_label='1998-99'").fetchone()
        self.assertEqual(row, ("Ambala","Paddy","1998-99",1320.0,1,"digital_pdf"))
        self.assertEqual(con.execute("select count(*) from observation where district_raw='MOHINDERGARH' and district_id is not null").fetchone()[0], 13)
    def test_imd_ruled_with_ghost_columns(self):
        p = os.path.join(self.d, "imd.pdf"); make_imd(p)
        recs = self.a.extract(p, IMD["List of sources"])
        self.assertEqual(len(recs), 9)                                  # Ambala, Charkhi Dadri, Hisar x 3; Chandigarh skipped
        r = [x for x in recs if x["district_raw"]=="AMBALA" and x["indicator"]=="rainfall_departure"][0]
        self.assertEqual((r["value_num"], r["unit"], r["as_of"], r["year"]), (-48.0, "%", "2026-10-01", "2026"))
        self.assertEqual({x["unit"] for x in recs if x["indicator"]=="rainfall_actual"}, {"mm"})
        self.assertEqual({x["indicator"] for x in recs}, {"rainfall_actual","rainfall_normal","rainfall_departure"})   # garbled header ignored
        self.assertEqual(sorted(r[1][:6] for r in self.a.rejected_rows), ["June","Season","eceive"])        # month rows and debris logged, not stored
    def test_as_of_keeps_two_bulletins(self):
        p = os.path.join(self.d, "imd.pdf"); make_imd(p)
        recs = self.a.extract(p, IMD["List of sources"]); self.a.load(recs, IMD)
        for r in recs: r["as_of"] = "2026-10-15"; r["value_num"] += 1       # a later bulletin, same URL
        out = self.a.load(recs, IMD); self.assertEqual((out["inserted"], out["duplicates"]), (9, 0))
    def test_local_file_keeps_source_url(self):
        out = self.a.run(dict(DES, local_file=self.des))                      # no download needed
        self.assertEqual(out["inserted"], 104)
        con = sqlite3.connect(self.a.db_path)
        self.assertEqual(con.execute("select url from source").fetchone()[0], DES["URLs"]); con.close()
    def test_legacy_pdf_reroutes(self):
        p = os.path.join(self.d, "hisar.pdf")
        make_text(p, ["ftyk Lakkf[;dh; lkjka\"k", "fglkj 2010&11", "vkeq[k izLrqr laLdj.k ftyk lakf[;dh; lkjka'k fglkj dk chlokaW", "izdk'ku gSAblesa ftyk fglkj dh vkfFkZd fLFkfr ij yxHkx lHkh"])
        with self.assertRaises(Reroute) as cm: self.a.extract(p, "x")
        self.assertEqual(cm.exception.agent, "hindi_ocr")
    def test_scanned_pdf_reroutes(self):
        sp = os.path.join(self.d, "s.pdf"); make_scanned(sp)
        with self.assertRaises(Reroute): self.a.extract(sp)
    def test_supervisor_hands_off_to_ocr(self):
        class Scan(DigitalPDFAgent):
            def run(s, src): raise Reroute("hindi_ocr", "no text layer")
        class Ocr(Agent):
            name="hindi_ocr"
            def run(s, src): pass
        a = dict(AGENTS); a["digital_pdf"]=Scan(); a["hindi_ocr"]=Ocr()
        s = Supervisor([{"List of sources":"x","URLs":"u","Modalities":"PDF","Limitations":""}], a); s.run(); j = s.jobs[0]
        self.assertEqual((j.agent, j.status), ("hindi_ocr", "ok")); self.assertIn("rerouted", j.log[0])
unittest.main(verbosity=2)
