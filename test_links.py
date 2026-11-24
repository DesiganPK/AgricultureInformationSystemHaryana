"""Tests for live link refresh (sources_live.py, refresh_links_lib.py) and for keeping a source's identity when its URL changes.
No network: the listing pages are small copies of the real page's structure (a table, title cell + 'View' link)."""
import csv, os, shutil, sqlite3, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
os.environ["AGRI_LLM"] = "off"; os.environ["AGRI_LLM_QUIET"] = "1"
import sources_live as sl, refresh_links_lib as rl

S3 = "https://cdnbbsr.s3waas.gov.in/s32b0f658cbffd284984fb11d90254081f/uploads/"
def row(d, f): return (f'<tr><td>District Statistical Abstracts ({d})</td><td>Accessible Version : <a href="{S3}{f}" '
                       f'title="District Statistical Abstracts ({d})">View</a> (577 KB)</td></tr>')
DISTRICT_HTML = ("<html><body><table><tr><th>Title</th><th>View / Download</th></tr>" + row("Bhiwani", "2026/05/new-bhiwani.pdf")
                 + row("Mewat", "2021/02/2021021859-2.pdf") + row("Faridabad", "2021/02/2021021834.pdf") + "</table></body></html>")
STATE_HTML = ('<ul><li>State Statistical Abstract of Haryana 2023-24 <a href="/uploads/sa2324.pdf">Download</a></li>'
              '<li>State Statistical Abstract of Haryana 2024-25 <a href="https://cdn.example/sa-2025-new.pdf">Download</a></li></ul>'
              '<a href="https://esaharyana.gov.in/document-category/state-statistical-abstract-of-haryana-english/page/2/">2</a>')
STATE_PAGE2 = '<ul><li>State Statistical Abstract of Haryana 2006-07 <a href="/uploads/sa0607.pdf">Download</a></li></ul>'
PAGES = {sl.DISTRICT_PAGE: DISTRICT_HTML, sl.STATE_ABSTRACT_PAGE: STATE_HTML, sl.STATE_ABSTRACT_PAGE + "/page/2/": STATE_PAGE2}


