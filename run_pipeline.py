"""Run the supervisor with the real structured_data agent; other agents are still stubs."""
import sys
from haryana_pipeline import Supervisor, AGENTS, load
from structured_agent import StructuredDataAgent
from pdf_agent import DigitalPDFAgent
from hindi_agent import HindiOCRAgent
agents = dict(AGENTS); agents["structured_data"] = StructuredDataAgent(); agents["digital_pdf"] = DigitalPDFAgent(); agents["hindi_ocr"] = HindiOCRAgent()
s = Supervisor(load(sys.argv[1]), agents); s.run()
print("complete:", s.complete())
for k, v in sorted(s.summary().items()): print(k, v)
for j in s.jobs:
    if j.status == "failed": print("FAILED:", j.row["List of sources"][:70], "|", j.error or j.log[-1:])
