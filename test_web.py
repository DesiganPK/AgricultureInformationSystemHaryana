"""Tests for the web_content agent (web_agent.py). No network: a saved page is used."""
import os, sqlite3, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
os.environ["AGRI_LLM"] = "off"
from web_agent import WebContentAgent, parse_html, merge_header, _expand
PAGE = os.path.join(HERE, "data", "samples", "sample_page.html")
SRC = {"List of sources": "sample page", "URLs": "https://example.org/stats/", "Period Of Interest": None, "local_file": PAGE}


class T(unittest.TestCase):
    def setUp(self):
        self.db = os.path.join(tempfile.mkdtemp(), "t.db"); self.ag = WebContentAgent(db_path=self.db)

    def test_spans_are_expanded_and_headers_merged(self):
        grid = _expand([[("District", 1, 2), ("Rice", 2, 1)], [("Area", 1, 1), ("Production", 1, 1)], [("Karnal", 1, 1), ("172.5", 1, 1), ("612", 1, 1)]])
        self.assertEqual(grid[1], ["District", "Area", "Production"])
        self.assertEqual(merge_header(grid)[0], ["District", "Rice Area", "Rice Production"])

    def test_table_rows_with_crop_measure_unit_and_locator(self):
        recs = self.ag.extract(PAGE, SRC["URLs"])
        self.assertEqual(len(recs), 9)
        r = next(x for x in recs if x["district_raw"] == "Karnal" and x["crop"] == "Wheat")
        self.assertEqual((r["indicator"], r["value_num"], r["unit"], r["year"]), ("area", 171.0, "000 ha", "2019-20"))
        self.assertTrue(r["table_ref"].startswith("table1|col5")); self.assertTrue(r["row_ref"].startswith("table1:r"))
        dash = next(x for x in recs if x["district_raw"] == "Kaithal" and x["crop"] == "Wheat")
        self.assertIsNone(dash["value_num"]); self.assertEqual(dash["value_text"], "--")          # a dash is stored as missing, never as zero

    def test_text_and_data_links_are_kept_and_scripts_ignored(self):
        self.ag.run(dict(SRC)); con = sqlite3.connect(self.db)
        topics = [r[0] for r in con.execute("select topic from text_fact order by fact_id")] if self._has(con, "fact_id") else [r[0] for r in con.execute("select topic from text_fact")]
        self.assertIn("Haryana kharif crops", topics); self.assertIn("data_link", topics)
        link = con.execute("select content from text_fact where topic='data_link'").fetchone()[0]
        self.assertEqual(link, "https://example.org/stats/files/rice_2019.xlsx")                   # relative link resolved against the page URL
        self.assertEqual(con.execute("select count(*) from text_fact where content like '%var x%'").fetchone()[0], 0)
        self.assertEqual(con.execute("select count(*) from observation").fetchone()[0], 9)
        self.assertEqual(con.execute("select count(*) from v_trace where agent='web_content'").fetchone()[0], 9)

    def _has(self, con, col): return col in [r[1] for r in con.execute("pragma table_info(text_fact)")]

    def test_page_with_nothing_readable_fails_loudly(self):
        p = os.path.join(tempfile.mkdtemp(), "js.html"); open(p, "w").write("<html><body><div id='app'></div><script>render()</script></body></html>")
        with self.assertRaises(ValueError): self.ag.extract(p)

    def test_run_py_routes_html_to_this_agent(self):
        import run
        log = os.path.join(tempfile.mkdtemp(), "log.csv")
        self.assertEqual(run.main(["--input", PAGE, "--db", self.db, "--log", log, "--llm", "off"]), 0)
        row = open(log, encoding="utf-8-sig").read()
        self.assertIn("web_content", row); self.assertIn(",ok,", row)


if __name__ == "__main__": unittest.main(verbosity=2)
