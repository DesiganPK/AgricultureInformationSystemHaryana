-- Haryana agri knowledge base: schema (SQLite syntax; portable to PostgreSQL)
PRAGMA foreign_keys = ON;

-- Reference tables -----------------------------------------------------
CREATE TABLE district (
  district_id   INTEGER PRIMARY KEY,          -- surrogate key (LGD code may not be loaded yet)
  lgd_code      INTEGER UNIQUE,               -- official LGD district code; NULL until filled in
  name_en       TEXT NOT NULL UNIQUE,         -- canonical: Gurugram, Nuh ...
  name_hi       TEXT
);
CREATE TABLE district_alias (                 -- Gurgaon->Gurugram, Mewat->Nuh, Hindi spellings
  alias         TEXT PRIMARY KEY,
  district_id   INTEGER NOT NULL REFERENCES district(district_id)
);
CREATE TABLE crop (
  crop_id       INTEGER PRIMARY KEY,
  name_en       TEXT NOT NULL UNIQUE,
  name_hi       TEXT
);
CREATE TABLE unit (                           -- keeps the unit exactly as printed + a normalised form
  unit_id       INTEGER PRIMARY KEY,
  raw_label     TEXT NOT NULL UNIQUE,         -- 'hectare', '000 ha', 'Rs/quintal'
  canonical     TEXT NOT NULL,                -- 'ha', 'ha', 'INR/quintal'
  factor        REAL NOT NULL DEFAULT 1.0     -- raw -> canonical multiplier
);

-- Provenance -----------------------------------------------------------
CREATE TABLE source (
  source_id     INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  url           TEXT NOT NULL UNIQUE,
  source_type   TEXT NOT NULL CHECK (source_type IN
                ('html','pdf_digital','pdf_scanned_hindi','csv','api','xlsx')),
  language      TEXT NOT NULL DEFAULT 'en',
  period_start  TEXT, period_end TEXT,        -- as stated by source; NULL if unverified
  link_status   TEXT
);
CREATE TABLE agent (
  agent_id      INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE          -- 'structured_data','digital_pdf','hindi_ocr','web_content'
);
CREATE TABLE extraction_run (
  run_id        INTEGER PRIMARY KEY,
  agent_id      INTEGER NOT NULL REFERENCES agent(agent_id),
  source_id     INTEGER NOT NULL REFERENCES source(source_id),
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  tool          TEXT NOT NULL,                -- e.g. 'docling', 'tesseract-hin'
  tool_version  TEXT,
  model         TEXT,                         -- NULL when rule-based
  status        TEXT NOT NULL CHECK (status IN ('ok','partial','failed','retried')),
  retry_count   INTEGER NOT NULL DEFAULT 0,
  manual_steps  INTEGER NOT NULL DEFAULT 0    -- feeds the automation metric
);

-- Facts ----------------------------------------------------------------
CREATE TABLE observation (
  obs_id        INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES extraction_run(run_id),
  source_id     INTEGER NOT NULL REFERENCES source(source_id),
  -- where in the source
  page_no       INTEGER, table_ref TEXT, row_ref TEXT, record_id TEXT,
  -- what it is about
  district_id   INTEGER REFERENCES district(district_id), -- NULL = state-level or unresolved name
  district_raw  TEXT,                                    -- district name exactly as printed in source
  tehsil_block  TEXT,
  crop_id       INTEGER REFERENCES crop(crop_id),        -- NULL if not crop-specific
  season        TEXT CHECK (season IN ('kharif','rabi','zaid','annual') OR season IS NULL),
  year_label    TEXT,                                    -- '2022-23' kept as printed
  indicator     TEXT NOT NULL,                           -- 'area','production','yield','price_modal','ground_water_draft'
  -- the value
  value_num     REAL,
  value_text    TEXT,                                    -- raw cell text, always kept
  unit_id       INTEGER REFERENCES unit(unit_id),
  missing_reason TEXT CHECK (missing_reason IN ('not_reported','dash_in_source','ocr_unreadable','not_applicable') OR missing_reason IS NULL),
  confidence    REAL CHECK (confidence BETWEEN 0 AND 1),
  needs_review  INTEGER NOT NULL DEFAULT 0,
  as_of         TEXT,                                    -- bulletin date (ISO) for overwritten/cumulative sources; NULL otherwise
  CHECK (value_num IS NOT NULL OR missing_reason IS NOT NULL)  -- never silently empty
);

-- Narrative knowledge (HTML pages, bulletins): variety/advisory text
CREATE TABLE text_fact (
  fact_id       INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES extraction_run(run_id),
  source_id     INTEGER NOT NULL REFERENCES source(source_id),
  location_ref  TEXT NOT NULL,                -- page/section/URL fragment
  crop_id       INTEGER REFERENCES crop(crop_id),
  topic         TEXT NOT NULL,                -- 'salt_tolerant_variety','subsidy_rule'
  content       TEXT NOT NULL,
  language      TEXT NOT NULL DEFAULT 'en'
);

-- Evaluation results linked back to runs
CREATE TABLE eval_result (
  eval_id       INTEGER PRIMARY KEY,
  run_id        INTEGER NOT NULL REFERENCES extraction_run(run_id),
  evaluator     TEXT NOT NULL,                -- 'tool_call_accuracy','task_adherence','exact_value_check'
  score         REAL, passed INTEGER, langsmith_run_url TEXT
);

-- duplicate control: same fact from the same source is stored once.
-- COALESCE is needed because NULLs never collide in a plain UNIQUE constraint.
CREATE UNIQUE INDEX ux_obs_dedup ON observation(
  source_id, indicator, COALESCE(district_id,-1), COALESCE(district_raw,''), COALESCE(crop_id,-1), COALESCE(season,''),
  COALESCE(year_label,''), COALESCE(as_of,''), COALESCE(tehsil_block,''), COALESCE(table_ref,''), COALESCE(row_ref,''));
CREATE INDEX ix_obs_query ON observation(district_id, crop_id, indicator, year_label);
CREATE VIEW v_trace AS                         -- value -> source page, for the traceability demo
  SELECT o.obs_id, d.name_en district, c.name_en crop, o.season, o.year_label, o.as_of, o.indicator, o.value_num, u.canonical unit,
         s.name source, s.url, o.page_no, o.table_ref, o.row_ref, a.name agent, r.run_id
  FROM observation o JOIN source s USING(source_id) JOIN extraction_run r USING(run_id)
  JOIN agent a USING(agent_id) LEFT JOIN district d USING(district_id)
  LEFT JOIN crop c USING(crop_id) LEFT JOIN unit u USING(unit_id);
