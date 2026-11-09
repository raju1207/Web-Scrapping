# Incremental collection, PostgreSQL memory and recovery validation

Date: 2026-11-09 (Asia/Kolkata). Workspace: `E:\GITHUB\PROJECTS\Web-Scrapping`.

**1. Exact files and preservation**

Paths below are relative to the workspace above. This inventory describes the incremental phase; the earlier dynamic-refactor report remains in reports/.

Created:

- `config/states_pincodes.json`
- `school_scraping/config.py`
- `school_scraping/incremental.py`
- `school_scraping/memory.py`
- `database/scrape_pipeline.py`
- `database/migrations/001_scraping_memory.sql`
- `tests/test_config.py`
- `tests/test_incremental.py`
- `tests/test_memory.py`
- `tests/test_scrape_pipeline.py`
- `tests/support/postgres_cluster.py`
- `docs/cleanup_inventory.md`
- `reports/incremental_scraping_validation.md`

Modified relative to the preserved source snapshot at the start of this phase:

- `.gitignore`
- `README.md`
- `daily_scheduler.py`
- `database/import_json_to_postgres.py`
- `database/load_school_tables.py`
- `saras_dynamic_scraper.py`
- `tests/test_saras_dynamic.py`
- `tests/test_scheduler.py`
- `tests/test_udise_dynamic.py`
- `udise_dynamic_scraper.py`

New archives or permanent deletions in this phase: none. The six previously disabled legacy scripts remain byte-for-byte unchanged; SHA-256 verification passed for all six. See `archive/legacy_scrapers/manifest.json` and `docs/cleanup_inventory.md`.

All 23 pre-existing modified/untracked raw-data, report and log files captured before these edits retain their original SHA-256 hashes. Prior code copies are retained under `.refactor_validation/incremental_before/code/`. Source samples and database tests used isolated validation directories.

Four ZIP deletions appeared in the working tree during the session: `data.zip`, `final_data-zipped.zip`, `school.zip`, `School_Data_Final_CrossCheck.zip`. No cleanup command in this implementation removed those files; their observed deletion state was left untouched. They are not proposed deletions.

**2. Active structure and flow**

```text
udise_dynamic_scraper.py
saras_dynamic_scraper.py
daily_scheduler.py
config/states_pincodes.json
school_scraping/
  common.py, config.py, datasets.py, incremental.py, memory.py
database/
  scrape_pipeline.py, import_json_to_postgres.py, load_school_tables.py
  migrations/001_scraping_memory.sql
  api/
tests/
docs/cleanup_inventory.md
```

`configuration -> checkpoint/due decision -> official school-ID discovery -> new/due details -> preserved raw JSON -> existing standardizer/canonical mapper -> existing validation -> existing PostgreSQL archive/source/entity/snapshot loaders -> reviewed match candidates -> existing FastAPI model`.

There are exactly three active collection/scheduling entry points. Helpers, batch processing utilities, database tools and tests remain separate. No active Python import or subprocess launch references an archived scraper.

**3. Migration and database integration**

`database/migrations/001_scraping_memory.sql` adds `scrape_runs`, `scrape_tasks`, `scrape_errors`, `scrape_checkpoints`, and `scrape_events` within the existing schema. It has explicit transactions, primary/foreign keys, indexes, timezone-aware timestamps and idempotent creation/column additions. Scope, per-location fields and summary metrics are retained in structured JSONB payloads, alongside indexed status/identity/retry columns.

The migration was applied repeatedly only to an isolated PostgreSQL 17 test cluster at loopback port 55439. Production migration was not applied and production records were not changed. The test server has been stopped; its data and logs remain under `.refactor_validation/memory_pg17/`.

Direct imports reuse the existing archive upsert, canonical parsing, school-source/entity loader, yearly-snapshot loader, and match-candidate workflow. An advisory source-ID lock plus global source lookup prevents geographic aliases from creating duplicate identities. Ambiguous pre-existing identities require manual review. Reviewed/shared entities and approved/rejected match decisions are preserved. Original/archive history is retained before replacement; older UDISE years cannot overwrite newer master records.

School writes and successful-import checkpoint advancement share one PostgreSQL transaction. A failure rolls back the database work while retaining the raw record and immutable pending input. The `imported` flag is false for current content until the transaction succeeds.

Database access is disabled by default. Review/apply the migration to the intended database, provide an authorized writer role using the private existing DB_* configuration, then use `--database` or enable it in config. No scraper performs automatic DDL.

**4. Incremental behavior**

