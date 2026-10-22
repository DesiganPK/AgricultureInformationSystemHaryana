# Data dictionary: Haryana agri knowledge base

SQLite, defined in `db/schema.sql` (same file as `haryana_schema.sql`; portable to PostgreSQL). Dump: `db/dump/haryana_dump.sql.gz` (`gunzip -c … | sqlite3 new.db`). Checks: `python db/checks.py`.

## Tables and keys
| Table | Rows now | What it holds | Key |
|---|---|---|---|
| `district` | 22 | canonical Haryana districts (`name_en`, optional `name_hi`, `lgd_code`) | `district_id` (surrogate); `lgd_code` UNIQUE, **currently NULL for all 22** |
| `district_alias` | 17 | spelling variants (Hissar → Hisar, Gurgaon → Gurugram, Mewat → Nuh …) | `alias` → `district_id` |
| `crop` | 72 | crop names as the source prints them (not merged across sources) | `crop_id` |
| `unit` | 21 | `raw_label` as printed plus a `canonical` form and a `factor` | `unit_id` |
| `source` | 4 | one row per source document: name, URL, type, language, period | `source_id`; URL UNIQUE |
| `agent` | 3 | `structured_data`, `digital_pdf`, `hindi_ocr` | `agent_id` |
| `extraction_run` | 4 | one run of one agent on one source: tool, version, status, retries, manual steps | `run_id` |
| `observation` | 164,421 | **the facts**: one value per row with its place in the source | `obs_id`; natural key below |
| `text_fact` | 86 | narrative text, here the header text of the Hindi tables (topic `table_header`) | `fact_id` |
| `eval_result` | 0 | evaluator scores linked to a run (not used yet; results are in `eval/`) | `eval_id` |

Relationships: `observation` → `extraction_run` → `agent` and `source`; `observation` → `district`, `crop`, `unit`. View `v_trace` joins a value back to its source URL, page, table, row and agent (used for the live trace-back).

## `observation` columns
- **Where in the source:** `page_no`, `table_ref`, `row_ref`, `record_id`. For PDFs `table_ref` is `p<page>c<printed column number>`; for Excel `row_ref` = Excel row (`r2377`) and `table_ref` = `col:<column code>`.
- **What it is about:** `district_id` (resolved) and `district_raw` (exactly as printed), `tehsil_block`, `crop_id`, `season` (`kharif`/`rabi`/`zaid`/`annual`), `year_label` (as printed, e.g. `2011-12`), `indicator`.
- **The value:** `value_num`, `value_text` (raw cell text, always kept), `unit_id`, `missing_reason`, `confidence` (1.0 for text and Excel; 0.6 for OCR), `needs_review`, `as_of` (bulletin date for overwritten sources such as the IMD bulletin).

## Identifiers
Surrogate integer keys everywhere. The place identifier will be the **LGD district code** once loaded; until then `district_id` is the stable key and the canonical name comes from `district_names.py`. **LGD codes must be downloaded from https://lgdirectory.gov.in and filled into `LGD_CODES`; this has not been done.**

## Missing-value handling
A value is never silently empty: `CHECK (value_num IS NOT NULL OR missing_reason IS NOT NULL)`. Codes: `dash_in_source` (the source printed `..`, `-`, `.`), `not_reported` (empty cell), `ocr_unreadable`, `not_applicable`. The raw text stays in `value_text`. Rows from a table whose unit could not be determined get unit `unspecified` and `needs_review = 1`.

## Duplicate control
Unique index `ux_obs_dedup` over (source, indicator, district, district_raw, crop, season, year, as_of, tehsil, table_ref, row_ref). Re-running a source adds 0 rows. M16 groups on the natural key (source, locator, crop, location, period, variable) and finds 0 duplicates.

## Validation rules (applied by `db/checks.py` for M15)
A row passes only if **all** hold:
1. unit is one of: `000 ha, ha, 000 tonnes, kg/ha, Rs/quintal, mm, %, number, 000, lakh, km, sq km, per sq km, 000 bales, quintal, 000 litres, 000 nuts, Nuts/ha, cm`;
2. `year_label` parses as `YYYY` or `YYYY-YY` / `YYYY-YYYY`;
3. the value is in a plausible range for its unit (percent and rainfall departure −100 to 1000; mm 0 to 6000; Rs/quintal and kg/ha 0 to 100000; other values ≥ 0);
4. a named place resolves to a canonical district (no place = state level);
5. *not applied yet:* "valid LGD code", because the codes are not loaded.

M14 (mandatory fields) is strict: source, source type, agent, run, locator, crop, location, period and unit must be filled, or, for the unit only, the value coded in `missing_reason`. A NULL crop (rainfall and general statistics rows), a NULL place (state-level rows) and a unit of `unspecified` all count as **missing**.

## Measured state (this dump)
- M14 = 0.970, M15 = 0.956, M16 = 0.000 (`python db/checks.py`).
- Rows failing M15 (7,293): unit `unspecified` (3,616, Hindi numbered tables where the column meaning is only in the header text), `bales (per sheet note; scale unverified)` (3,283, ICRISAT cotton/jute/mesta), `00 ha (as printed)` (391), `lakh kilowatt (as printed)` (2) and a few others.
- `needs_review = 1` on 4,652 rows (all Hindi numbered-table cells and OCR rows).
- Source groups loaded: Excel (ICRISAT area-production, 158,053 rows), digital PDF (farm harvest prices 1,482; IMD monsoon 66), Hindi PDF (Bhiwani abstract 4,820). Only one district's Hindi abstract has been loaded.
- Counts of rows coded missing: 97,738 `dash_in_source`, 6,147 `not_reported`.
