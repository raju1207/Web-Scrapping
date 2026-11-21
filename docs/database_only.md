# Database-only collection

The default storage mode is `files`. Database storage is an explicit choice shared by both scrapers and the scheduler:

```text
--storage-mode database --database [--new-only]
```

Use `--dry-run` to review a bounded command before executing it. The implementation does not enable locations, scheduling or database integration in the original configuration. Existing source authorization, manual CAPTCHA handling, retry bounds and HTTP 429 stops still apply.

Database-only **scheduler** jobs now additionally require an operator-approved production safety profile, positive hard timeout, and successful admission of every planned job. See [PRODUCTION_READINESS.md](PRODUCTION_READINESS.md). The inactive example profile fails closed and no deployment has been approved. Direct scraper CLIs retain their existing explicit opt-in behavior. Read-only ACK reconciliation and readiness checks neither replay imports nor repair checkpoints.

The configured daily scheduler time is `23:00` (11 PM) in `Asia/Kolkata`, equivalent to `17:30` UTC. Scheduler and default database integration remain disabled. This timing update preserves candidate limits, database-only admission, hard process supervision and immediate `--once` behavior; it does not create or activate an external scheduled task.

## Collection policy

Identifiers come from source listings and are validated before parameterized batch lookups. SARAS uses affiliation identity; UDISE requires a committed snapshot for the selected school/year. A pending task or event alone is insufficient. One connection is reused for a collection run; lookup batches contain at most 500 identifiers.

`--new-only` skips committed identities before details. Without it, committed identities follow the detail refresh interval and database checkpoints. Missing or incomplete records remain eligible. `--refresh` requests a deliberate detail refresh and conflicts with new-only collection. `--limit` continues to bound unique candidates, including existing skips and invalid candidates that consume the source's normal budget. It does not mean a target number of successful imports.

An existing identity does not prove that its current PIN/geography matches a requested filter. Skips have no fresh detail evidence and cannot certify complete geographic coverage. In database mode, PIN-filtered details are not saved as local payload caches.

## Storage and commit boundaries

The existing `import_scraped_record()` accepts either legacy physical snapshot inputs or an in-memory `raw_record` with a checked hash and logical source path. Both inputs run the same standardization, canonical v4 mapping, validation and loader. There is no second school model or new migration.

Database mode stores original raw and canonical JSONB, immutable content hashes/history, provenance, source/master relationships and UDISE snapshots in the existing tables. Match candidates retain the existing review process. Logical paths in provenance and archive rows are keys; the database-only importer does not read or create files at those paths.

The first transaction commits school data, the import request/completion evidence and the completed detail checkpoint atomically. A source/identifier advisory lock protects concurrent imports; new-only imports recheck existence under that lock. The second transaction records acknowledgement only after the first commit succeeds. A run reports confirmed commits and acknowledgements separately.

| Failure boundary | Durable state and recovery |
|---|---|
| Mapping/validation/import before commit | Transaction rolls back; no completed school is claimed. Retry can fetch/reprocess it. |
| Connection loss during commit | Outcome can be unknown. Collection stops; a later run checks committed PostgreSQL state. |
| Acknowledgement fails after data commit | School/completion remain committed; no successful acknowledgement is claimed. A later new-only run skips the committed identity without duplicating it. |
| Lookup/database unavailable | Collection stops. No local spool or silent fallback exists. |

Database-mode failed tasks retain listing metadata in PostgreSQL. Normal retry timing/attempt limits and explicit `--retry-failed` apply. A process crash before commit can require another detail request because uncommitted full payloads have no offline copy. Small operational reports/locks may still be local; school raw JSON, canonical JSON and immutable import payload files are not generated.

## Prerequisites and legacy recovery

Review the intended database identity and role before enabling writes. Database mode requires explicit private `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, existing `database/schema.sql`, `database/schema_school_tables.sql`, and `database/migrations/001_scraping_memory.sql`, plus their table/sequence privileges. Startup does not apply schema changes. Help/dry-run does not connect.

`--storage-mode database --no-database`, new-only file storage, and database-mode `--sync-pending` are rejected. Existing file data, journals, snapshots and backups are retained. Use legacy file mode deliberately for `--sync-pending`; database-only runs and scheduler jobs do not read or replay that queue.

Run reports include examined candidates, database identifiers checked, existing skips, detail fetches avoided, detail records fetched, canonical validations, confirmed imports, acknowledgements, failed/pending records, lookup duration, and zero school payload files. Confirmed commits with failed acknowledgements have a separate pending-acknowledgement count. Database failures include confirmed/unknown commit status; a stopped database cannot guarantee a durable final run report.

## Offline verification

Fake-store/source tests run without PostgreSQL. Guarded transaction and mocked-source integration tests use `SCHOOL_SCRAPING_DATABASE_ONLY_TEST_DSN` and refuse endpoints other than `127.0.0.1:55449`, database names outside `database_only_test_*`, and data directories outside the retained isolated restore cluster. The old `SCHOOL_SCRAPING_TEST_DSN` must remain unset for this verification. Apply existing DDL only to a freshly provisioned disposable database; all website responses remain mocked.

Verification completed on 2026-11-20: focused suite 94 passed, 9 skipped, 78 subtests; full suite 262 passed, 25 skipped, 219 subtests, with no failures in either final run. All new guarded PostgreSQL cases ran; the skipped cases require the deliberately unset legacy database DSN. The existing databases' rows, schemas and sequences, and historical file hashes remained unchanged. The initial UDISE test assertion was corrected to expect the source's original integer PIN; production mapping was unchanged.

Across twelve successful mocked-source runs, including repeated verification passes, 21 identifiers were checked, 12 existing identities were skipped, and 9 details were validated, committed and acknowledged. These are synthetic test observations, not live collection or a production benchmark. Database-only operations generated zero local school payload files. The disposable database is retained as `database_only_test_936f6d824445` on the isolated restore cluster. Evidence is under `.refactor_validation/database_only_20261118/`, the directory created when this work began.

## Production files changed

| File | Change |
|---|---|
| `school_scraping/database_only.py` (new) | Batch existence queries, shared connection, existing transactional projector/pipeline, completion and post-commit acknowledgement. |
| `database/scrape_pipeline.py` | In-memory input adapter alongside unchanged legacy physical snapshot loading; logical archive provenance. |
| `school_scraping/incremental.py` | Database-backed lifecycle adapter, refresh policy, failure handling and performance metrics. |
| `saras_dynamic_scraper.py` | Bounded pre-detail lookup and in-memory storage branch. |
| `udise_dynamic_scraper.py` | Validated school/year lookup and in-memory storage branch. |
| `school_scraping/config.py` | Shared storage/new-only settings and conflict validation. |
| `daily_scheduler.py` | Flag forwarding and database-mode bypass of legacy journal/replay/due checks. |

No mapper, validator, loader, schema or migration was changed. No live source, production activation, original configuration edit, historical queue replay, Git commit or push was performed.
