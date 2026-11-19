# AgriAgent, Team 2 (Haryana)

Give it a file (PDF, Excel, CSV or a saved web page) and it loads the tables into one SQLite database, keeping the page, row and column of every value. A supervisor picks one of four agents. Fixed rules do the work; a small LLM (Qwen2.5 by default) can be switched on to decide the things rules cannot, and its answers are checked before they are used.

**The LLM never supplies a number.** Values are copied from the source by code. The LLM only labels: which agent, what a column means (crop, measure, unit), which district a document is about. With the LLM off, everything still runs.

## Quick start
```
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python run.py --input data/raw/Bhiwani.pdf --db demo.db --view
```
`--view` writes `demo_view.html` and opens it in your browser: totals, one line per source, and the stored rows with filter, search and the source location of each value. To look again later: `python view_db.py demo.db`.

Any other file works the same way:
```
python run.py --input some_unseen.pdf --db demo.db --view
python run.py --input data/samples/sample_page.html --db demo.db --view
python run.py --input https://example.org/page-or-file --db demo.db --view      # downloads once into data/raw
```
What you get depends on the file:
- **Tables with numbers** become rows in `observation`.
- **Pages without tables** (a letter, a report chapter) are kept as text in `text_fact`, so the document is in the database but has no numeric rows. The run prints how many.
- **Scanned pages** go through OCR (Tesseract with Hindi; install `tesseract-ocr` and `tesseract-ocr-hin`). OCR rows are stored with `needs_review = 1`.

## Switching the LLM on
```
python run.py --input file.pdf --db demo.db --llm off                 # rules only
python run.py --input file.pdf --db demo.db                           # auto: uses Ollama if it is running, otherwise rules only (it prints which)
python run.py --input file.pdf --db demo.db --llm ollama              # after: ollama pull qwen2.5:1.5b
python run.py --input file.pdf --db demo.db --llm hf --adapter finetune/adapter_qwen25_agri     # transformers + your fine-tuned adapter
```
**On a CPU this is slow.** Each model call takes roughly 10 to 60 seconds, and one file makes up to 60 calls (one per table header the rules cannot read fully), so Bhiwani can take 15 minutes or more. The run prints one line per call. For a demo, cap it: `--llm-max-calls 10` (the rest of the file runs on rules), or use the 8-page `PDF_bhiwani_crop_area_production.pdf`.

**What did the model change?** Build the same file twice, once with `--llm off`, then run `python tools/compare_dbs.py rules.db llm.db`. It lists every row whose crop, unit or indicator differs, and writes them to `llm_changes.csv` for checking against the PDF.

