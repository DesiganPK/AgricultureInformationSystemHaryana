# Agent card: hindi_ocr

**Responsibility.** Turn Hindi district PDFs (Haryana DESA District Statistical Abstracts) into rows in `observation`, with the page, row and column of every value kept for tracing.

**Sources it handles.** 20 inventory sources are routed here (`docs/source_agent_map.csv`). Only **Bhiwani** has been run and checked so far (run 4, 4,820 rows). The other 19 are marked `pending`. Hisar was only seen as extracted text (`hisar_text.txt`), not as a PDF, so it has not been run through the agent.

**What the real files turned out to be.** The Bhiwani PDF (98 pages) has a text layer on every page. 58 pages are Hindi in the legacy **Kruti Dev** font (Latin-looking letters, Hindi glyphs) and 40 are English. So most pages need *decoding*, not OCR. OCR is the fallback for pages with no text layer.

## Inputs and outputs
- Input: a PDF path or URL, plus the inventory row (name, URL).
- Output: `observation` rows (district, tehsil, crop where known, year, indicator, value, unit, page/row/column), one `extraction_run` row, and `text_fact` rows holding each table's header text.
- Run it: `python run_pipeline.py Source_files_Haryana_final.csv` (the supervisor sends Hindi sources here, and `digital_pdf` reroutes legacy-font or scanned PDFs here).

## Decisions: model vs fixed code
**No model makes any decision.** Everything below is fixed code.

| Step | Tool | Decision rule |
|---|---|---|
| Page kind | pdfplumber | fewer than 20 characters in the text layer means scanned; otherwise text |
| Legacy-font test | `kruti_dev.looks_legacy`, `line_is_english` | a line is Hindi if it has legacy-only symbols or common legacy words, English if it looks like English |
| Decode Hindi | `kruti_dev.convert_line` | table-driven longest match, then i-matra and reph reordering |
| Table layout | `_page` | `मद इकाई जिला` header gives item / unit / district / state rows; a numbered column row gives year or tehsil rows |
| Wrong value count | `_numbered_row` | row **rejected and counted**, never guessed |
| Missing values | `parse_val` | `..` and `--` are stored as missing; a decimal written as `1634-4` becomes 1634.4 and the raw text is kept |
| Unit | `split_unit`, `title_unit` | longest known unit phrase; a hectare phrase in the table title; otherwise `unspecified` and flagged |
| Scanned page | `ocr_page` | backend chain Paddle → Tesseract (hin) → EasyOCR; rows stored with confidence 0.6 and `needs_review=1` |

## Measured results (Bhiwani.pdf, this machine)
- 4,820 rows loaded, 0 duplicates, 0 unresolved districts; 69 of 98 pages produced rows; 29 pages skipped (maps, text, English pages); 184 rows rejected for a wrong value count.
- Units on the 4,820 rows: 166 are fully specified (mostly summary rows); 1,038 get a hectare unit from their table title (`ha`, or `00 ha (as printed)` where the title prints `00`, which is not verified); 3,616 stay `unspecified`. All numbered-table rows are `needs_review=1` because the column meaning sits in the header text.
- Tests: `test_hindi.py` 13/13, `test_pdf.py` 10/10, `test_structured.py` 8/8 pass.
- **OCR path, measured.** Five Bhiwani pages were turned into image-only PDFs and compared with the text layer (`eval/ocr_backend_comparison.csv`; reproduce with `python tools/ocr_backend_eval.py Bhiwani.pdf "<source name>"`):

| Backend | Recall | Precision | Notes |
|---|---|---|---|
| Tesseract (hin+eng, grid lines removed, digit pass) | 0.58 | 0.78 | item/unit/district/state pages 0.81 to 1.00; first-page table 0.54; tehsil grid page 0.00 |
| PaddleOCR | not run | not run | installed, but its Devanagari model could not be downloaded here (HuggingFace, ModelScope, AIStudio and BOS unreachable) |
| EasyOCR | not run | not run | not installed |

**Why this matters.** The text-layer path copies digits straight from the PDF; Tesseract on the same pages loses about 4 in 10 of those values. OCR should only be used where no text layer exists. Independent check: 14 values on Bhiwani page 8 were compared by eye with the rendered page (all 14 match: numbers, units and the missing `--` cells); the signed references in `eval/` are the formal evidence and are not filled yet.

## Limitations (what is research, what is our implementation)
- **Implementation:** only Bhiwani has been run. 19 other Hindi PDFs are untested and may use other layouts. The Hisar PDF had never been run through the agent.
- **Implementation:** 3,616 rows have no unit, so they cannot pass a field-match check. Column meaning for tehsil tables is not parsed from the header; the header text is kept in `text_fact` for review.
- **Implementation:** labels can contain small decoding errors (a stray space the PDF inserts inside a word). Numbers and units are not affected. One year is printed `20012–13` in the source; it is flagged, not corrected.
- **Implementation:** the Tesseract OCR path reads nothing from the tehsil grid page and can invent a row from an English title.
- **Research:** OCR quality for Devanagari tables is limited; our numbers above are one document and five pages.
- English pages inside a Hindi PDF are skipped here and left to `digital_pdf`.
