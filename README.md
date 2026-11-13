# School Data Scraping and Unified Database

## 1. Project Overview

This project collects, processes, standardizes, and manages school information from two sources:

- **UDISE+** – Unified District Information System for Education.
- **CBSE SARAS** – Central Board of Secondary Education affiliation records.

The project stores the original scraped JSON files, loads them into PostgreSQL, creates a unified school master database, identifies possible matches between sources, and provides a searchable REST API using FastAPI.

## Dynamic collection and recovery

There are three active scraper/scheduler entry points. Adding a location does not require another scraper.

| File | Purpose |
|---|---|
| `udise_dynamic_scraper.py` | UDISE+ discovery, incremental details, and automatic pipeline import |
| `saras_dynamic_scraper.py` | SARAS discovery, incremental details, and automatic pipeline import |
| `daily_scheduler.py` | One daily schedule for enabled locations and both sources |

The flow is `configuration -> checkpoint/due check -> official discovery -> new/due school details -> preserved raw JSON -> existing standardizer and canonical mapper -> existing validation -> PostgreSQL archive/source records/yearly snapshots -> reviewed match candidates -> FastAPI`.

Shared code lives in `school_scraping/`. `database/scrape_pipeline.py` calls the existing mappers, validators, archive upsert, school-table loaders, snapshot loader, and match-candidate loader inside one transaction. It does not create a competing school model or automatically merge schools.

### Clean project layout

The root now has exactly three Python files. Processing modules and maintenance tools have been physically moved into packages; the old root-level launch paths have been removed.

```text
Web-Scrapping/
  udise_dynamic_scraper.py
  saras_dynamic_scraper.py
  daily_scheduler.py
  README.md
  requirements.txt
  config/states_pincodes.json
  school_scraping/
    standardize_school_data.py
    canonical_v4_mapper.py
    saras_canonical_v4_mapper.py
    validate_canonical_v4.py
    validate_saras_canonical_v4.py
    common.py, config.py, datasets.py, incremental.py, memory.py
    maintenance/
      audit_list_vs_raw.py
      compare_udise_lists.py
      build_summary.py
      compare_raw_vs_standardized.py
      count_current_data.py
      missing_value_classifier.py
      raw_data_audit.py
      saras_raw_data_audit.py
      section_level_audit.py
  database/
    api/
    migrations/001_scraping_memory.sql
    scrape_pipeline.py, import_json_to_postgres.py, load_school_tables.py
  data/
    samples/gallery_data.json
    samples/standardized_sample.txt
    ... existing school datasets and history ...
  tests/
  docs/
  reports/
  logs/
  archive/                 # Recoverable backups retained
  final_data-zipped/       # Historical datasets retained
  venv/                   # Existing environment retained
  .refactor_validation/    # Test evidence, raw samples and isolated DB retained
```

The six repeated city-specific audit/list-comparison scripts were replaced by two configurable maintenance modules. The distinct utilities remain available. Samples and local recovery JSON are excluded from bulk school imports. Historical reports keep the file paths recorded when they were generated; use the package commands below for current operations.

VS Code Explorer/search exclusions hide retained environments, validation/history folders and caches. Hiding these directories does not delete them. The reviewed rules live in `.vscode/settings.json`.

### Run processing and maintenance modules

Run these commands from the project root. `--help` is safe for every relocated module. Normal processing commands intentionally regenerate derived files or reports, so choose them deliberately.

```powershell
python -m school_scraping.standardize_school_data --year 2025-26
python -m school_scraping.canonical_v4_mapper --year 2025-26
python -m school_scraping.saras_canonical_v4_mapper
python -m school_scraping.validate_canonical_v4 --year 2025-26
python -m school_scraping.validate_saras_canonical_v4

# Replaces the three city-specific list-versus-raw audits.
python -m school_scraping.maintenance.audit_list_vs_raw --location Delhi --dry-run
python -m school_scraping.maintenance.audit_list_vs_raw --location Punjab --year 2025-26 --list-file exports/punjab.json --raw-dir data/Punjab/2025-26 --report-file reports/punjab_audit.json

# Replaces the three saved/fresh school-list comparison copies.
python -m school_scraping.maintenance.compare_udise_lists --location Mumbai --dry-run
python -m school_scraping.maintenance.compare_udise_lists --location Haryana --saved-list exports/haryana_old.json --fresh-list exports/haryana_new.json --report-file reports/haryana_comparison.json

python -m school_scraping.maintenance.count_current_data
python -m school_scraping.maintenance.build_summary --help
python -m school_scraping.maintenance.compare_raw_vs_standardized --help
python -m school_scraping.maintenance.missing_value_classifier --help
python -m school_scraping.maintenance.raw_data_audit --help
python -m school_scraping.maintenance.saras_raw_data_audit --help
python -m school_scraping.maintenance.section_level_audit --help
```

