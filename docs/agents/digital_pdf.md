# Agent card: digital_pdf

**Responsibility.** Read PDFs that have a real text layer and extract district tables. If the PDF turns out to be scanned or in the legacy Hindi font, hand it to `hindi_ocr` instead of guessing.

**Sources.** 7 inventory sources route here. **Done:** DES farm harvest prices 1998-2001 (1,482 rows) and the IMD Chandigarh monsoon bulletin (66 rows). **Mapped, not yet run:** the DESA State Statistical Abstract (English, 2 entries), the District Socio-Economic Review, CGWB groundwater report, CGWB Hisar aquifer report.

## Inputs and outputs
Input: PDF path or URL plus the inventory row. Output: `observation` rows (district, crop, year, indicator, value, unit, page, line, printed column number) and one `extraction_run`. Run: `python run.py --input <pdf> --source-name "Farm Harvest Prices"`.

## Decisions: model vs fixed code
**No model; all fixed code.** Two layouts, chosen by source name (`SOURCE_CONFIG`):

| Layout | Used for | Rule |
|---|---|---|
| `lines` | borderless tables read line by line (farm harvest prices: district, year, 13 crop columns) | a row with the wrong number of values is **rejected and counted**, never guessed; the district carries down to the year rows below it; the PDF's own column numbers `(1)…(28)` become the column reference |
| `ruled` | ruled tables read with pdfplumber (IMD bulletin: DISTRICT / ACTUAL / NORMAL / DEPARTURE) | only the known districts are kept (debris and month rows are logged, not stored); the bulletin date becomes `as_of` so two bulletins from one URL are both kept |

Other fixed rules: `..` and `-` are missing; `1320r` is stored as 1320 with the raw text and flag kept; pages with no table rows (maps, charts) are skipped and logged.

**Hand-off rule.** No page has a text layer, **or** at least 15% of the pages (at least one) look like legacy-font Hindi → the agent raises a re-route to `hindi_ocr`. This is judged page by page over the whole document: on the Bhiwani abstract 58 of 98 pages are legacy, while the two English PDFs have 0.

## Measured results
- Tests `test_pdf.py`: 10/10 pass (both layouts, load and trace, two bulletins kept, re-route for legacy and for scanned PDFs, supervisor hand-off).
- Real files: farm harvest prices 1,482 rows (940 coded missing), 0 rejected; IMD bulletin 66 rows, 10 rows rejected (month rows and debris), 4 pages skipped.
- Re-run through `run.py` reproduces the same counts. An unseen copy of the Bhiwani abstract with no hints was re-routed to `hindi_ocr` automatically.
- Hard case shown live: the damaged Panipat row with too few values is rejected and counted.
- Field accuracy against the source: **not yet measured**; the 15 digital_pdf cases are drawn and the references await checking.

## Limitations
- *Implementation:* for a PDF whose name matches no config the default `ruled` layout is used, with indicator `value` and **no unit**, so an unseen table will load with missing units. The farm-price unit is read from the table title (`Rs per quintal`) only for the known layout.
- *Implementation:* the 5 mapped-but-unrun sources are large mixed PDFs (state and district tables together); no layout is written for them.
- *Implementation:* a mixed Hindi/English PDF goes entirely to `hindi_ocr`, which skips the English pages.
- *Research:* borderless tables have no ruling lines to anchor on; line-based parsing depends on the text order the PDF stores.
