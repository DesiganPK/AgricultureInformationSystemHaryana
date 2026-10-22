# Agent card: structured_data

**Responsibility.** Read Excel / CSV workbooks, keep only Haryana rows, turn wide tables into one row per value, and load them with their source cell as provenance.

**Sources.** 12 inventory sources are routed here (`docs/source_agent_map.csv`). **Done:** the ICRISAT-TCI area-production workbook (158,053 rows). **Mapped, not yet run:** 10 other ICRISAT workbooks (credit, groundwater, fruits, vegetables, spices, cold storage, warehouses, veterinary, runoff, GDP) and the data.gov.in mandi CSV/API (needs an account and key; the agent raises "not implemented" for it on purpose).

## Inputs and outputs
- Input: an Excel/CSV path or URL plus the inventory row. Output: `observation` rows (district, crop, season, year, indicator, value, unit, Excel row and column code), one `extraction_run`.
- Run: `python run.py --input <file> --source-name "<inventory name>"`.

## Decisions: model vs fixed code
**No model is used; every decision is fixed code.**

| Step | Tool | Decision rule |
|---|---|---|
| Fetch | `urllib`, `sniff()` | the file type is read from its first bytes, never its name; an HTML error page is rejected, not parsed |
| Layout | `parse_icrisat` | the header row containing `STNAME` is found; the 3 rows above it give crop, season, measure; if not found, fall back to `parse_simple` (single-row headers like `RICE AREA (1000 ha)`) |
| Filter | `extract` | only rows where state = Haryana; **no Haryana rows is an error, not an empty "ok"** |
| Values | `_num` | `.`, `-`, `..`, `NA` become missing, with the raw text kept |
| Units | `DEFAULT_UNITS`, `PROD_UNIT_OVERRIDE` | from the sheet's own notes; cotton, jute, mesta (bales) are marked "scale unverified" |
| Districts | `canonical()` | spelling aliases resolved in code; an unknown name is stored with `needs_review = 1` |
| Duplicates | unique index | re-running a file adds 0 rows |

## Measured results
- Tests `test_structured.py`: 8/8 pass (layout, load, trace, missing values, unresolved district, idempotent re-run, no-Haryana-rows error, HTML page rejected).
- Database: 158,053 rows, 102,628 of them coded missing (the workbook marks blanks with `.`), 0 duplicates, 0 unresolved districts.
- Hard case shown live: a workbook where every Haryana row is renamed Punjab → the agent fails loudly with "no Haryana records parsed" instead of reporting success.
- Field accuracy against the source: **not yet measured**. The 15 structured_data cases in `eval/` are drawn but the reference values are unfilled, and the Excel file is not in this snapshot.

## Limitations
- *Implementation:* only the area-production layout has been checked on a real file. The fallback parser has only been tested on synthetic files. Only the first worksheet is read. 3,283 rows (cotton, jute, mesta production) carry a unit whose scale is unverified. The Excel file is not committed (see `data/manifest.csv`).
- *Implementation:* the other 10 ICRISAT files and the data.gov.in source are not run, so source coverage is low (M2).
- *Research / source:* ICRISAT notes say production units for some crops differ; whether the scale is thousand bales or bales needs the original DACNET documentation.
