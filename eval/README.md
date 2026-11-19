# Evaluation set: how it is built, checked and scored

**Status: the case sheets are drawn; the reference values are NOT yet filled.** Until a person fills and signs them, `run_eval.py` scores nothing and writes M3 and M4 as `not_computed`. That is intended: a number only counts when it was checked against the source.

## The cases (`eval/cases/cases.csv`)
45 cases: 15 per implemented agent (the protocol minimum is 10 per agent and 30 in total, so there is room to drop invalid ones).

| Agent | Source group | Language | Source file | Cases |
|---|---|---|---|---|
| structured_data | structured_excel | English | ICRISAT area-production Excel (`data/raw/*area*production*`) | 15 |
| digital_pdf | digital_pdf_prices | English | DES farm harvest prices PDF | 9 |
| digital_pdf | digital_pdf_rainfall | English | IMD monsoon bulletin PDF | 6 |
| hindi_ocr | hindi_pdf | **Hindi** (non-English group) | DESA Bhiwani abstract PDF | 15 |

Cases were drawn at random with a fixed seed (`python eval/build_cases.py`), spread over districts, crops and years, with about one in four a value the source prints as missing, and one value the source flags with a letter (r / D / A) in the price group.

**Limits you should know.** All four source files are also the files the agents were developed on, so a fixed-code agent is being scored on its own development documents; the surprise items in the live session are the real out-of-sample test. The Excel file is not in this repository snapshot, so the 15 structured_data cases cannot be scored until it is placed in `data/raw/`.

## How to fill the reference (`eval/reference/reference.csv`)
Open the **source file** (never the database) and for each row:
1. Use `where_to_look` to find the value. Type the value **as printed** into `value_ref` (a missing value is `..`; letter flags such as `r` are fine; the Hindi abstracts print a decimal point as `-`, and `34-79` is accepted as 34.79). Type the unit **as the source states it** into `unit_ref`.
2. Check the identity columns (`district`, `crop`, `year`, and `tehsil` where present). If they are right, put `Y` in `identity_ok`. If wrong, put `N` and correct them in `district_corrected` / `crop_corrected` / `year_corrected`.
3. Put `Y` in `locator_ok` if the page / row / column in the sheet really leads to the value, otherwise `N` (this is M4, provenance accuracy).
4. Fill `verified_by` and `verified_on`. **Both are needed for the row to count.**

The agent's own value is not shown anywhere on the sheet, on purpose.

## Scoring (`python eval/run_eval.py`)
The agent is re-run on the source file (nothing is written to the database) and its record at the case's locator is compared with the reference. A field is **correct** only if all hold (protocol rule):
- district, crop and period match the reference after normalisation (below);
- the value is exact for integers and within ±1% for decimals; a missing value matches only a missing value;
- the unit matches after normalisation. A correct number with a wrong unit is wrong.

Per agent: **P** = correct ÷ fields extracted at the case locators, **R** = correct ÷ fields in the reference, **F1** = 2PR/(P+R). M3 is the **macro-average of F1 over agents**. The Hindi group is also reported alone (`non_english`).

*Precision is measured on checked fields only:* an agent that outputs extra unchecked fields is not penalised here, and the instructor's key may count them. Statuses written to `eval/results/case_results.csv`: `correct`, `wrong_value`, `wrong_unit`, `wrong_identity`, `not_extracted`, `unverified`, `source_unavailable`, `bad_reference`.

## Normalisation declared before the demo
- **District:** `district_names.canonical()` (aliases such as Hissar → Hisar, Gurgaon → Gurugram, Mewat → Nuh, Mohindergarh → Mahendragarh). **LGD codes are not loaded** (`LGD_CODES` in `district_names.py` is empty). They must be downloaded from https://lgdirectory.gov.in and filled in before the LGD part of M15 can be applied.
- **Crop:** lower-case, spaces collapsed, plus synonyms: Caster Seed = Castor seed, Groundnut = Ground nut, Arhar = Tur = Tur Arhar, Rapeseed & Mustard = Rape/Mustard.
- **Period:** `2007-2008` and `2007-08` compare equal; en-dash and `&` become `-`; a flag such as `(P)` is dropped.
- **Unit:** lower-case with these equal: `000 ha` = thousand ha = '000 ha; `Rs/quintal` = Rs. per quintal; `kg/ha` = kg per ha; `%` = percent; `000 tonnes` = thousand tonnes.
- **Value:** flag letters stripped (`1320r` = 1320); thousands commas removed.

## Other metrics written by `run_eval.py` into `eval/phase2_metrics.csv`
M1, M2 (from `docs/source_agent_map.csv`), M12 and M11 (from `logs/automation_log.csv`), M14, M15, M16 (SQL in `db/checks.py`). M5, M6 to M10, M13, M17 and M18 are written as `not_measured` with the reason; nothing is filled in to look complete. **The column layout of `phase2_metrics.csv` is provisional:** the protocol's CSV template was not available when this was built and the file must be mapped onto it.
