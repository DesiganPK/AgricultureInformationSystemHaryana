"""Run the digital_pdf agent on a real PDF you downloaded.
Usage:  python try_pdf.py FHP_HARYANA_98-2001.pdf "Farm Harvest"
        python try_pdf.py monsoon_har.pdf "monsoon rainfall"     (second argument = text from the source name in the CSV)"""
import sys
from haryana_pipeline import load
from pdf_agent import DigitalPDFAgent
file, key = sys.argv[1], sys.argv[2]
row = [r for r in load("Source_files_Haryana_final.csv") if key.lower() in r["List of sources"].lower()][0]
row["local_file"] = file                      # use the saved file; the CSV URL is still stored as the source
print("source:", row["List of sources"])
a = DigitalPDFAgent()
print(a.run(row))
print("rejected rows:", a.rejected_rows)
print("skipped pages:", a.skipped_pages)
