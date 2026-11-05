PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS games (
  appid INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aspects (aspect TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS datasets (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, start_ts INTEGER NOT NULL,
  end_ts INTEGER NOT NULL, filters_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('collecting','complete','partial','failed')),
  CHECK(start_ts<end_ts)
);
CREATE TABLE IF NOT EXISTS collection_jobs (
  dataset_id TEXT NOT NULL REFERENCES datasets(id), appid INTEGER NOT NULL REFERENCES games(appid),
  cursor TEXT NOT NULL DEFAULT '*', page_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending', error TEXT,
  PRIMARY KEY(dataset_id,appid)
);
CREATE TABLE IF NOT EXISTS reviews (
  dataset_id TEXT NOT NULL REFERENCES datasets(id), review_id TEXT NOT NULL,
  appid INTEGER NOT NULL REFERENCES games(appid), review_text TEXT NOT NULL,
  content_hash TEXT NOT NULL, language TEXT NOT NULL,
  timestamp_created INTEGER NOT NULL, timestamp_updated INTEGER NOT NULL,
  voted_up INTEGER CHECK(voted_up IN (0,1)), playtime_minutes INTEGER,
  playtime_forever_minutes INTEGER, steam_purchase INTEGER, received_for_free INTEGER,
  votes_up INTEGER, observed_at TEXT NOT NULL, raw_path TEXT NOT NULL,
  PRIMARY KEY(dataset_id,review_id)
);
CREATE INDEX IF NOT EXISTS ix_reviews_app_time ON reviews(dataset_id,appid,timestamp_created);
CREATE TABLE IF NOT EXISTS analysis_runs (
  id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES datasets(id),
  created_at TEXT NOT NULL, model TEXT NOT NULL, base_url TEXT NOT NULL,
  prompt_version TEXT NOT NULL, prompt_hash TEXT NOT NULL, prompt_text TEXT NOT NULL,
  settings_json TEXT NOT NULL, seed INTEGER NOT NULL, per_game INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS extraction_results (
  run_id TEXT NOT NULL REFERENCES analysis_runs(id), review_id TEXT NOT NULL,
  input_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','success','failed','skipped')),
  error TEXT, attempts INTEGER NOT NULL DEFAULT 0, elapsed_seconds REAL,
  response_path TEXT, usage_json TEXT, updated_at TEXT NOT NULL,
  PRIMARY KEY(run_id,review_id)
);
CREATE TABLE IF NOT EXISTS mentions (
  run_id TEXT NOT NULL, review_id TEXT NOT NULL, mention_index INTEGER NOT NULL,
  aspect TEXT NOT NULL REFERENCES aspects(aspect), issue TEXT NOT NULL,
  polarity TEXT NOT NULL CHECK(polarity IN ('positive','negative','mixed','neutral')),
  target TEXT, progress TEXT, evidence TEXT NOT NULL,
  PRIMARY KEY(run_id,review_id,mention_index),
  FOREIGN KEY(run_id,review_id) REFERENCES extraction_results(run_id,review_id)
);
CREATE TABLE IF NOT EXISTS sampling_strata (
  run_id TEXT NOT NULL REFERENCES analysis_runs(id), appid INTEGER NOT NULL REFERENCES games(appid),
  block_index INTEGER NOT NULL, from_percent REAL, to_percent REAL,
  pool_count INTEGER NOT NULL, target_count INTEGER NOT NULL, selected_count INTEGER NOT NULL,
  min_minutes INTEGER, max_minutes INTEGER, inclusion_probability REAL,
  PRIMARY KEY(run_id,appid,block_index)
);
CREATE TABLE IF NOT EXISTS sample_members (
  run_id TEXT NOT NULL, review_id TEXT NOT NULL, block_index INTEGER NOT NULL,
  selection_order INTEGER NOT NULL,
  PRIMARY KEY(run_id,review_id),
  FOREIGN KEY(run_id,review_id) REFERENCES extraction_results(run_id,review_id)
);
CREATE VIEW IF NOT EXISTS reviews_enriched AS
SELECT r.*, g.name AS game,
  date(timestamp_created,'unixepoch','+8 hours') AS created_day,
  CASE WHEN playtime_minutes IS NULL OR playtime_minutes<0 THEN '时长缺失'
       WHEN playtime_minutes<120 THEN '01 不足2小时'
       WHEN playtime_minutes<600 THEN '02 2—10小时'
       WHEN playtime_minutes<1800 THEN '03 10—30小时'
       WHEN playtime_minutes<3600 THEN '04 30—60小时'
       ELSE '05 60小时及以上' END AS duration_group
FROM reviews r JOIN games g USING(appid);
CREATE VIEW IF NOT EXISTS analysis_review_base AS
SELECT a.id AS run_id, r.*, e.status AS extraction_status
FROM analysis_runs a
JOIN reviews_enriched r ON r.dataset_id=a.dataset_id
JOIN extraction_results e ON e.run_id=a.id AND e.review_id=r.review_id;
