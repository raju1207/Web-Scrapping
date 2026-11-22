# UDISE+ and CBSE SARAS School Data Pipeline

A Python platform for collecting authorized school information from [UDISE+](https://kys.udiseplus.gov.in/) and [CBSE SARAS](https://saras.cbse.gov.in/SARAS/AffiliatedList/ListOfSchdirReport), mapping it to Canonical School Schema v4, and maintaining source-specific school records in PostgreSQL.

The core pipeline is implemented and locally tested. PostgreSQL-first, database-only collection is an explicit option; the original file workflow remains available. **Continuous scheduling and default database writes are disabled. No production target is approved.**

## Architecture

The database-only workflow with `--new-only` is:

~~~text
Authorized source listing
  -> validate affiliation numbers / UDISE codes
  -> bounded PostgreSQL identifier lookup
  -> skip an already committed school / requested UDISE year
  -> collect eligible details
  -> map and validate Canonical v4 in memory
  -> transaction: raw + canonical JSONB, history, source/entity links,
                  yearly snapshot, completion and checkpoint
  -> commit
  -> separate post-commit acknowledgement
~~~

SARAS identity uses the CBSE affiliation number. UDISE lookup requires the school code **and requested academic year**; a school without that year's committed snapshot remains eligible. Existing candidates consume the candidate limit, so the limit does not mean a target number of successful imports.

Both modes reuse [the existing import pipeline](database/scrape_pipeline.py), mappers, validators and loaders. Possible cross-source matches require review and are not automatically merged. Source PIN values are checked without inventing PINs from school names or addresses.

| Capability | Implementation |
|---|---|
| Source collection | SARAS browser collection; UDISE official catalogs, authorized saved listings and detail requests |
| PostgreSQL-first collection | Validated identifiers checked in bounded batches before detail requests |
| Canonical v4 | Source-specific mapping, validation, provenance and missing-value handling |
| Database-only storage | Raw/canonical JSONB, content hashes, archive history and yearly snapshots |
| Identity and duplicates | Stable source identities, constraints, advisory locking and repeat-import checks |
| Recovery | Transaction rollback, separate commit/ACK reporting and read-only reconciliation |
| Scheduling safety | Approved-profile admission and hard child-process supervision |
| Deployment preparation | Read-only Windows checks and inactive PostgreSQL provisioning templates |

## Storage and failure behavior

Use `--storage-mode database --database` to select database-only storage. Add `--new-only` for pre-detail committed-record skipping. Without it, existing records follow the incremental refresh policy; `--refresh` requests a deliberate refresh and conflicts with `--new-only`.

Database-only operation creates no local school raw/canonical JSON or immutable payload files. Logical `data/...` paths in database provenance are archive keys. Small operational reports, logs and locks may still be local.

School data, completion evidence and the detail checkpoint commit atomically. Acknowledgement is written afterward. Validation/import failure rolls back the transaction. Lookup/database failures stop collection without falling back to local school payloads. If ACK writing fails after commit, the committed school remains discoverable; reporting distinguishes that state from a successful ACK. An uncertain commit is reported as uncertain.

The retained file mode saves raw JSON, immutable retry inputs and local journals. Its explicit recovery operations can write files or replay pending imports. Database-only mode does not replay those historical queues. See [database-only behavior](docs/database_only.md) for the complete policy.

History preservation is enforced by the importer. Its archive UPDATE permission does not provide row-level immutability against arbitrary SQL. Read-only ACK reconciliation detects inconsistencies; it does not repair checkpoints or replay imports.

## Project structure

~~~text
udise_dynamic_scraper.py        UDISE+ entry point
saras_dynamic_scraper.py        SARAS entry point
daily_scheduler.py             Shared daily scheduler
school_scraping/                Configuration, runtimes, mapping, validation,
                               coverage, safety and maintenance modules
database/                      Existing DDL, import pipeline, loaders and API
database/provisioning/production/
                               Guarded future provisioning templates
config/                        Source settings and inactive deployment examples
tests/                         Offline tests and guarded integration fixtures
docs/                          Operational, validation and deployment guides
~~~

Local datasets, logs, reports, archives, virtual environments and private validation evidence may also be retained. They are not required to run the offline suite. Do not delete recovery evidence or publish private operational artifacts as part of a source submission. Historical file moves and maintenance commands are documented in [the cleanup inventory](docs/cleanup_inventory.md).

## Technologies

The verified local baseline uses Windows 11, Python 3.14.6 and PostgreSQL 17.5. Runtime dependencies are declared in [requirements.txt](requirements.txt).

| Technology | Purpose |
|---|---|
| Python, Requests, Playwright/Chromium, Beautiful Soup | Source access and parsing |
| PostgreSQL, Psycopg, SQL/JSONB | Transactional school data, history and tracking |
| pandas and existing processing modules | Standardization and data audits |
| FastAPI and Uvicorn | Optional local search interface |
| pytest and unittest fixtures | Offline and isolated database verification |

## Install for offline review

From the project root, use a reviewed Python 3.14 interpreter. The following assumes `python` selects that interpreter:

~~~powershell
python -m venv venv
$Python = '.\venv\Scripts\python.exe'
& $Python -m pip install -r requirements.txt
& $Python -m pip install pytest==9.2.0
~~~

pytest 9.2.0 is the separately verified test dependency; it is not included in the runtime requirements. Offline tests do not require a running PostgreSQL server, browser installation or source access. Chromium installation is needed only for separately authorized browser collection.

## Safe offline checks

Use a fresh PowerShell session. These commands clear database/test routing only in that process, disable dotenv loading, and run without an integration DSN:

~~~powershell
$Python = '.\venv\Scripts\python.exe'
$env:PYTHON_DOTENV_DISABLED = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'

Get-ChildItem Env: | Where-Object {
    $_.Name -match '^(PG|DB_)|^SCHOOL_SCRAPING_.*(DSN|GUARD)$'
} | ForEach-Object {
    [Environment]::SetEnvironmentVariable($_.Name, $null, 'Process')
}

& $Python -B -m compileall -q school_scraping database
& $Python -B -m pytest -q -rs -p no:cacheprovider
~~~

Compilation can create ignored bytecode caches; it does not execute the modules. With integration DSNs absent, PostgreSQL cases are **skipped**, not treated as passed. Never redirect them to a historical database to remove skips. Test guards require explicitly approved isolated targets; the additional Section 14A route also verifies a pinned rehearsal manifest and server identity.

A focused offline review can use:

~~~powershell
& $Python -B -m pytest -q -rs -p no:cacheprovider tests/test_database_only.py tests/test_database_only_sources.py tests/test_pipeline_dynamic.py tests/test_scheduler.py tests/test_production_safety.py tests/test_windows_readiness.py
~~~

Current submission results and limitations belong in [the submission report](docs/PROJECT_SUBMISSION.md); historical test totals are not a claim about a reviewer's environment.

## Preview the collection CLI

These examples retain `--dry-run`: they make no source/database requests, open no browser and create no run files.

~~~powershell
& $Python -B saras_dynamic_scraper.py --state Delhi --limit 1 --database --storage-mode database --new-only --dry-run
& $Python -B udise_dynamic_scraper.py --state Delhi --academic-year 2025-26 --limit 1 --database --storage-mode database --new-only --dry-run
& $Python -B daily_scheduler.py --source both --state Delhi --limit 1 --database --storage-mode database --new-only --dry-run
~~~

Dry-run previews configuration; it does not verify current source authorization, live year IDs, database connectivity or geographic completeness. Removing that flag makes the scraper commands operational and requires a separately reviewed target and authorized source access.

Both sources preserve their access restrictions. HTTP 429 stops collection without automatic retry. CAPTCHA/authentication challenges require authorized manual handling; no bypass is implemented. Saved UDISE responses establish only their exported scope.

## Configuration and scheduling

[states_pincodes.json](config/states_pincodes.json) is shared by both scrapers and the scheduler. Explicit source/state/district/PIN/year options override the relevant configured scope. Existing source locations are retained, including the enabled Haryana SARAS location; do not run a bare collection command assuming no scope is enabled.

The current shared settings are:

~~~json
{
  "database": {"enabled": false},
  "scheduler": {
    "enabled": false,
    "time": "23:00",
    "timezone": "Asia/Kolkata"
  }
}
~~~

The daily design is 11 PM IST, equivalent to 17:30 UTC. No Windows scheduled task or continuous scheduler is activated by these settings. The existing loop waits for the next future daily occurrence; `--once` is an immediate cycle, not an offline preview. Restarting after the scheduled time does not automatically catch up a missed run.

Database-only scheduled jobs require an approved `--production-profile`, explicit source/geographic/year scope, approved hard timeout and successful production preflight before child launch. Direct scraper and maintenance commands remain independently write-capable.

### Private database settings

The actual database-only connector requires explicit `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` and `DB_PASSWORD`. Reviewed profiles also control TLS and connection/statement/lock timeouts. Supply secrets through an approved private process/service configuration; never put passwords, sessions, headers, private keys or secret connection strings into source files, command arguments, reports or Git.

No production database name, port, login, OID or server identity has been selected. The [production example profile](config/production_safety.production.example.json) intentionally retains null identities and false approvals. Ports 5432, 55439, 55449 and 59487 are protected, not suggested deployment targets.

Do not apply migrations, render server/HBA proposals or start database-backed scheduling from this README. Use the reviewed guides below and keep the actual production profile unapproved until every operational decision is verified.

## PostgreSQL model

The pipeline uses the existing `school_scraping` schema:

| Tables | Responsibility |
|---|---|
| `json_file_archive` | Original and canonical JSONB, serialized observations, hashes and history |
| `school_entities`, `school_source_records` | Master entities and source-specific identifiers/relationships |
| `school_yearly_snapshots` | UDISE snapshots unique to source record and academic year |
| `school_match_candidates` | Candidate links requiring review |
| `scrape_runs`, `scrape_tasks`, `scrape_events` | Run/task state and transactional receipt evidence |
| `scrape_errors`, `scrape_checkpoints` | Failure resolution, imported state and detail freshness |

Startup does not apply DDL. The future guarded migration wrapper preserves the order of the existing [archive schema](database/schema.sql), [school schema](database/schema_school_tables.sql) and [tracking migration](database/migrations/001_scraping_memory.sql). The new provisioning templates have received static review, not server execution or runtime SQL acceptance.

## Verified scope and limitations

- The recorded Delhi SARAS dataset contains **2,255 records** mapped, validated and exported as Canonical v4, with zero recorded mapping exceptions or changed PIN values. This is a scoped historical dataset, not a current national school census.
- Controlled Chandigarh live acceptance on 20 November 2026 verified one new import and an independent existing-record skip for each source. UDISE used an authorized saved listing for 2025-26; fresh CAPTCHA listing discovery was not established by that test.
- The separate PostgreSQL 17.5 SCRAM rehearsal exercised a real restricted login with mocked sources, JSONB/history, year-aware skips, rollback and post-commit ACKs. Its recorded PostgreSQL cases passed in a new disposable environment. See [the rehearsal report in the setup guide](docs/POSTGRESQL_LOCAL_SETUP.md).
- Rehearsal monitoring was mocked, transport used an explicit test-only loopback exception, and its backup was local and unencrypted. These results do not establish production monitoring, TLS, encrypted off-machine recovery or disaster-recovery objectives.
- Candidate limits, existing-record skips and successful runs do not prove complete PIN, geographic or India-wide coverage.

### Optional search API

[database/api/main.py](database/api/main.py) provides GET `/`, `/health`, `/schools` and `/schools/{entity_id}` using parameterized queries and read-only database transactions. It depends on the separately provided `school_scraping.school_master_search` view, which the three pipeline DDL files do not create.

The API is an optional local interface. Authentication and public deployment are not implemented or approved; do not present it as a secured production service.

## Documentation and remaining deployment work

| Guide | Purpose |
|---|---|
| [Project submission](docs/PROJECT_SUBMISSION.md) | Reviewer overview, implementation evidence and final audit results |
| [Database-only operation](docs/database_only.md) | CLI policy, storage boundaries and failure semantics |
| [Production readiness](docs/PRODUCTION_READINESS.md) | Admission, ACK reconciliation, monitoring and rollback requirements |
| [Windows local deployment](docs/WINDOWS_LOCAL_DEPLOYMENT.md) | Read-only host checks and inactive 23:00 task design |
| [Local PostgreSQL rehearsal](docs/POSTGRESQL_LOCAL_SETUP.md) | Isolated SCRAM/restricted-role integration and restore evidence |
| [Production PostgreSQL plan](docs/POSTGRESQL_PRODUCTION_SETUP.md) | Unapproved identity, TLS/SCRAM proposals and future provisioning |
| [Provisioning template notes](database/provisioning/production/README.md) | Stage guards, ownership, grants and static-validation limitations |
| [Cleanup and maintenance inventory](docs/cleanup_inventory.md) | Relocated modules, retained artifacts and deliberate maintenance commands |

Production remains blocked pending approved identities and credentials, TLS policy, Windows account/ACL/power decisions, encrypted off-machine backups, key recovery, retention, monitoring/alert delivery and an independently accepted restore procedure. No deployment, live scraping, scheduler activation or automatic Git publication is part of the submission audit.