The summary builder and historical audits retain their original dataset/report scope. They are explicit maintenance commands and are not scheduled as competing scrapers. `build_summary` rebuilds historical aggregate JSON when intentionally executed. The consolidated audit/comparison tools preserve their old report fields and default filenames; the comparison now labels Mumbai correctly.

### Configure locations once

Edit `config/states_pincodes.json`. Both scrapers and the scheduler read it by default. Scheduling and PostgreSQL writes are disabled. The existing Punjab/Haryana settings are retained, including the enabled Haryana SARAS location. Chandigarh, Delhi and Mumbai are configured for both sources and remain disabled. A bare scraper uses enabled locations with a bounded candidate limit.

```json
{
  "name": "punjab",
  "enabled": true,
  "state": "Punjab",
  "districts": ["Ludhiana"],
  "pincodes": ["141001", "141002"],
  "refresh_days": 30,
  "check_interval_hours": 24
}
```

Put each location object in the appropriate `sources.udise.locations` or `sources.saras.locations` array. Each location keeps its own district/PIN pairing; a Punjab PIN is not applied to Haryana accidentally. Lists may contain one or many pincodes/districts. Use multiple location objects for multiple states or separate filters within one state. Location names must be unique within each source.

- `enabled` controls a source or location.
- `defaults` provides shared settings; source and location settings override them.
- `refresh_days` controls periodic detail rechecks when no reliable source change indicator exists.
- `check_interval_hours` controls geographic discovery checks.
- `limit` is a positive candidate bound. Omitted/null limits resolve to 25; only the explicit CLI option `--unlimited` selects every candidate.
- `retry_delay_minutes` and `recovery.max_task_attempts` bound automatic failed-task retries.
- `sources.<source>.all_states: true` explicitly selects the source's available states and cannot be combined with enabled location scopes.
- UDISE `academic_year` is `YYYY-YY`; `year_id` is optional and must match the official catalog.
- UDISE `school_lists` can contain complete authorized saved official search responses. `headers_file` and SARAS `storage_state` refer to private local files, never embedded credentials.
- All relative configured paths use the project root, including when `--config` points to another file.

Configuration is validated before source requests or database operations. Invalid types, unknown settings, invalid PINs, conflicting all-state scopes, and invalid intervals fail clearly. Source state/district identifiers are still resolved from the official website, not from invented numeric codes.

Explicit `--state`/`--all-states` replaces configured geography for that command. Other explicitly supplied options override configuration. `--location NAME` selects enabled named scopes. A custom `--data-dir` also isolates recovery state unless `--state-dir` is explicitly supplied.

### Preview and run

Activate the existing virtual environment and install dependencies as described below. Browser collection requires `python -m playwright install chromium` once.

```powershell
python udise_dynamic_scraper.py --help
python saras_dynamic_scraper.py --help
python daily_scheduler.py --help

python udise_dynamic_scraper.py --state Punjab --state Haryana --limit 2 --dry-run
python saras_dynamic_scraper.py --state Haryana --pincode 134001 --limit 2 --dry-run
python daily_scheduler.py --config config/states_pincodes.json --dry-run
python saras_dynamic_scraper.py --all-states --dry-run
```

Dry runs make no source/database requests, open no browser, and write no files. With no enabled locations, a bare command prints an explanatory idle result. `--limit` limits unique candidates, including filtered or skipped candidates; it does not prove geographic completeness. Scheduler previews always show either `--limit N` or `--unlimited`. Limited or interrupted discovery does not create a successful full-scope checkpoint. `--max-duration SECONDS` stops at a safe boundary and preserves pending inputs; rerun the same command to resume.

```powershell
# Explicit small source runs, with database integration disabled by default.
python udise_dynamic_scraper.py --state Punjab --district Ludhiana --limit 2 --manual-captcha 300
python udise_dynamic_scraper.py --state Haryana --pincode 134001 --limit 2 --manual-captcha 300
python saras_dynamic_scraper.py --state Punjab --limit 2
python saras_dynamic_scraper.py --state Haryana --pincode 134001 --limit 2

# Use enabled configured locations.
python saras_dynamic_scraper.py --config config/states_pincodes.json --location punjab
python udise_dynamic_scraper.py --state Punjab --school-list .\exports\punjab.json --limit 2
```

