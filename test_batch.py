"""Tests for batch_run.py without network: one file name per job, and no file loaded under two source names."""
import os, shutil, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import batch_run as br

LONG = "Haryana DESA - State Statistical Abstract of Haryana (one PDF per year)"


def job(name, url="https://x/a.pdf", agent="hindi_ocr", modality="PDF"):
    return {"source_name": name, "url": url, "agent": agent, "modality": modality}


class Names(unittest.TestCase):
    def test_jobs_of_one_listing_page_get_different_files(self):
        names = [f"{LONG} | State Statistical Abstract of Haryana {y}" for y in ("2022-23", "2023-24", "2024-25")]
        paths = {br.fname(job(n)) for n in names}
        self.assertEqual(len(paths), 3)                                        # was 1 before the fix: all cut at 80 characters
        self.assertTrue(all(len(os.path.basename(p)) <= 80 + 1 + 8 + 4 for p in paths))

    def test_short_names_keep_their_old_file_name(self):                       # files already in data/raw stay usable
        p = br.fname(job("Haryana DESA - District Statistical Abstract: Bhiwani"))
        self.assertEqual(os.path.basename(p), "Haryana_DESA_District_Statistical_Abstract_Bhiwani.pdf")

    def test_same_name_same_file(self):
        self.assertEqual(br.fname(job(LONG + " | 2024-25")), br.fname(job(LONG + " | 2024-25", url="https://y/new.pdf")))

    def test_extension_follows_agent_and_modality(self):
        self.assertTrue(br.fname(job("w", agent="web_content")).endswith(".html"))
        self.assertTrue(br.fname(job("x", agent="structured_data", modality="Excel (.xlsx)")).endswith(".xlsx"))
        self.assertTrue(br.fname(job("c", agent="structured_data", modality="CSV")).endswith(".csv"))


class Dedupe(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp()
    def tearDown(self): shutil.rmtree(self.d)

    def write(self, name, data):
        p = os.path.join(self.d, name); open(p, "wb").write(data); return p

    def test_same_bytes_under_two_names_runs_once(self):
        a, b, c = self.write("a.pdf", b"%PDF-1 same"), self.write("b.pdf", b"%PDF-1 same"), self.write("c.pdf", b"%PDF-1 other")
        got = [(job("A"), a, "downloaded"), (job("B"), b, "cached"), (job("C"), c, "downloaded"), (job("D"), c, "download failed: x")]
        to_run, same = br.dedupe(got)
        self.assertEqual([r["source_name"] for r, _ in to_run], ["A", "C"])
        self.assertEqual([(r["source_name"], keep) for r, _, keep in same], [("B", "A")])

    def test_cached_manual_file_without_stamp_is_used(self):
        p = self.write("m.pdf", b"%PDF-" + b"x" * 2000)                        # saved by hand in a browser, no .url stamp
        self.assertEqual(br.download((job("M", url="https://unreachable.invalid/m.pdf"), p))[2], "cached")


if __name__ == "__main__": unittest.main(verbosity=2)