class Links(unittest.TestCase):
    def setUp(self):
        sl._CACHE.clear(); self._get = sl.get
        sl.get = lambda url, timeout=60: PAGES[url] if url in PAGES else (_ for _ in ()).throw(OSError("offline"))
    def tearDown(self): sl.get = self._get

    def inv(self):
        return [{"List of sources": "Haryana DESA - District Statistical Abstract: Bhiwani", "URLs": S3 + "2021/02/2021021818-2.pdf"},
                {"List of sources": "Haryana DESA - District Statistical Abstract: Mewat (Nuh)", "URLs": S3 + "2021/02/2021021859-2.pdf"},
                {"List of sources": "Haryana DESA - District Statistical Abstract: Hisar", "URLs": S3 + "2021/02/2021021818-3.pdf"},
                {"List of sources": "Haryana DESA - State Statistical Abstract of Haryana (one PDF per year) - 2024-25 PDF direct link",
                 "URLs": S3 + "2026/03/sa.pdf"}]

    def test_listing_table_is_read(self):
        items = sl.parse_listing(DISTRICT_HTML, sl.DISTRICT_PAGE)
        self.assertEqual(len(items), 3); self.assertTrue(items[0][0].startswith("District Statistical Abstracts (Bhiwani)"))

    def test_changed_link_is_taken_from_the_page_and_names_are_matched(self):
        rows = self.inv(); changes, missing, extra = sl.refresh(rows)
        self.assertEqual(rows[0]["URLs"], S3 + "2026/05/new-bhiwani.pdf")             # moved file: new link from the page
        self.assertEqual(rows[1]["URLs"], S3 + "2021/02/2021021859-2.pdf")            # Mewat on the page = Mewat (Nuh) in the CSV
        self.assertEqual(missing, ["Haryana DESA - District Statistical Abstract: Hisar"])   # not on the page: CSV URL kept
        self.assertEqual(rows[2]["URLs"], S3 + "2021/02/2021021818-3.pdf")
        self.assertEqual([d for d, _ in extra], ["Faridabad"])
        self.assertEqual(rows[3]["URLs"], "https://cdn.example/sa-2025-new.pdf")       # the 2024-25 file found by its year

    def test_unreadable_page_changes_nothing(self):
        sl.get = lambda url, timeout=60: (_ for _ in ()).throw(OSError("offline"))
        rows = self.inv(); before = [r["URLs"] for r in rows]
        sl.refresh(rows); self.assertEqual([r["URLs"] for r in rows], before)

    def test_listing_page_expands_to_one_job_per_pdf_across_pages(self):
        jobs = sl.expand("Haryana DESA - State Statistical Abstract of Haryana (one PDF per year)", sl.STATE_ABSTRACT_PAGE)
        self.assertEqual(len(jobs), 3)
        self.assertTrue(jobs[0][0].endswith("| State Statistical Abstract of Haryana 2023-24"))
        self.assertEqual(jobs[0][1], "https://esaharyana.gov.in/uploads/sa2324.pdf")
        self.assertEqual(sl.expand("x", S3 + "a.pdf"), [("x", S3 + "a.pdf")])          # a file URL stays one job

    def test_csv_is_rewritten_with_backup_and_new_district(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, "inv.csv"); shutil.copy(os.path.join(HERE, "Source_files_Haryana_final.csv"), p)
        name = "Haryana DESA - District Statistical Abstract: Bhiwani"
        rows0 = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        keep = [r for r in rows0 if "Abstract: Faridabad" not in r["List of sources"]]
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows0[0])); w.writeheader(); w.writerows(keep)
        n0 = len(keep)
        rl.write_urls(p, {name: S3 + "2026/05/new-bhiwani.pdf"})
        rows = list(csv.DictReader(open(p, encoding="utf-8-sig")))
        self.assertEqual(next(r for r in rows if r["List of sources"] == name)["URLs"], S3 + "2026/05/new-bhiwani.pdf")
        self.assertTrue(os.path.exists(p + ".bak")); self.assertEqual(len(rows), n0)
        self.assertEqual(rl.add_districts(p, [("Faridabad", S3 + "2021/02/2021021834.pdf")]), ["Haryana DESA - District Statistical Abstract: Faridabad"])
        self.assertEqual(len(list(csv.DictReader(open(p, encoding="utf-8-sig")))), n0 + 1)

    def test_period_text_removed_from_inventory(self):
        txt = open(os.path.join(HERE, "Source_files_Haryana_final.csv"), encoding="utf-8-sig").read()
        self.assertNotIn("Edition not verified (listing page last updated Feb 2021)", txt)


class SourceIdentity(unittest.TestCase):
    def test_new_url_for_same_source_adds_no_rows(self):
        import run
        d = tempfile.mkdtemp(); db = os.path.join(d, "t.db"); log = os.path.join(d, "log.csv")
        pdf = os.path.join(HERE, "data", "raw", "PDF_bhiwani_crop_area_production.pdf")
        name = "Haryana DESA - District Statistical Abstract: Bhiwani"
        for u in (S3 + "2021/02/2021021818-2.pdf", S3 + "2026/05/new-bhiwani.pdf"):
            self.assertEqual(run.main(["--input", pdf, "--db", db, "--log", log, "--llm", "off", "--source-name", name, "--url", u]), 0)
        con = sqlite3.connect(db)
        self.assertEqual(con.execute("select count(*), max(url) from source").fetchone(), (1, S3 + "2026/05/new-bhiwani.pdf"))
        self.assertIn("previous: " + S3 + "2021/02/2021021818-2.pdf", con.execute("select link_status from source").fetchone()[0])
        self.assertEqual(con.execute("select count(*) from observation").fetchone()[0], 864)


if __name__ == "__main__": unittest.main(verbosity=2)
