-- Tracking only. Apply explicitly; scraper startup never changes database schemas.
BEGIN;
CREATE SCHEMA IF NOT EXISTS school_scraping;
CREATE TABLE IF NOT EXISTS school_scraping.scrape_runs (
    run_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    scope_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'running',
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS school_scraping.scrape_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES school_scraping.scrape_runs(run_id),
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS school_scraping.scrape_tasks (
    task_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES school_scraping.scrape_runs(run_id),
    source TEXT NOT NULL,
    academic_year TEXT NOT NULL DEFAULT '',
    school_identifier TEXT NOT NULL DEFAULT '',
    scope_key TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    last_event_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS school_scraping.scrape_errors (
    error_id TEXT PRIMARY KEY REFERENCES school_scraping.scrape_events(event_id),
    run_id TEXT REFERENCES school_scraping.scrape_runs(run_id),
    task_id TEXT REFERENCES school_scraping.scrape_tasks(task_id),
    source TEXT NOT NULL,
    error_category TEXT NOT NULL,
    error_message TEXT NOT NULL,
    sanitized_traceback TEXT,
    occurred_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb
);
ALTER TABLE school_scraping.scrape_errors ADD COLUMN IF NOT EXISTS resolution_status TEXT NOT NULL DEFAULT 'open';
ALTER TABLE school_scraping.scrape_errors ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ;
ALTER TABLE school_scraping.scrape_errors ADD COLUMN IF NOT EXISTS resolution_notes TEXT;
ALTER TABLE school_scraping.scrape_errors ADD COLUMN IF NOT EXISTS retry_count INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS school_scraping.scrape_checkpoints (
    source TEXT NOT NULL,
    academic_year TEXT NOT NULL DEFAULT '',
    school_identifier TEXT NOT NULL DEFAULT '',
    scope_key TEXT NOT NULL DEFAULT '',
    checkpoint_status TEXT NOT NULL,
    last_detail_check TIMESTAMPTZ,
    last_successful_check TIMESTAMPTZ,
    last_successful_save TIMESTAMPTZ,
    next_check_at TIMESTAMPTZ,
    last_known_content_hash TEXT,
    raw_path TEXT,
    imported BOOLEAN NOT NULL DEFAULT FALSE,
    last_event_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (source, academic_year, school_identifier, scope_key)
);
CREATE INDEX IF NOT EXISTS idx_scrape_runs_source_time ON school_scraping.scrape_runs(source, started_at);
CREATE INDEX IF NOT EXISTS idx_scrape_tasks_retry ON school_scraping.scrape_tasks(source, status, next_retry_at);
CREATE INDEX IF NOT EXISTS idx_scrape_tasks_scope ON school_scraping.scrape_tasks(source, academic_year, scope_key);
CREATE INDEX IF NOT EXISTS idx_scrape_errors_run ON school_scraping.scrape_errors(run_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_scrape_checkpoints_next ON school_scraping.scrape_checkpoints(source, next_check_at);
COMMIT;