- Shared validated location config preserves state/district/PIN pairings and supports enabled/disabled locations, selected scopes, and explicit all-states mode. The shipped examples and database writes are disabled.
- Identity uses UDISE code plus academic year, or SARAS affiliation number across geography. Content hashes ignore search-only metadata.
- New IDs collect details. Existing valid detail records wait their configured refresh interval (default 30 days); a listing check or import never extends detail freshness. Legacy records without checkpoints bootstrap from raw-file modification time.
- No reliable source detail-version indicator is currently wired for these websites; periodic detail checks are used instead of claiming unchanged listings imply unchanged details.
- Complete geographic checks wait the configured interval (default 24 hours). Limited/interrupted/recovery-only runs do not claim nationwide or full-scope coverage.
- Eligible failed tasks resume saved listing inputs when possible. Automatic source retries respect exponential delay and maximum attempts; CAPTCHA/manual access remains paused until an explicit retry.
- Verified nonmatching PIN detail responses are privately cached until their detail refresh interval, avoiding repeat detail requests for known nonmatches. Altered cache contents are rejected. Such cache records do not claim a matching school import.
- Changed raw files receive verified byte-preserving backups; unchanged raw files are not rewritten.

**5. Memory, outages and recovery**

Events are appended to `events.jsonl` and fsynced before database work. Stable event IDs and transactional `scrape_events` insertion make replay idempotent even after commit-before-ack crashes. Local acknowledgements are persisted only after commit. The journal is retained, including unreadable/truncated data that is explicitly flagged; later valid events remain recoverable.

Pending imports reference immutable byte-verified snapshots rather than relying on a later version of the latest raw file. A crash after raw checkpointing but before import enqueue is recoverable. Database failure retains pending writes and sanitized errors with bounded reconnect backoff. Pending import repair does not fetch source details again. Corrected school content can progress while rejected historical input remains retained for review/retry. Successful imports resolve prior detail/import errors, including during replay.

PostgreSQL memory can bootstrap a fresh local journal. The local projection reads appended journal events incrementally; lookup caches are updated after old imports finally commit. Configuration, helpers and dry runs have no import-time scrape/database side effects.

Explicit `--sync-pending --database` drains up to 100 batches of 1,000 events, reports any remainder, and opens no source browser. Recovery state and snapshots are Git-ignored but must be retained/backed up.

**6. Executed testing**

Passed:

- Final complete suite: **137 tests passed, zero failures, zero skipped**, including **25 real isolated PostgreSQL tests**. Evidence: `.refactor_validation/incremental_results/postgres_tests_final.txt`.
- Offline/default suite passed; database-only cases are deliberately skipped without the explicit isolated test DSN. The final opt-in run above executed them all.
- Python syntax/bytecode checks for 42 active source/test files; 10 import checks.
- Three CLI help checks and 12 dry-run/config/maintenance checks.
- Configuration validation/selection, new and changed ID handling, source/year identity, duplicate prevention, timed checks, filtered-PIN caching, partial scope handling and bounded task recovery.
- Actual PostgreSQL transaction rollback, raw-history preservation, alias identity reuse, year ordering, reviewed match/entity preservation, sanitized error logging/resolution, checkpoint correctness, lost acknowledgement replay, fresh-local-state bootstrap, and corrected-content progress.
- 23 protected existing data/report/log hashes and six legacy archive hashes unchanged.
- Refactor-owned tracked code/documentation whitespace checks and active legacy-reference scan passed.

Controlled live evidence:

- SARAS Punjab: exactly one record, affiliation `1600001`, PIN `144103`. It was collected with DB disabled into `.refactor_validation/incremental_live/punjab/`, then its queued writes were replayed into isolated PG17: 8 events synchronized, no failures or pending events. One source row remained after replay, detail freshness correctly skipped another fetch, and raw SHA-256 was unchanged.
- SARAS Haryana: exactly one record, affiliation `500001`, PIN `133001`. It was collected with DB disabled into `.refactor_validation/incremental_live/haryana/`, then its queued writes were replayed into isolated PG17: 8 events synchronized, no failures or pending events. One source row remained after replay, detail freshness correctly skipped another fetch, and raw SHA-256 was unchanged.

The live collection CLI deliberately used `--no-database`; automatic per-record importing with an injected isolated connection was separately exercised by real PostgreSQL runtime tests. No test used production credentials or production tables.

Final failed checks: none. Intermediate connection/server-interruption failures and cached-projection regression failures were corrected and rerun; they are not reported as passing before execution.

Not executed / intentionally excluded:

- Live UDISE school collection or manual CAPTCHA interaction during this incremental phase. UDISE pipeline/request/recovery behavior was tested offline and against isolated PostgreSQL using samples.
- Nationwide scraping, full live pagination/state coverage, or a continuously running scheduler.
- Production migration, production imports/updates, production API/database integration tests.
- Full batch mapping/validation mains against workspace data, because those regenerate existing derived files/reports. Pure functions and existing schema compatibility were tested instead.

**7. Commands**

