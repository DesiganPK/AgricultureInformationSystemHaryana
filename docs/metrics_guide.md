# Metrics guide: how to read the output

Definitions, floors and targets are copied from the protocol ("Quantitative metrics" and "Scoring rubric"). **Scoring rule:** a score at or below the **floor** earns 0; at or above the **target** earns the full weight; in between it is scaled linearly. Agent metrics are macro-averaged over agents, so one weak agent cannot hide behind a strong one.

## 1. Which database am I looking at?
`run_eval.py` and `db/checks.py` report on whichever database you pass with `--db`.
- `haryana.db`: the project database, 22 sources, 218,990 rows.
- A database you created in a demo (`demo.db`, `try.db`): only the files you loaded into it.

The same command gives very different M14/M15 on different databases (see section 4). **Always say which database a number is for.**

## 2. The 18 metrics

| ID | Metric | Computed as | Floor → Target | Pts | Where it comes from | State now |
|---|---|---|---|---|---|---|
| M1 | Source mapping coverage | inventory sources mapped to an agent ÷ all inventory sources | 0.50 → 1.00 | 3 | `docs/source_agent_map.csv` | **1.00** (41/41) |
| M2 | Implementation coverage | sources with an agent marked done or partial ÷ inventory | 0.20 → 0.60 | 2 | same file; spot-checked live | **0.537** (22/41) |
| M3 | Field-level F1 | F1 on the checked reference cases (see below) | 0.50 → 0.85 | 6 | `eval/run_eval.py` | not computed: references unfilled |
| M4 | Provenance accuracy | sampled records whose page / table / row / URL leads to the value ÷ records sampled | 0.60 → 0.95 | 3 | `locator_ok` column of the reference sheet; evaluator re-opens the source | not computed |
| M5 | Surprise-item F1 | F1 on the instructor's key for the 3 unseen items | 0.30 → 0.75 | 6 | live session only | not measurable in advance |
| M6 | Tool selection accuracy | correct tool chosen ÷ tool calls vs a reference trajectory | 0.60 → 0.90 | 2 | Foundry / AgentEvals | not measured |
| M7 | Tool input accuracy | calls with correct arguments ÷ calls | 0.60 → 0.90 | 2 | Foundry / AgentEvals | not measured |
| M8 | Trajectory match | cases whose trajectory matches the reference ÷ cases | 0.50 → 0.80 | 2 | AgentEvals | not measured |
| M9 | Task completion | cases with a schema-valid final output ÷ cases | 0.50 → 0.90 | 2 | Foundry | not measured |
| M10 | Groundedness | output fields supported by the source text ÷ output fields | 0.70 → 0.95 | 2 | Foundry or a calibrated judge | not measured |
| M11 | End-to-end workflow success | multi-agent runs reaching a correct "complete" ÷ runs, plus 1 live fault injection handled | 0.50 → 0.90 | 4 (+2 fault injection) | LangSmith traces; here, the automation log | partial: completion only |
| M12 | Automation rate | inputs completed with no manual intervention ÷ inputs attempted | 0.30 → 0.80 | 6 | `logs/automation_log.csv` | 1.0 on logged runs (see note) |
| M13 | Redundant call ratio (**lower is better**) | (calls − calls in the reference trajectory) ÷ calls | 0.50 → 0.10 | 2 | run stats | not measured |
| M14 | Mandatory field completeness | rows with source, source type, agent, run ID, locator, crop, location, time and unit filled (or coded missing) ÷ rows | 0.80 → 1.00 | 3 | `db/checks.py` | **0.788** on `haryana.db` (below the 0.80 floor until the Hindi sources are re-extracted; see section 4) |
| M15 | Validation pass rate | rows passing the declared rules ÷ rows | 0.70 → 0.98 | 2 | `db/checks.py`; rules in `db/dictionary.md` | **0.723** on `haryana.db` |
| M16 | Duplicate rate (**lower is better**) | rows sharing a natural key ÷ rows | 0.10 → 0.00 | 2 | `db/checks.py` | **0.000** |
| M17 | Live trace-back | random database rows correctly traced to their source location, out of 3 | 1 → 3 | 3 | evaluator picks rows; use the `v_trace` view | live |
| M18 | Adapter comparison | F1(adapter) − F1(base) on the same held-out cases; overlap with training data = 0 by file hash | see rubric | 3 (1+1+1) | `eval/adapter_comparison.csv` | done: 0.148 → 0.995 F1 (synthetic held-out) |

