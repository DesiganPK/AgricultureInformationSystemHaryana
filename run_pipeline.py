    """Run the supervisor with the four real agents."""
    import sys
    from haryana_pipeline import Supervisor, AGENTS, load
    from structured_agent import StructuredDataAgent
    from pdf_agent import DigitalPDFAgent
    from hindi_agent import HindiOCRAgent
from web_agent import WebContentAgent
    agents = dict(AGENTS); agents["structured_data"] = StructuredDataAgent(); agents["digital_pdf"] = DigitalPDFAgent(); agents["hindi_ocr"] = HindiOCRAgent(); agents["web_content"] = WebContentAgent()
    s = Supervisor(load(sys.argv[1]), agents); s.run()
    print("complete:", s.complete())
    for k, v in sorted(s.summary().items()): print(k, v)
    for j in s.jobs:
        if j.status == "failed": print("FAILED:", j.row["List of sources"][:70], "|", j.error or j.log[-1:])
