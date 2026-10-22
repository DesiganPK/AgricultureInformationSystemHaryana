# AgriAgent, Team 2 (Haryana): Phase 2

A supervisor routes each source to one of four agents (fixed code, no LLM), loads rows into one SQLite database with the source page / row / column kept, and an evaluation harness scores the agents against human-checked references.

## One command
```
python run.py --input <file> [--source-name "<name from the inventory>"] [--db haryana.db]
```
It detects what the file is, routes it, lets agents hand off, loads the database and appends one row to `logs/automation_log.csv`. Nothing is edited during a run; hand edits must be declared with `--manual-steps N`.

## Orchestration
```
 input file ──► run.py ──► sniff type (xlsx/csv/pdf) ──► Supervisor.route()  (fixed rules)
                                                           │
        ┌──────────────────────┬───────────────────────┬────┴─────────────────┐
        ▼                      ▼                       ▼                      ▼
  structured_data         digital_pdf             hindi_ocr             web_content
  Excel / CSV             PDFs with a text        legacy-font Hindi      HTML pages
  (DONE)                  layer (DONE)            or scanned (DONE)      (PENDING)
        │                      │  ── re-route ──►      │
        │                      │  if ≥15% of pages     │  per page: decode Kruti Dev text layer,
        │                      │  are legacy Hindi     │  else OCR (Paddle → Tesseract → EasyOCR)
        │                      │  or no text layer     │
        └──────────────┬───────┴───────────────────────┘
                       ▼
        validate (wrong cell count → reject) → SQLite (observation + source + run + agent)
                       ▼
        logs/automation_log.csv · db/checks.py (M14-M16) · eval/run_eval.py (M3, M4 …)
```
Hand-off, retry (2) and "complete" status are in `haryana_pipeline.py` (`Supervisor`).

## Set up (clean clone)
```
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# only needed for scanned pages (OCR fallback):  Tesseract + Hindi data, e.g. Ubuntu: sudo apt install tesseract-ocr tesseract-ocr-hin
```
Source files go in `data/raw/` (see `data/manifest.csv`). The ICRISAT Excel file is **not** in this repository snapshot.

## Run the demo
```
python run.py --input data/raw/FHP_HARYANA_98-2001.pdf --source-name "Farm Harvest Prices" --db demo.db
python run.py --input data/raw/monsoon_har.pdf          --source-name "IMD Chandigarh - District-wise Haryana monsoon" --db demo.db
python run.py --input data/raw/Bhiwani.pdf              --source-name "District Statistical Abstract: Bhiwani" --db demo.db
python run.py --input <surprise file> --db demo.db      # an unseen PDF or Excel: routed by content
python eval/run_eval.py --db haryana.db                 # scores the agents and writes eval/phase2_metrics.csv
python db/checks.py haryana.db                          # M14, M15, M16
```
Show the Hindi agent's output on Bhiwani page 8 (raw line, decoded Hindi, stored row, trace-back):
```
python tools/show_hindi_output.py --html hindi_p8.html --csv hindi_p8.csv    # open the .html in a browser
```
Expected on the three files above: 1,482 + 66 + 4,820 rows, all `OK`.

## Tests
`python test_structured.py`, `python test_pdf.py`, `python test_hindi.py` (8 + 10 + 13 tests, all pass).

See `docs/metrics_guide.md` for what every metric (M1 to M18) means and how to read the `run.py`, `run_eval.py` and `checks.py` output.

## Where things are (protocol checklist)
| # | Item | Path | Status |
|---|---|---|---|
| 1 | Source-to-agent map | `docs/source_agent_map.csv` | done (41 rows; regenerate with `tools/make_source_map.py`) |
| 2 | Agent cards | `docs/agents/*.md` | done (3 implemented + 1 pending) |
| 3 | Challenge note | `docs/challenge.md` | written; **literature paragraph for the team to finish** |
| 4 | Orchestration + one command | this file, `run.py` | done |
| 5 | Evaluation set | `eval/cases/`, `eval/reference/` | **cases drawn; reference values not yet filled by a person** |
| 6 | Evaluation script | `eval/run_eval.py` | done, tested on synthetic references |
| 7 | Metrics report | `eval/phase2_metrics.csv` | generated; **column layout provisional (template not applied)** |
| 8 | Automation log | `logs/automation_log.csv` | rows from sandbox runs; add your own real runs |
| 9 | Schema, dictionary, dump | `db/schema.sql`, `db/dictionary.md`, `db/dump/` | done |
| 10 | Adapter comparison | `eval/adapter_comparison.csv` | **not done** (no adapter trained) |
| 11 | Manifest | `data/manifest.csv` | done; collection dates and licences marked TO CONFIRM |

## Known gaps (stated, not hidden)
- Only 4 of 41 sources are loaded; 37 are not: 19 Hindi district PDFs, 10 further ICRISAT workbooks plus the data.gov.in source, 5 further PDFs for `digital_pdf`, and 2 for the pending `web_content` agent.
- LGD district codes are not loaded (`district_names.LGD_CODES`); download from https://lgdirectory.gov.in.
- 3,616 Hindi rows have no unit; 3,283 ICRISAT rows have an unverified unit scale.
- PaddleOCR is integrated but could not be run in our build environment.