Easiest on a laptop: install Ollama (https://ollama.com), run `ollama pull qwen2.5:1.5b`, leave it running. `python llm.py` prints the backend in use and one test answer. Details, checks and logging: `docs/llm.md`.

## How it works
```
 file or URL ─► run.py ─► profile (type, text layer, legacy-font Hindi pages)
                              │
                     Supervisor: rules, or the LLM if on (pick is checked; agents can still hand over)
        ┌──────────────────┬──────────────┴───────┬─────────────────────┐
        ▼                  ▼                      ▼                     ▼
  structured_data     digital_pdf             hindi_ocr             web_content
  Excel / CSV         English PDFs            legacy-font Hindi     HTML pages: tables,
                      (known layouts +        PDFs (decode) and     text and data links
                      any ruled table)        scanned pages (OCR)
        └──────────────────┴───────────┬──────────┴─────────────────────┘
                                       ▼
        table_mapper: what is each column?  rules first, LLM for the gaps, answer validated
                                       ▼
        SQLite: observation + source + extraction_run + agent + text_fact   (view: v_trace)
                                       ▼
        logs/automation_log.csv · logs/llm_calls.jsonl · db/checks.py · eval/run_eval.py · view_db.py
```

| Agent | File | LLM is asked | Status |
|---|---|---|---|
| Supervisor | `doc_context.py`, `haryana_pipeline.py` | which agent takes a PDF | done |
| `structured_data` | `structured_agent.py` | column meaning on sheets with an unknown layout | done |
| `digital_pdf` | `pdf_agent.py` | column meaning on tables outside the two known layouts; district and year of the document | done |
| `hindi_ocr` | `hindi_agent.py`, `kruti_dev.py` | column meaning from the decoded Hindi header; district and year | done |
| `web_content` | `web_agent.py` | column meaning on HTML tables | done; **tested on a sample page only, not on the two inventory web sources** |

## Fine-tuning the model
1. `python finetune/build_dataset.py` writes `finetune/train.jsonl` and `finetune/heldout.jsonl` (already built: 1,200+ training examples).
2. Open `finetune/finetune_qwen_lora.ipynb` in Google Colab with a T4 GPU, upload this project's zip when it asks, run all cells. It scores the base model, trains a LoRA adapter, scores the adapter on the same held-out cases, and writes `eval/adapter_comparison.csv`.
3. Unzip the `adapter_and_comparison.zip` it gives you into this folder and run with `--llm hf --adapter finetune/adapter_qwen25_agri`.

## Other commands
```
python batch_run.py --db haryana.db                 # download and run every pending inventory source
python tools/show_hindi_output.py --html hindi_p8.html     # one Hindi page: raw text, decoded Hindi, stored rows, trace-back
python eval/run_eval.py --db haryana.db             # metrics; writes eval/phase2_metrics.csv
python db/checks.py haryana.db                      # completeness, validation, duplicates (M14 to M16)
python tools/make_dump.py haryana.db                # refresh db/dump/haryana_dump.sql.gz
python test_structured.py; python test_pdf.py; python test_hindi.py; python test_llm.py; python test_web.py     # 8 + 10 + 18 + 14 + 5 tests
```

## Re-extract the Hindi sources in `haryana.db` (do this once)
`haryana.db` in this folder is the database you sent: 22 sources, 218,990 rows. Its Hindi rows were made by the earlier agent, which left most units and crops empty. The current agent reads them from the table headers. On Bhiwani alone, completeness (M14) goes from 0.004 to 0.689 and validation (M15) from 0.168 to 0.713. To get that for all districts:
```
cp haryana.db haryana_backup.db
python tools/clear_rows.py --db haryana.db --agent hindi_ocr --yes
python batch_run.py --db haryana.db --redo --only "Statistical Abstract"
python eval/build_cases.py          # the 45 evaluation cases were drawn from the old rows; draw them again before filling references
python tools/make_dump.py haryana.db
```
Only Bhiwani could be re-run here, because the other district PDFs are not in this folder.

## What has been checked, and what has not
**Checked here**
- All 55 tests pass.
- Five files run end to end with the LLM off: `Bhiwani.pdf` 5,917 rows, `PDF_bhiwani_crop_area_production.pdf` 864, the farm harvest prices PDF 1,482, the monsoon PDF 66, and the sample web page 9 (8,338 rows in 14.7 s on a cloud CPU).
- Pages 1 and 8 of the crop PDF were checked by eye against the database on 19 Nov; four faults found (`@` cells rejecting rows, a merged rice/maize header, HYV area mixed with total area, a misprinted year) are fixed and have regression tests in `test_hindi.py`.
- The LLM path runs end to end against a stand-in model server, and the stored values are identical with the LLM on and off. On the team's Windows laptop (RTX 5060) the fine-tuned adapter answered all 27 calls for `Bhiwani.pdf` with valid JSON in 252 s.
- The notebook's code runs from the first cell to the last on a tiny random model (a code check only; its scores mean nothing).
- Values on Bhiwani page 8 and on page 2 of the crop PDF match the source text.

**Not checked**
- **Adapter result is on synthetic cases only.** `eval/adapter_comparison.csv` (Colab T4, 17 Nov): F1 0.148 base vs 0.995 adapter on 120 held-out cases from the same synthetic generator as training; no real file was held out. Put other districts' PDFs in `data/raw/` and re-run `build_dataset.py` to hold real files out.
- The 45 evaluation references are still blank, so field-level F1 (M3) and provenance accuracy (M4) are not computed.
- `web_content` has not been run on the two inventory web pages. It does not run JavaScript.
- About 1,660 Bhiwani rows still have no unit, because none is printed near their tables.
- LGD district codes are not loaded (`district_names.LGD_CODES`).

## Where things are
| Item | Path |
|---|---|
| Source-to-agent map (22 of 41 sources done) | `docs/source_agent_map.csv` |
| Agent cards | `docs/agents/*.md` |
| LLM design, checks, fine-tuning | `docs/llm.md` |
| Metric definitions and how to read the output | `docs/metrics_guide.md` |
| Challenge note (literature paragraph still for the team) | `docs/challenge.md` |
| Evaluation cases, references, script, metrics file | `eval/` |
| Run log and LLM call log | `logs/automation_log.csv`, `logs/llm_calls.jsonl` |
| Schema, dictionary, dump | `db/schema.sql`, `db/dictionary.md`, `db/dump/` |
| Data manifest (collection dates and licences marked TO CONFIRM) | `data/manifest.csv` |
