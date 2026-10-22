# Agent card: web_content  (STATUS: PENDING, not implemented)

**Responsibility (planned).** Read static HTML pages and the ICRISAT download index, and produce `text_fact` rows (variety and advisory text) and the list of Excel files for `structured_data` to fetch.

**Sources mapped here (2):** ICAR-CSSRI salt-tolerant rice, wheat and mustard varieties (static text page); ICRISAT-TCI "download index page" (it feeds the 10 other ICRISAT workbooks, which `structured_data` depends on).

**Status.** The stub in `haryana_pipeline.py` raises "not implemented", and the supervisor records these two jobs as `pending_implementation`. Nothing was run, no tool was tested, and the database has no rows from them. In `docs/source_agent_map.csv` they are `pending`.

**Why declared pending.** Protocol rule: pending work loses only coverage points; work claimed but failing live loses demonstration points too.

**Plan.** Fetch the page, parse text with a fixed HTML parser (no model), split by headings into `text_fact` rows with the URL fragment as `location_ref`; on the ICRISAT index, collect the `bulkDownload` links and queue them for `structured_data`.
