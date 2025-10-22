import os, tempfile, unittest, sqlite3, shutil
import pandas as pd
from structured_agent import StructuredDataAgent, parse_column
from district_names import canonical
SRC = {"URLs":"http://x/test.xlsx","List of sources":"test","Period Of Interest":"2010"}

def make_icrisat(path):
    """Mimics the real ICRISAT area-production sheet: notes, then crop/season/measure/code header rows."""
    W = 9; R = lambda *a: list(a) + [None]*(W-len(a))
    rows = [R("District wise and season wise yearly area and production"), R("Units: Area in (000 Ha) : Production in (000 t)"),
      R("Note: Production data of Cotton, Jute and Mesta are reported in Bales"), R("Source:","DACNET"), R("Note: Blank data values replaced with . (dot)"),
      R(None,None,None,None,None,None,None,"(Nuts/ha)"),
      R(None,None,None,None,None,"Rice","Rice","Wheat","Cotton(lint)"),
      R("State",None,"District",None,None,"Kharif","Total","Rabi","Total"),
      R("code1","State name","code","District name","Year","Area","Production","Yield","Production"),
      R("State Code","STNAME","DIST","DISTNAME","YEAR","RICE_KA","RICE_TP","WHEAT_RY","COTTON_TP"),
      R(6,"Haryana",1,"Hissar",1997,"12.5","40.1","3100","8"),
      R(6,"Haryana",2,"Gurgaon",1997,".","3.0","2900","1"),
      R(6,"Haryana",3,"Atlantis",1997,"1","2","3","4"),
      R(1,"Punjab",4,"Ludhiana",1997,"99","99","99","99")]
    pd.DataFrame(rows).to_excel(path, header=False, index=False)

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); self.x = os.path.join(self.d, "t.xlsx"); make_icrisat(self.x)
        self.a = StructuredDataAgent(db_path=os.path.join(self.d, "t.db"), schema="haryana_schema.sql")
    def test_alias(self):
        self.assertEqual((canonical("Hissar"), canonical("Gurgaon"), canonical("Nope")), ("Hisar","Gurugram",None))
    def test_parse_column_fallback(self):
        self.assertEqual(parse_column("RICE AREA (1000 ha)"), ("Rice","AREA","1000 ha"))
    def test_icrisat_layout(self):
        recs = self.a.extract(self.x)
        self.assertEqual(len(recs), 12)                          # 3 Haryana rows x 4 columns; Punjab dropped
        r = [x for x in recs if x["district_raw"]=="Hissar" and x["table_ref"]=="col:RICE_KA"][0]
        self.assertEqual((r["value_num"], r["crop"], r["season"], r["indicator"], r["unit"], r["year"], r["row_ref"]),
                         (12.5, "Rice", "kharif", "area", "000 ha", "1997", "r11"))
        w = [x for x in recs if x["table_ref"]=="col:WHEAT_RY"][0]; self.assertEqual((w["season"], w["unit"]), ("rabi", "Nuts/ha"))
        c = [x for x in recs if x["table_ref"]=="col:COTTON_TP"][0]; self.assertIn("bales", c["unit"])
    def test_load_trace_missing_unresolved(self):
        out = self.a.load(self.a.extract(self.x), SRC)
        self.assertEqual((out["inserted"], out["unresolved_districts"]), (12, 4))   # Atlantis x4
        con = sqlite3.connect(self.a.db_path)
        self.assertEqual(con.execute("select count(*) from observation where missing_reason is not null").fetchone()[0], 1)  # '.'
        row = con.execute("select district,value_num,unit,row_ref,agent from v_trace where district='Hisar' and indicator='area'").fetchone()
        self.assertEqual(row, ("Hisar", 12.5, "000 ha", "r11", "structured_data"))
        self.assertEqual(con.execute("select season from observation where table_ref='col:RICE_TP' limit 1").fetchone()[0], "annual")
    def test_rerun_is_idempotent(self):
        self.a.load(self.a.extract(self.x), SRC); out = self.a.load(self.a.extract(self.x), SRC)
        self.assertEqual((out["inserted"], out["duplicates"]), (0, 12))
    def test_no_haryana_rows_is_an_error_not_ok(self):
        raw = pd.read_excel(self.x, header=None); raw.iloc[10:13, 1] = "Punjab"
        p = os.path.join(self.d, "np.xlsx"); raw.to_excel(p, header=False, index=False)
        with self.assertRaises(ValueError): self.a.extract(p)
    def test_file_without_extension(self):
        p = os.path.join(self.d, "download_no_ext"); shutil.copy(self.x, p); self.assertEqual(len(self.a.extract(p)), 12)
    def test_html_error_page_rejected(self):
        p = os.path.join(self.d, "page"); open(p, "w").write("<html><body>Access denied</body></html>")
        with self.assertRaises(ValueError): self.a.extract(p)
unittest.main(verbosity=2)