### Source filters and authorized access

| Source | Verified behavior |
|---|---|
| [UDISE+](https://kys.udiseplus.gov.in/) | Official year/state/district catalogs are discovered dynamically. Advanced Search requires state/district/year and CAPTCHA. The official direct PIN Code search exists; it is a current national lookup, so historical-year coverage cannot be inferred from it. |
| [CBSE SARAS](https://saras.cbse.gov.in/SARAS/AffiliatedList/ListOfSchdirReport) | State and dependent district choices come from the live directory. Pagination is followed. There is no dedicated PIN search on the inspected form; PIN filtering compares the detail page's explicit `Pin Code` field after geographic discovery. |

When a PIN filter is requested, missing, invalid or conflicting dedicated PIN values cannot match it. Whole geographic collection preserves those schools and reports their PIN problems separately. Source PIN fields accept six digits separated by whitespace or one ordinary hyphen; labels, decimals and multiple PINs are rejected. Addresses and school names are not used to guess PINs. SARAS accepts `--district "Punjab:Ludhiana"` for disambiguation; that qualification is SARAS-specific.

### Chandigarh, Delhi and Mumbai coverage

`config/location_geography_reference.json` records government postal references and the official source catalogs verified on 2026-11-10. Chandigarh and Delhi use every district exposed by each source. Mumbai is restricted to SARAS `MUMBAI` plus `MUMBAI SUBURBAN`, or UDISE `MUMBAI II` plus `MUMBAI (SUBURBAN)`. These are source names, resolved to live identifiers rather than assumed numeric IDs.

The geographic configurations intentionally leave `pincodes` empty. One complete geographic listing discovers school PINs without excluding schools omitted from an incomplete postal reference. Verified reference PINs and observed school PINs remain separate. The available government postal tables are not certified exhaustive, so reports leave the complete denominator and percentage unknown.

SARAS reports contain `listing_scopes`, source totals, pagination exhaustion evidence, a durable `candidate_manifest`, `record_outcomes` with PIN fields/reasons, and `pending_identifiers`. Listing completion and detail completion are separate. A completed process or a zero-failure smoke test never establishes complete coverage. UDISE saved responses establish only their exported scope; historical summary years remain unchanged and differences from the selected detail year are reported.

Unusable source listing identities are quarantined with their original public row fields. Valid candidates can continue, while the invalid row prevents a verified school-coverage claim. Reported source rows, numeric school identifiers and pending detail tasks remain separate counts. UDISE PIN evidence also retains its academic year: a current search-summary PIN cannot satisfy an older annual PIN filter without matching year context.

The read-only `school_scraping.coverage.generate_location_reports()` helper combines explicit historical audits and isolated current runs into `reports/location_coverage/*_coverage.csv` and `summary.json`. `_MISSING`, `_INVALID` and `_UNKNOWN` rows retain unresolved counts. Historical inventory, current source listings, current details and newly discovered identifiers are reported separately. `COMPLETE_VERIFIED` additionally requires an exhaustive validated postal denominator.

CSV source-check timestamps come from recorded source observations, separately from report generation time. Saved official exports retain their supplied-list provenance and do not become current live school totals merely because their details were checked again.

The current validation results, limitations and exact isolated resume commands are in `reports/location_coverage/full_validation_report.md`. Review those results before enabling locations, scheduling or database writes.

Verified nonmatching PIN details are cached privately for their detail refresh interval. Repeated geographic searches can therefore filter those IDs without another detail request. This cache is not imported as a matching school, and altered cache content is rejected.

UDISE `--manual-captcha 300` opens a visible browser and waits for the user to submit each matching official search and CAPTCHA. It observes authorized responses without solving or replaying CAPTCHA tokens. Alternatively, repeat `--school-list FILE` with complete saved official responses. Pagination metadata that reports missing rows is rejected; an export establishes only its exported coverage.

CAPTCHA/access restrictions pause the affected task. No automatic attempt solves or bypasses them. Restore authorized access before using `--retry-failed`; add the appropriate manual access option if needed. SARAS supports a private authorized `--storage-state FILE`. Headless collection cannot complete a manual challenge. API headers can be provided through UDISE `--headers-file` or `UDISE_HEADERS_JSON`; secrets are never placed in operational logs.

### Incremental collection

UDISE identities are `(UDISE code, academic year)`. SARAS identities use the affiliation number across all geographic folders. Existing source IDs and checkpoints prevent duplicate files across overlapping searches. Names are not duplicate keys, and cross-source matching still requires review.

A listing check is not evidence that details are unchanged. These sources do not currently supply a reliable detail-change version to this project, so known detail records are periodically fetched again after `refresh_days` (default 30). A detail content hash detects actual changes after that fetch. Listing checks and database imports do not reset detail freshness. Old raw files without checkpoints use their file modification time until a successful detail check establishes a checkpoint.

Complete location checks wait `check_interval_hours` (default 24). New schools are collected when discovered. Eligible failed tasks resume from their saved listing input, avoiding discovery requests when possible. A recovery-only run does not claim a complete new geographic check. `--refresh` requests an intentional detail recheck, and `--refresh-days 0` makes every eligible known detail due.

Changed raw files are backed up and verified byte-for-byte before atomic replacement. Unchanged details do not rewrite raw files. Database archive history retains immutable raw/canonical/snapshot evidence. Older UDISE years update their own snapshots without replacing newer master data. A school found under a new geographic alias reuses its existing source/entity identity. Ambiguous pre-existing identities are reported for manual review.

Existing `data/<Location>/<year>/...` and `data/CBSE_SARAS/<location>/schools/...` paths and field names are retained. Historical `_all_schools.json`/`_summary.json` files are not rewritten; use new run reports for new collection results. Derived standardized/canonical views retain per-location paths, so intentionally running the batch mappers for another year regenerates that derived view while retaining raw yearly snapshots.

### PostgreSQL memory and automatic saving

The new versioned migration is `database/migrations/001_scraping_memory.sql`. It adds tracking tables in the existing `school_scraping` schema:

| Table | Purpose |
|---|---|
| `scrape_runs` | Start/end/status, scope and summary counts in structured payloads |
| `scrape_tasks` | Source IDs, saved task input, attempts, status and retry times |
| `scrape_errors` | Sanitized errors/tracebacks, retry counts and resolution state |
| `scrape_checkpoints` | Detail checks, successful imports, hashes and next checks |
| `scrape_events` | Stable event IDs and transactional replay deduplication |

Timestamps use `TIMESTAMPTZ`/UTC. Foreign keys, scope/source indexes and primary keys support history and replay. Memory can be bootstrapped from PostgreSQL if the local journal is unavailable. The existing archive, source, entity, yearly snapshot and match-review tables remain the school data model.

**Production migration has not been applied by this task.** Review the SQL and choose the intended database/role yourself before enabling database writes. Existing `database/schema.sql` and `database/schema_school_tables.sql` must already be present. A writer role needs the required existing archive/school-table/sequence permissions; the API's read-only role may not have them.

```powershell
# Manual migration step: replace the database name and review the connection first.
psql --dbname YOUR_REVIEWED_DATABASE -v ON_ERROR_STOP=1 --file database/migrations/001_scraping_memory.sql

# After migration and writer-role review, enable the automatic pipeline explicitly.
python saras_dynamic_scraper.py --state Punjab --limit 2 --database
python udise_dynamic_scraper.py --state Punjab --school-list .\exports\punjab.json --limit 2 --database
```

Alternatively set `database.enabled` to true in the shared config. Runtime connection settings reuse the existing private `.env` `DB_*` settings with a bounded connection timeout. Importing modules or running dry-run/help does not connect. Schema changes are never applied automatically by the scraper.

Each valid saved record goes through existing standardization, canonical mapping and validation before its transactional database import. Archive/source/snapshot/match-candidate writes and successful-import checkpoint advancement commit together. Failure rolls back those database changes while preserving the raw file and immutable retry input. Approved/rejected matches and reviewed/shared entities are preserved. FastAPI continues to query the existing school model.

### Outages, replay and failed-task recovery

Recovery state is stored under `.scraping_state/` by default (or the isolated data directory when `--data-dir` is supplied). Keep and back up this directory. It contains an append-only `events.jsonl` journal, commit acknowledgements, retry metadata and immutable import payload snapshots. It is ignored by Git, not disposable temporary data.

Events are flushed and fsynced before database work. PostgreSQL event IDs prevent duplicate replay if a process dies after commit but before local acknowledgement. Acknowledgements advance only after commit. Truncated/corrupt journal data is preserved and flagged; subsequent valid events remain recoverable. A database outage keeps records on disk and queues logs/imports without claiming they were imported. Bounded reconnect backoff prevents a tight failure loop.

```powershell
# Resume only failed source tasks in an explicit scope.
python saras_dynamic_scraper.py --state Punjab --retry-failed
python udise_dynamic_scraper.py --state Haryana --retry-failed --manual-captcha 300

# Retry local database operations without opening a source website/browser.
python saras_dynamic_scraper.py --sync-pending --database
python udise_dynamic_scraper.py --sync-pending --database
```

`--retry-failed` is an explicit retry and can override the automatic delay/attempt limit after the underlying problem is fixed. Automatic retries use exponential delay and a maximum attempt count. Pending database imports do not trigger another detail fetch just to repair an import. Successful retries resolve the corresponding error history. Local events remain queued while database integration is disabled and can be synchronized after it is enabled.

Explicit synchronization drains up to 100 batches of 1,000 events per invocation and reports any remainder. Historical input rejected by validation remains queued for review/retry; corrected content for that school can still progress. Operational errors and rejected raw input are retained rather than silently discarded.

### One daily scheduler

The scheduler reads enabled configured locations and defaults to `02:00` in `Asia/Kolkata`. It waits until the scheduled time; `--once` explicitly runs a cycle immediately. Source/scheduler locks prevent overlapping jobs using the same data root. One failed source does not prevent the other source from running. Each cycle records a summary and appends structured events to the existing scheduler log.

```powershell
python daily_scheduler.py --config config/states_pincodes.json --dry-run
python daily_scheduler.py --source saras --state Punjab --time 02:00 --limit 2
python daily_scheduler.py --source both --config config/states_pincodes.json
```

Pending database events are replayed before due checks when enabled. Locations with future checkpoints are skipped; eligible failed tasks can make a scope due sooner. CAPTCHA/manual tasks wait for an explicit retry. Press Ctrl+C for graceful shutdown. Scheduling cannot remove a source's manual access requirement.

### Inspect runs and unresolved errors

```sql
SELECT run_id, source, status, started_at, finished_at,
       payload->'counts' AS counts, payload->>'duration_seconds' AS duration_seconds
FROM school_scraping.scrape_runs ORDER BY started_at DESC LIMIT 20;

SELECT occurred_at, source, error_category, error_message, retry_count,
       resolution_status, resolved_at, resolution_notes
FROM school_scraping.scrape_errors
WHERE resolution_status = 'open' ORDER BY occurred_at DESC LIMIT 50;

SELECT task_id, source, school_identifier, academic_year, status, attempts,
       next_retry_at, payload->>'state' AS state, payload->>'district' AS district
FROM school_scraping.scrape_tasks
WHERE status IN ('failed', 'manual_intervention', 'pending_import')
ORDER BY next_retry_at NULLS FIRST;

SELECT source, academic_year, school_identifier, checkpoint_status, imported,
       last_detail_check, last_successful_save, next_check_at
FROM school_scraping.scrape_checkpoints ORDER BY last_event_at DESC LIMIT 50;
```

### Tests and cleanup

```powershell
python -m unittest discover -s tests -v
```

The default suite runs offline and skips tests requiring a separately provisioned PostgreSQL instance. Database tests opt in using `SCHOOL_SCRAPING_TEST_DSN` and refuse ordinary database connections: the test code requires the isolated loopback port `55439`. `tests/support/postgres_cluster.py` is an import-only test helper for a cluster under `.refactor_validation/memory_pg17`; it is not another scraper/scheduler entry point.

See `docs/cleanup_inventory.md` for the actual moves, backed-up duplicate-file removals, retained directories and command migration. Recoverable originals are in `archive/cleanup_backups/` with SHA-256 manifests. Existing raw data, reports, logs, migrations and local edits are retained. Live nationwide scraping, production database changes and a continuously running scheduler require a separate deliberate run.

## 2. Project Objectives

- Collect school information from UDISE+ and CBSE SARAS.
- Preserve original scraped JSON data.
- Store and manage school records in PostgreSQL.
- Maintain source-specific school information.
- Organize yearly UDISE school snapshots.
- Identify possible duplicate schools across sources.
- Review and approve school matches manually.
- Provide searchable school records through REST API endpoints.

## 3. Technology Stack

| Technology | Purpose |
|---|---|
| Python | Scraping and data processing |
| PostgreSQL | Database storage |
| SQL | Database schema and queries |
| FastAPI | REST API development |
| Uvicorn | API server |
| Psycopg | PostgreSQL connectivity |
| python-dotenv | Environment configuration |
| Git and GitHub | Version control |

## 4. Data Sources

### UDISE+
Provides school information, including UDISE codes, school names, location details, and available yearly statistics.

### CBSE SARAS
Provides CBSE-affiliated school information, including affiliation numbers, school names, addresses, and contact details where available.

The two sources have different identifiers and data structures. The project maintains their source-specific records while creating a unified master-school view.

## 5. Database Architecture

**Database:** `school_scraping_db`

**Schema:** `school_scraping`

Main database objects:

| Table / View | Purpose |
|---|---|
| `json_file_archive` | Stores archived original JSON content and file metadata |
| `school_entities` | Maintains master school entities |
| `school_source_records` | Links UDISE and SARAS source records |
| `school_yearly_snapshots` | Stores yearly UDISE school data |
| `school_match_candidates` | Stores possible school matches and review decisions |
| `school_entity_merge_history` | Maintains an audit history of approved merges |
| `school_master_search` | Provides a unified search view for the API |

## 6. Data Processing Workflow

1. Collect school information from UDISE+ and CBSE SARAS.
2. Save the original scraped JSON files.
3. Import JSON files into the PostgreSQL archive.
4. Load canonical school records into normalized database tables.
5. Create yearly UDISE snapshots.
6. Identify possible matches using normalized school names and region.
7. Review candidate matches using available evidence.
8. Approve or reject matches through manual review.
9. Merge approved source records into common master entities.
10. Maintain merge history for audit purposes.
11. Expose the unified school data through FastAPI.

**Important:** The project does not automatically merge candidate schools. Human review is required before merging.

## 7. Current Database Statistics

| Metric | Count |
|---|---:|
| Archived JSON files | 17,226 |
| Canonical source records | 5,779 |
| Active master school entities | 5,770 |
| Merged entities | 9 |
| UDISE yearly snapshots | 3,260 |
| Match candidates | 12 |
| Approved matches | 9 |
| Candidates requiring more evidence | 3 |

These figures represent the current database state at the time of documentation.

## 8. FastAPI Endpoints

The project provides the following endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | API welcome message |
| GET | `/health` | Checks database connectivity |
| GET | `/schools` | Searches and lists schools |
| GET | `/schools/{entity_id}` | Retrieves an individual school |

### Search API Examples

List schools:

`http://127.0.0.1:8000/schools?page=1&page_size=5`

Search by school name:

`http://127.0.0.1:8000/schools?q=Oxford`

Find schools available in both sources:

`http://127.0.0.1:8000/schools?has_udise=true&has_saras=true`

Retrieve an individual school:

`http://127.0.0.1:8000/schools/2555`

API documentation:

`http://127.0.0.1:8000/docs`

## 9. Local Setup

### Prerequisites

- Python 3
- PostgreSQL
- Git
- Required Python packages

### Clone the repository

```bash
git clone https://github.com/raju1207/Web-Scrapping.git
cd Web-Scrapping
```

### Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Install dependencies

```powershell
pip install -r requirements.txt
```

### Configure environment variables

Create a local `.env` file in the project root.

Configure these variables with your own PostgreSQL credentials:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=school_scraping_db
DB_USER=school_api_user
DB_PASSWORD=YOUR_LOCAL_PASSWORD
```

Never upload the `.env` file or database credentials to GitHub.

### Start the API

```powershell
python -m uvicorn database.api.main:app --reload
```

Open:

`http://127.0.0.1:8000/docs`

## 10. Data Integrity and Safety

- Original scraped JSON files are preserved.
- Source-specific UDISE and SARAS records are maintained.
- Missing values remain NULL rather than being replaced with zero.
- Candidate school matches require manual review.
- Approved merges are recorded in merge history.
- The API uses parameterized SQL queries.
- The API database connection is configured for read-only transactions.

## 11. Project Repository

GitHub:

https://github.com/raju1207/Web-Scrapping

## 12. Future Improvements

- Improve school matching using additional evidence.
- Add more validation and data-quality checks.
- Extend API filtering and search capabilities.
- Add API authentication and access control.
- Develop a frontend for school search and data visualization.
- Improve automated data refresh and monitoring.

---

**Project:** School Data Scraping and Unified Database  
**Data Sources:** UDISE+ and CBSE SARAS  
**Database:** PostgreSQL  
**API Framework:** FastAPI  
**Repository:** https://github.com/raju1207/Web-Scrapping
