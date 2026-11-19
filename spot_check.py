"""Draw a random sample of stored PDF values so you can tick them off against the PDF by eye.
Usage: python spot_check.py <run_id> [n=20]
Writes spot_check_run<id>.csv with an empty 'matches_pdf (Y/N)' column to fill in (evidence for the evaluation)."""
import sqlite3, sys, csv, random
run = int(sys.argv[1]); n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
con = sqlite3.connect("haryana.db")
rows = con.execute("""select o.obs_id, coalesce(d.name_en, o.district_raw, 'STATE') district, o.district_raw, o.year_label, c.name_en crop,
                      o.indicator, o.value_text, o.page_no, o.row_ref, o.table_ref, o.value_num
                      from observation o left join district d using(district_id) left join crop c using(crop_id)
                      where o.run_id=?""", (run,)).fetchall()
rng = random.Random(7); rng.shuffle(rows)
nums = [r for r in rows if r[10] is not None]; blanks = [r for r in rows if r[10] is None]
k = max(1, n // 5) if blanks else 0                                  # ~80% real numbers, ~20% blanks ('..')
sample = nums[:n - k] + blanks[:k]; rng.shuffle(sample)
name = f"spot_check_run{run}.csv"
with open(name, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f); w.writerow(["obs_id","district (as printed)","year","crop","indicator","value_in_database","pdf_page","row_ref","column_ref","matches_pdf (Y/N)"])
    for r in sample: w.writerow([r[0], r[2], r[3], r[4] or "", r[5], r[6], r[7], r[8], r[9], ""])
print(f"{len(sample)} of {len(rows)} stored values written to {name}\n")
print(f"{'#':>2} {'page':>4}  {'district':<14} {'year':<8} {'crop/indicator':<22} value")
for i, r in enumerate(sample, 1):
    print(f"{i:>2} {r[7]:>4}  {(r[2] or r[1]):<14} {(r[3] or ''):<8} {(r[4] or r[5]):<22} {r[6]}")