M18 does **not** reward a positive gain: a comparison that is honest and explained earns full marks even if the adapter does not beat the base model.

**M3, the field-match rule.** A field is correct only if crop, location and period match the reference after normalisation, the value is exact for integers or within ±1% for decimals (after unit conversion), **and the unit is correct**. A correct number with a wrong unit counts as wrong. P = correct ÷ fields extracted, R = correct ÷ fields in the reference, F1 = 2PR/(P+R).

## 3. Reading the output lines

**`python run.py …`**
- `OK` = the run completed. It says nothing about correctness (that is M3).
- `digital_pdf->hindi_ocr` = routed first to one agent, handed to another (here: legacy-font Hindi pages found).
- `rows N` = rows now in the database for that source. `rejected N` = rows dropped because they had the wrong number of values (never guessed). `skipped pages N` = pages with no table rows.

**`python eval/run_eval.py`**
- `cases: 45 {'unverified': 45}` = 45 case sheets, none with a checked reference yet. Only checked references are scored.
- `M3 <scope> not_computed` = nothing to score yet. When references are filled this shows the F1 per agent (`digital_pdf`, `hindi_ocr`, `structured_data`), per source group, and `end_to_end` (the macro-average over agents). `non_english` is the Hindi group on its own.
- `M12` and the `M14/M15/M16` lines come from the log and the database named in `--db`.

**`python db/checks.py`**
- `M14 … (18/4820)`: 18 of 4,820 rows have every mandatory field filled. `by field:` shows which field fails; the lowest is the cause.
- `M15 … (811/4820)`: rows passing the validation rules (allowed unit, parsable year, plausible value, resolvable district).
- `M16 … (0/4820)`: duplicate rows.

## 4. Why the numbers change with the database
| Database | Rows | M14 | M15 | Why |
|---|---|---|---|---|
| `haryana.db` as shipped (22 sources) | 218,990 | 0.788 | 0.723 | its Hindi rows were made by the earlier agent (unit and crop mostly empty); the State Abstract rows also lack some units and years |
| Bhiwani only, earlier agent | 4,820 | 0.004 | 0.168 | only 36 rows had a crop; 3,616 had no unit |
| Bhiwani only, current agent (LLM off) | 5,917 | 0.690 | 0.719 | crop is read from the header or coded `(not crop-specific)`; units are read from the table title; 1,660 rows still have no unit printed |
| 5 sample files, current agent (LLM off) | 8,338 | 0.771 | 0.800 | Bhiwani, the Bhiwani crop tables, prices, monsoon, sample web page |

**To raise M14 and M15 on `haryana.db`:** re-extract the Hindi sources with the current agent (commands in the README, section "Re-extract the Hindi sources"). The remaining gap is rows whose unit is not printed anywhere near the table; those stay `unspecified` and flagged, because a guessed unit would be worse than a missing one.

## 5. Estimated points from what is measured so far (not a prediction of your final score)
Using the linear rule and the weights above, on `haryana.db` as shipped: M1 3/3, M2 about 1.7/2 (0.537 between 0.20 and 0.60), M14 0/3 (0.788 is just under the 0.80 floor), M15 about 0.2/2, M16 2/2. Re-extracting the Hindi sources should move M14 and M15 up; by how much is not known until it is run. M3, M4, M5, M6 to M11, M13, M17 and M18 are unscored or unmeasured. M12 depends on the live surprise runs. **M3 (6 pts) and M4 (3 pts) need your signed references.**

## 6. What the LLM layer adds to the metrics
- **M18:** `eval/adapter_comparison.csv` from the Colab run of 17 Nov: F1 0.148 (base) vs 0.995 (adapter) on 120 synthetic held-out cases; overlap 0 by file hash and by prompt hash; 0 real files held out.
- **M6, M7, M9, M13 raw material:** `logs/llm_calls.jsonl` has one line per model call (task, valid JSON, retry, seconds, tokens) and `logs/automation_log.csv` has per-run call counts, who routed, and answers used or not used. Reference trajectories and the scoring script for these metrics are not written.
- **Reported but not scored** (latency, tokens, retries per document): columns `seconds`, `llm_seconds`, `llm_tokens`, `llm_retries` in the automation log.