```powershell
# Safe previews
python udise_dynamic_scraper.py --state Punjab --state Haryana --dry-run
python saras_dynamic_scraper.py --state Haryana --pincode 134001 --dry-run
python daily_scheduler.py --config config/states_pincodes.json --dry-run

# Small source runs; production DB disabled unless explicitly enabled
python udise_dynamic_scraper.py --state Punjab --district Ludhiana --limit 2 --manual-captcha 300
python saras_dynamic_scraper.py --state Punjab --limit 2

# Enable a named location in config before using it
python daily_scheduler.py --source both --config config/states_pincodes.json

# Explicit failed source recovery, or pending DB operations without source requests
python saras_dynamic_scraper.py --state Punjab --retry-failed
python saras_dynamic_scraper.py --sync-pending --database

# Manual reviewed migration step, never executed on production by this task
psql --dbname YOUR_REVIEWED_DATABASE -v ON_ERROR_STOP=1 --file database/migrations/001_scraping_memory.sql

python -m unittest discover -s tests -v
```

**8. Remaining limitations**

UDISE discovery requires official manual CAPTCHA completion or complete authorized saved listings. Its PIN discovery is current/national and does not establish historical-year completeness. SARAS PIN filtering uses the dedicated detail field; unknown or conflicting PINs are excluded. Session/authentication access must be supplied through authorized private configuration. There is no CAPTCHA bypass or solver.

Existing historical aggregate JSON reports are retained and may not include newly collected files. Use the new run reports and PostgreSQL memory queries in README. Existing batch canonical/standardized views remain per location; intentionally processing another UDISE year regenerates that view while preserving raw snapshots. No nationwide throughput benchmark or uninterrupted long-running scheduler test was performed.

**9. Final Git status**

Branch remains `refactor/dynamic-state-pincode`. Nothing was staged, committed, pushed, reset or cleaned. The status below includes preserved changes from before this task and observed ZIP deletions in addition to incremental implementation changes.

```text
 M .gitignore
 M README.md
 D School_Data_Final_CrossCheck.zip
 M canonical_v4_mapper.py
 M daily_scheduler.py
 D data.zip
 M data/CBSE_SARAS/delhi/_all_schools.json
 M data/CBSE_SARAS/delhi/_summary.json
 M data/CBSE_SARAS/mumbai/_all_schools.json
 M data/CBSE_SARAS/mumbai/_summary.json
 M data/Mumbai/2025-26/_summary.json
 M database/import_json_to_postgres.py
 M database/load_school_tables.py
 D final_data-zipped.zip
 M logs/daily_scheduler.log
 M saras_canonical_v4_mapper.py
 D saras_scraper.py
 D saras_scraper_mumbai.py
 D school.zip
 D scraper.py
 M standardize_school_data.py
 D test_udise.py
 D udise_batch_refresh.py
 D udise_daily_refresh.py
 M validate_canonical_v4.py
 M validate_saras_canonical_v4.py
?? archive/
?? config/
?? data/CBSE_SARAS/delhi/schools/B.R._INTERNATIONAL_SCHOOL_2730851.json
?? data/CBSE_SARAS/delhi/schools/GOVT._BOYS_SECONDARY_SCHOOL_NEB_SARAI_2720103.json
?? data/CBSE_SARAS/delhi/schools/K.M._INTERNATIONAL_SCHOOL_2730944.json
?? data/CBSE_SARAS/delhi/schools/SKV(UPDESH_KAUR),_DARIYAPUR_KALAN_2720102.json
?? data/CBSE_SARAS/mumbai/schools/MUMBAI_PUBLIC_SCHOOL_ASHISH_TALAO_(CBSE)_1120144.json
?? data/refresh_backups/UDISE/Andhra_Edn._Society_Sr._Sec._School,_1_DDU_Marg_(Rouse_Avenue)_New_Delhi_07060208102_20261023_215718_747777.json
?? data/refresh_backups/UDISE/Andhra_Education_Society_Sr._Sec._School,_Prasad_Nagar,_New_Delhi_07060209108_20261023_215724_576894.json
?? data/refresh_backups/UDISE/Anglo_Arabic_Model_School,_Ajmeri_Gate,Delhi_07060308202_20261023_215727_729979.json
?? data/refresh_backups/UDISE/Arya_Girls_Sr._Sec._School,_Karol_Bagh,_Reghar_Pura,_New_Delhi_07060209204_20261023_215735_333759.json
?? database/migrations/
?? database/scrape_pipeline.py
?? docs/
?? reports/dynamic_refactor_validation.md
?? reports/incremental_scraping_validation.md
?? reports/postgres_json_import_failures.csv
?? reports/udise_refresh/batch_udise_20261023_214130_001255.json
?? reports/udise_refresh/udise_07060208102_20261023_214108_327612.json
?? reports/udise_refresh/udise_07060208307_20261023_214120_345716.json
?? reports/udise_refresh/udise_07060209108_20261023_214110_899778.json
?? reports/udise_refresh/udise_07060209204_20261023_214129_957442.json
?? reports/udise_refresh/udise_07060308202_20261023_214115_000653.json
?? saras_dynamic_scraper.py
?? school_scraping/
?? tests/
?? udise_dynamic_scraper.py
```
