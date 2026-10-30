
CREATE SCHEMA IF NOT EXISTS school_scraping;

CREATE TABLE IF NOT EXISTS school_scraping.json_file_archive (
    file_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    relative_path TEXT NOT NULL UNIQUE,
    dataset_layer TEXT NOT NULL,
    source_system TEXT NOT NULL DEFAULT 'unknown',
    region TEXT,
    academic_year TEXT,
    file_name TEXT NOT NULL,

    content_sha256 TEXT NOT NULL
        CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),

    file_size_bytes BIGINT NOT NULL
        CHECK (file_size_bytes >= 0),

    json_root_type TEXT NOT NULL
        CHECK (
            json_root_type IN (
                'object', 'array', 'string',
                'number', 'boolean', 'null'
            )
        ),

    raw_json TEXT NOT NULL,
    payload JSONB NOT NULL,

    file_modified_at TIMESTAMPTZ,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_json_archive_layer
    ON school_scraping.json_file_archive (dataset_layer);

CREATE INDEX IF NOT EXISTS idx_json_archive_source_region
    ON school_scraping.json_file_archive (source_system, region);

CREATE INDEX IF NOT EXISTS idx_json_archive_academic_year
    ON school_scraping.json_file_archive (academic_year);
