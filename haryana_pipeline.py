"""Haryana agri pipeline: supervisor/router skeleton (Phase 2).
Routing is fixed rules (code), not an LLM call. Agents are stubs until implemented."""
import csv, re, sys, time
from dataclasses import dataclass, field

# ---- routing rules (deterministic) ------------------------------------
def route(row):
    m, url, name = row["Modalities"], row["URLs"], row["List of sources"]
    if "index of Excel" in m:            return "web_content"      # discovery step, feeds structured_data
    if m.startswith("HTML"):             return "web_content"
    if "XLSX" in m or m.startswith("CSV"): return "structured_data"
    if "Hindi" in m:                     return "hindi_ocr"
    if m == "PDF" and "bulletin" in name.lower() and "CSR 30" in name:
        return "hindi_ocr"               # older scanned-style bulletin -> OCR path
    if m == "PDF":                       return "digital_pdf"
    raise ValueError(f"unroutable modality: {m!r} ({name})")

def depends_on(row):
    """ICRISAT XLSX files need the index page crawled first; DESA listing pages need discovery."""
    if "ICRISAT" in row["List of sources"] and "XLSX" in row["Modalities"]:
        return "ICRISAT-TCI District Level Database - Additional Data: download index page"
    return None

# ---- agents (stubs) ---------------------------------------------------
class AgentNotImplemented(Exception): pass
class Reroute(Exception):
    """An agent decides the source belongs to another agent (e.g. scanned PDF -> OCR agent)."""
    def __init__(self, agent, reason): super().__init__(reason); self.agent, self.reason = agent, reason

class Agent:
    name = "base"
    def run(self, src):                # real agents: fetch -> extract -> validate -> insert
        raise AgentNotImplemented(self.name)
class StructuredData(Agent): name = "structured_data"
class DigitalPDF(Agent):     name = "digital_pdf"
class HindiOCR(Agent):       name = "hindi_ocr"
class WebContent(Agent):     name = "web_content"
AGENTS = {a.name: a() for a in (StructuredData, DigitalPDF, HindiOCR, WebContent)}

# ---- supervisor -------------------------------------------------------
@dataclass
class Job:
    row: dict; agent: str; status: str = "queued"; attempts: int = 0
    error: str = ""; log: list = field(default_factory=list)

class Supervisor:
    TERMINAL = {"ok", "failed", "pending_implementation"}
    def __init__(self, rows, agents=AGENTS, max_retries=2):
        self.jobs = [Job(r, route(r)) for r in rows]
        self.agents, self.max_retries = agents, max_retries
    def _dep_done(self, job):
        d = depends_on(job.row)
        return d is None or any(j.row["List of sources"] == d and j.status == "ok" for j in self.jobs)
    def run(self):
        while not self.complete():
            progressed = False
            for j in self.jobs:
                if j.status in self.TERMINAL: continue
                if not self._dep_done(j):
                    dep = [x for x in self.jobs if x.row["List of sources"] == depends_on(j.row)][0]
                    if dep.status in self.TERMINAL:      # dependency failed -> cannot proceed
                        j.status, j.error = "failed", "dependency failed"; progressed = True
                    continue
                j.attempts += 1; progressed = True
                try:
                    self.agents[j.agent].run(j.row); j.status = "ok"
                except Reroute as e:
                    j.log.append(f"rerouted {j.agent} -> {e.agent}: {e.reason}"); j.agent = e.agent; j.attempts = 0; j.status = "queued"
                except AgentNotImplemented:
                    j.status = "pending_implementation"
                except Exception as e:
                    j.error = str(e); j.log.append(f"attempt {j.attempts}: {e}")
                    j.status = "queued" if j.attempts <= self.max_retries else "failed"
            if not progressed: break                     # deadlock guard
    def complete(self):              # done = every source is in a terminal state
        return all(j.status in self.TERMINAL for j in self.jobs)
    def summary(self):
        out = {}
        for j in self.jobs: out[(j.agent, j.status)] = out.get((j.agent, j.status), 0) + 1
        return out

def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f: return list(csv.DictReader(f))

if __name__ == "__main__":
    rows = load(sys.argv[1]); s = Supervisor(rows); s.run()
    print("complete:", s.complete()); [print(k, v) for k, v in sorted(s.summary().items())]
