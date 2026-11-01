
BEGIN;

-- 1. Master school identity
CREATE TABLE IF NOT EXISTS school_scraping.school_entities (
    entity_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    preferred_school_name TEXT NOT NULL,
    region TEXT NOT NULL,
    state TEXT,
    district TEXT,
    address TEXT,
    pincode TEXT,
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    phone TEXT,
    email TEXT,
    website TEXT,
    entity_status TEXT NOT NULL DEFAULT 'active'
        CHECK (entity_status IN ('active', 'merged')),
    merged_into_entity_id BIGINT
        REFERENCES school_scraping.school_entities(entity_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (
        merged_into_entity_id IS NULL
        OR merged_into_entity_id <> entity_id
    )
);

-- 2. Original source records: UDISE and SARAS
CREATE TABLE IF NOT EXISTS school_scraping.school_source_records (
    source_record_pk BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id BIGINT NOT NULL
        REFERENCES school_scraping.school_entities(entity_id),
    archive_file_id BIGINT NOT NULL UNIQUE
        REFERENCES school_scraping.json_file_archive(file_id),
    source_system TEXT NOT NULL
        CHECK (source_system IN ('udise', 'saras')),
    region TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    school_name TEXT NOT NULL,
    normalized_school_name TEXT NOT NULL,
    udise_code TEXT,
    cbse_affiliation_number TEXT,
    state TEXT,
    district TEXT,
    address TEXT,
    pincode TEXT,
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    phone TEXT,
    email TEXT,
    website TEXT,
    entity_resolution JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source_system, region, source_record_id)
);

-- 3. Yearly school statistics
CREATE TABLE IF NOT EXISTS school_scraping.school_yearly_snapshots (
    snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_record_pk BIGINT NOT NULL
        REFERENCES school_scraping.school_source_records(source_record_pk),
    source_archive_file_id BIGINT NOT NULL
        REFERENCES school_scraping.json_file_archive(file_id),
    academic_year TEXT NOT NULL,
    total_students BIGINT,
    total_teachers BIGINT,
    student_teacher_ratio NUMERIC(12,4),
    infra_score NUMERIC(12,4),
    pass_percentage_x NUMERIC(7,3),
    pass_percentage_xii NUMERIC(7,3),
    schema_version TEXT,
    source TEXT,
    snapshot_payload JSONB NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source_record_pk, academic_year),

    CHECK (total_students IS NULL OR total_students >= 0),
    CHECK (total_teachers IS NULL OR total_teachers >= 0),
    CHECK (
        student_teacher_ratio IS NULL
        OR student_teacher_ratio >= 0
    )
);

-- 4. UDISE-SARAS match candidates
CREATE TABLE IF NOT EXISTS school_scraping.school_match_candidates (
    candidate_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    udise_source_record_pk BIGINT NOT NULL
        REFERENCES school_scraping.school_source_records(source_record_pk),
    saras_source_record_pk BIGINT NOT NULL
        REFERENCES school_scraping.school_source_records(source_record_pk),

    match_status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (
            match_status IN (
                'pending_review',
                'approved',
                'rejected',
                'needs_more_evidence'
            )
        ),

    match_method TEXT,
    name_match BOOLEAN NOT NULL DEFAULT FALSE,
    pincode_match BOOLEAN,
    address_match BOOLEAN,
    website_match BOOLEAN,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_notes TEXT,
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (udise_source_record_pk, saras_source_record_pk),

    CHECK (udise_source_record_pk <> saras_source_record_pk)
);

-- Indexes for common searches and joins
CREATE INDEX IF NOT EXISTS idx_entities_region
    ON school_scraping.school_entities(region);

CREATE INDEX IF NOT EXISTS idx_source_records_entity
    ON school_scraping.school_source_records(entity_id);

CREATE INDEX IF NOT EXISTS idx_source_records_name
    ON school_scraping.school_source_records(normalized_school_name);

CREATE INDEX IF NOT EXISTS idx_source_records_udise_code
    ON school_scraping.school_source_records(udise_code);

CREATE INDEX IF NOT EXISTS idx_source_records_cbse_affiliation
    ON school_scraping.school_source_records(cbse_affiliation_number);

CREATE INDEX IF NOT EXISTS idx_source_records_region_pincode
    ON school_scraping.school_source_records(region, pincode);

CREATE INDEX IF NOT EXISTS idx_snapshots_academic_year
    ON school_scraping.school_yearly_snapshots(academic_year);

CREATE INDEX IF NOT EXISTS idx_snapshots_source_record
    ON school_scraping.school_yearly_snapshots(source_record_pk);

CREATE INDEX IF NOT EXISTS idx_match_candidates_status
    ON school_scraping.school_match_candidates(match_status);

COMMIT;
