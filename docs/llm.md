# The LLM in AgriAgent: what it decides, what it may not, how to run and fine-tune it

## 1. The rule that does not bend
**The LLM never supplies a number.** Every value in the database is copied from the source file by code, with its page, row and column. The LLM is asked about *meaning* only: which agent should read a file, what a table column is (crop, measure, unit), and which district a document is about. If the model is off, slow, unreachable or wrong, the pipeline still runs on its fixed rules.

`test_llm.py::test_numbers_never_come_from_the_model` checks this: the same PDF is extracted with the LLM off and with a model that tries to inject a value; the stored values and locators are identical.

## 2. Where each agent calls the LLM

| Who | Question (task name in the log) | Asked when | Answer is used only if |
|---|---|---|---|
| Supervisor (`doc_context.choose_agent`) | Which agent takes this file? (`route`) | the file type has more than one possible agent (a PDF) | the agent can open that file type. A wrong pick is still corrected, because each agent checks the content and hands the file over |
| `digital_pdf`, generic path (`table_mapper.map_columns`) | What is each column: crop, measure, unit? What does the first column hold? (`map_columns`) | the rules left a gap that a checked answer could fill (see below) | column number exists; crop is in the crop list; measure is in the list; **unit words are printed in the title or header** |
| `hindi_ocr` (`_enrich`, `_grid_fallback`) | same `map_columns` question, on the decoded Hindi header | same | same |
| `web_content` (`web_agent.py`) | same `map_columns` question, on the HTML table's merged header | same | same |
| `structured_data`, unknown sheets (`parse_generic`) | same `map_columns` question, on the sheet's header row | same | same |
| `digital_pdf` / `hindi_ocr` | Which district and year is this document about? (`doc_context`) | cover page, source name and file name did not say | district is one of the 22 Haryana districts; the year's digits are printed in the text |

**When is a table sent to the model?** (`table_mapper.llm_could_help`) Only when an accepted answer is possible: a unit word is printed but the rules could not read it; a crop table has headings the rules do not know; or the kind of row label is unknown. A table of counts with no unit printed is not sent, because no unit could be accepted. On the two Bhiwani files this gives 16 and 3 calls per document (measured with a counting stub), not one per table.

**What happens to an accepted answer.** The field is stored, the row gets `confidence = 0.8` and `needs_review = 1`. Fields decided by rules keep `confidence = 1.0`.

**Policies** (`--policy`, or `AGRI_LLM_POLICY`): `fill` (default) the model only fills what rules left empty; `always` ask for every table, still only fill gaps (use it to measure agreement); `prefer` a valid model answer overrides the rules (only sensible after fine-tuning and a comparison on real held-out files).

## 3. Switching it on

```
python run.py --input file.pdf --db demo.db --llm off        # rules only
python run.py --input file.pdf --db demo.db                   # auto: Ollama if it is running, otherwise rules only (prints which)
python run.py --input file.pdf --db demo.db --llm ollama --model qwen2.5:3b
python run.py --input file.pdf --db demo.db --llm hf --adapter finetune/adapter_qwen25_agri
python run.py --input file.pdf --db demo.db --llm openai      # any OpenAI-compatible server: set AGRI_LLM_URL, AGRI_LLM_KEY, AGRI_LLM_MODEL
```

**Simplest setup (laptop, no GPU): Ollama.** Install Ollama from https://ollama.com, then `ollama pull qwen2.5:1.5b`. Leave it running; `run.py` finds it by itself. Check with `python llm.py` (prints the backend and one test answer).

**Transformers (`--llm hf`).** `pip install torch transformers peft`. The first run downloads `Qwen/Qwen2.5-1.5B-Instruct` (about 3 GB). This is the backend that can load the fine-tuned adapter.

Environment variables: `AGRI_LLM`, `AGRI_LLM_MODEL`, `AGRI_LLM_URL`, `AGRI_LLM_KEY`, `AGRI_LLM_ADAPTER`, `AGRI_LLM_POLICY`, `AGRI_LLM_TIMEOUT` (seconds, default 120), `AGRI_LLM_MAX_CALLS` (per document, default 60), `AGRI_LLM_LOG`.

## 4. What is logged
- `logs/llm_calls.jsonl`: one line per call: task, document, backend, model, adapter, seconds, tokens in and out, valid JSON or not, retry, the raw output.
- `logs/automation_log.csv`: per run: who routed (`rules` or `llm`) and why, LLM calls, valid JSON, retries (reported separately), answers used and not used, seconds, tokens, rows flagged for review.

These give the "reported but not scored" numbers (latency, tokens, retries per document) and the raw material for tool-selection and call-count metrics.

## 5. Fine-tuning
1. `python finetune/build_dataset.py` writes `finetune/train.jsonl`, `finetune/heldout.jsonl` and `finetune/split_manifest.json`.
   - **Synthetic examples (gold):** tables generated from the project vocabularies with known answers, then damaged the way real text is damaged (split vowel signs, misplaced anusvara, typos, split words).
   - **Real examples (silver):** tables from the PDFs in `data/raw/`, labelled by the rules, kept only where the rules resolved every column. Not human-checked.
   - **Held-out:** real files are held out by file (default: any file whose name contains karnal, sirsa, rewari or jind); synthetic held-out uses another seed. The manifest records both overlap counts; both must be 0.
2. Open `finetune/finetune_qwen_lora.ipynb` in Colab with a T4 GPU, upload the project zip when asked, run all cells. It scores the base model, trains the LoRA adapter, scores the adapter on the same cases, writes `eval/adapter_comparison.csv` and gives you `adapter_and_comparison.zip`.
3. Unzip that into the project and run with `--llm hf --adapter finetune/adapter_qwen25_agri`.

## 6. What has and has not been verified
- **Verified here:** the plumbing and every check in section 2 (`test_llm.py`, 14 tests, using a stub and a local stand-in for the Ollama server); the dataset builder; the scoring code; the notebook's code path from the first cell to the last on a tiny random model (`AGRI_FT_SMOKE=1`; a code check only, its scores mean nothing); one full run of `run.py --llm ollama` against a stand-in server, with stored values identical to the LLM-off run.
- **Not verified here:** how well Qwen2.5 answers these questions, with or without the adapter. No model could be downloaded in the build environment, so **no real model has been run and the notebook has not been trained**. `eval/adapter_comparison.csv` does not exist until you run the notebook. Treat any statement about model quality as unknown until then.
- **Known limits:** the shipped held-out set is synthetic only (no other district PDFs were available when it was built); silver labels repeat the rules' own mistakes; a 1.5B model on a CPU takes seconds per call.
