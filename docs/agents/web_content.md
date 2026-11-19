# Agent card: web_content

**Responsibility.** Turn an HTML page (a saved `.html` file or a URL) into database rows: table values into `observation`, page text into `text_fact`, and links to data files into `text_fact` with topic `data_link`.

**Sources mapped here (2):** ICAR-CSSRI salt-tolerant varieties (a text page) and the ICRISAT download index page. **Neither has been run yet.** The agent was tested on `data/samples/sample_page.html` only (`test_web.py`, 5 tests), so in `docs/source_agent_map.csv` both sources stay `pending` until you run them: `python batch_run.py --db haryana.db --only CSSRI`.

## Inputs and outputs
- Input: a path or URL, plus the inventory row.
- Output: `observation` rows with `table_ref = table<k>|col<n>:<heading>` and `row_ref = table<k>:r<row>`; `text_fact` rows `section<i>` (heading as topic) and `link<i>` (topic `data_link`).
- Run it: `python run.py --input page.html --db demo.db --view` or `--input https://...`.

## Decisions: model vs fixed code
| Step | Tool | Decision rule |
|---|---|---|
| Read the page | Python `html.parser` | scripts, styles, navigation and footers are dropped; no JavaScript is run |
| Table grid | `_expand` | `colspan` and `rowspan` cells are repeated into every cell they cover |
| Header | `merge_header` | header rows above the first row with a number are joined per column (`Rice` + `Area` gives `Rice Area`) |
| Row roles | `structured_agent.parse_generic` | district, year, crop and value columns found by rule; rows of other states are not loaded |
| Column meaning | `table_mapper.map_columns` | rules first; **the LLM, if on, for headings the rules do not know**; its answer must pass the checks in `docs/llm.md` |
| Values | code | copied from the cell; `--` and `..` stored as missing, never as zero |

## Limitations
- Not run on real inventory pages. Real pages may use layout tables or nested tables that this reader handles poorly.
- A page that builds its tables with JavaScript gives text only, or fails with a clear message if nothing is readable.
- A table needs a district, year or crop column and at least one numeric column; other tables are skipped and counted.
- Text is stored as written; no facts are extracted from prose.
