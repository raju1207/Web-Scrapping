# Dynamic scraper refactor validation

Date: 2026-11-09 (Asia/Kolkata). Workspace: `E:\GITHUB\PROJECTS\Web-Scrapping`.

**A. Files created or updated**

All paths below are relative to the exact workspace path above.

Created:

- `udise_dynamic_scraper.py`
- `saras_dynamic_scraper.py`
- `school_scraping/__init__.py`
- `school_scraping/common.py`
- `school_scraping/datasets.py`
- `tests/test_common.py`
- `tests/test_pipeline_dynamic.py`
- `tests/test_saras_dynamic.py`
- `tests/test_scheduler.py`
- `tests/test_udise_dynamic.py`
- `archive/legacy_scrapers/README.md`
- `archive/legacy_scrapers/manifest.json`
- `archive/legacy_scrapers/daily_scheduler.py.before_refactor.txt`
- `reports/dynamic_refactor_validation.md`
- `archive/legacy_scrapers/scraper.py.disabled`
- `archive/legacy_scrapers/saras_scraper.py.disabled`
- `archive/legacy_scrapers/saras_scraper_mumbai.py.disabled`
- `archive/legacy_scrapers/udise_daily_refresh.py.disabled`
- `archive/legacy_scrapers/udise_batch_refresh.py.disabled`
- `archive/legacy_scrapers/test_udise.py.disabled`

Updated:

- `.gitignore`
- `README.md`
- `daily_scheduler.py`
- `standardize_school_data.py`
- `canonical_v4_mapper.py`
- `saras_canonical_v4_mapper.py`
- `validate_canonical_v4.py`
- `validate_saras_canonical_v4.py`
- `database/import_json_to_postgres.py`

**B. Legacy scripts**

Six root workflows were moved into the disabled archive after dependency inspection. All six SHA-256 hashes were verified after the move. These are preserved working-tree contents, including existing local edits. Git shows root deletions and an untracked archive until the user stages the moves.

- `scraper.py` -> `archive/legacy_scrapers/scraper.py.disabled` (SHA-256 `DF6FE169406EF411A4BBF1AE05319890ACD6DFE956DC8C8E5C1B80F720866646`)
- `saras_scraper.py` -> `archive/legacy_scrapers/saras_scraper.py.disabled` (SHA-256 `3D169B4E595FC414EE15D4D6F4990A5645689873D0D77C3637CE0525FB48DC74`)
- `saras_scraper_mumbai.py` -> `archive/legacy_scrapers/saras_scraper_mumbai.py.disabled` (SHA-256 `49CB6E0DE8EF9F738C559EDF13F56E3FA4821E7304EBB9F68A551BEF93D634D5`)
- `udise_daily_refresh.py` -> `archive/legacy_scrapers/udise_daily_refresh.py.disabled` (SHA-256 `D7CD6C86D75B24D3425405D97D1D8835209718F03518D4427BF4469DCAB3CFA8`)
- `udise_batch_refresh.py` -> `archive/legacy_scrapers/udise_batch_refresh.py.disabled` (SHA-256 `67579BCE1DF9E3AE0FF824E1F67D363F5C3CC2760B53BCFCD11BE2E9EF0D16F4`)
- `test_udise.py` -> `archive/legacy_scrapers/test_udise.py.disabled` (SHA-256 `5FAB4C8A35545986B5E8011092A491A0AC28B6968E2C0C6687CD29D5B31CD1EC`)

The original modified scheduler is preserved at `archive/legacy_scrapers/daily_scheduler.py.before_refactor.txt`. No active code imports or launches the archived scripts. Unrelated audit/database/API utilities remain.

**C. Architecture**

`daily_scheduler.py -> udise_dynamic_scraper.py / saras_dynamic_scraper.py -> compatible raw JSON -> existing standardization/canonical v4 -> PostgreSQL JSON archive and school tables -> reviewed cross-source matching -> FastAPI`.

The helper package provides source-specific global identifier indexing, atomic saves, verified refresh backups, versioned reports, OS-held process locks, and dynamic dataset discovery. UDISE identity includes the selected academic year; SARAS identity uses the affiliation number across geographic folders. Cross-source matching and the database/API implementations were preserved.

**D. Testing**

Passed:

- 61 offline unit/regression tests: access restrictions, bounded retries, geographic/PIN scope, pagination, duplicate detection, exact original-byte backups, interrupted/resumable storage, legacy sample mapping, year propagation, scheduler commands/timezones/DST/locks/shutdown.
- Python syntax/bytecode checks: 33 active Python source and test files.
- Import checks: 11 scraper/scheduler/pipeline/database/API modules; no database connection or scrape launched.
- All three CLI help checks and six selected/all-state offline dry runs.
- Six legacy archive SHA-256 checks and active legacy-reference scan.
- Whitespace checks for all tracked code/documentation changed by this refactor.
- Controlled SARAS live collection: exactly one Punjab school and one Haryana school, zero failures in each successful run.
- Both live raw samples passed the existing standardizer and SARAS canonical mapper, preserving affiliation identifiers and dedicated pincode values.

Live evidence:

- Punjab: `.refactor_validation/live_saras_punjab/reports/saras/run_20261109_063304_185243_07616f25.json`; counts `{"discovered": 1, "duplicate": 0, "failed": 0, "filtered": 0, "missing_pincode": 0, "skipped": 0, "success": 1, "unchanged": 0, "updated": 0}`.
- Haryana: `.refactor_validation/live_saras_haryana/reports/saras/run_20261109_063408_114370_b6dbf343.json`; counts `{"discovered": 1, "duplicate": 0, "failed": 0, "filtered": 0, "missing_pincode": 0, "skipped": 0, "success": 1, "unchanged": 0, "updated": 0}`.
- UDISE: `.refactor_validation/udise/reports/udise/run_20261109_063253_047555_1b2de880.json`; live year/state/district discovery verified Punjab and Haryana, then stopped before school collection for manual CAPTCHA. Pending discovery tasks were recorded.

Failed/unresolved checks:

- No final offline code checks failed. Initial network-sandbox live attempts were denied; their reports were retained and authorized escalated retries completed.
- A whole-working-tree `git diff --check` still flags whitespace already present in the user-modified `logs/daily_scheduler.log`. That original log was preserved; checks restricted to refactor changes pass.

Skipped / not tested:

- UDISE live school collection and manual browser interaction: require manual CAPTCHA completion; no bypass attempted.
- Nationwide scraping and full live pagination/state coverage: intentionally not run. Pagination logic has offline coverage; live tests stop at one school.
- Full running scheduler and production database integration/writes: intentionally not run.
- Full mapping/validation mains against workspace data: not run because they regenerate derived files and reports; pure mappers/validators were checked on existing and isolated samples.
- Archived `test_udise.py` was a live diagnostic, not an automated offline suite.

**E. Commands**

Run using the existing virtual environment, or activate it first:

```powershell
python udise_dynamic_scraper.py --state Punjab --district Ludhiana --limit 2 --manual-captcha 300
python udise_dynamic_scraper.py --state Haryana --pincode 134001 --limit 2 --manual-captcha 300
python udise_dynamic_scraper.py --state Punjab --school-list .\exports\punjab.json --limit 2
python saras_dynamic_scraper.py --state Punjab --limit 2
python saras_dynamic_scraper.py --state Haryana --pincode 134001 --limit 2
python daily_scheduler.py --source both --state Punjab --udise-school-list .\exports\punjab.json --time 02:00 --timezone Asia/Kolkata --limit 2
python daily_scheduler.py --source both --state Punjab --state Haryana --dry-run
python udise_dynamic_scraper.py --all-states --dry-run
python saras_dynamic_scraper.py --all-states --dry-run
python -m unittest discover -s tests -v
```

Use repeated `--state`, `--district`, or `--pincode` for multiple values. `--refresh` intentionally checks existing records for changes and verifies a backup before replacement. A bare scheduler command requires explicit scope before live startup. `--once` starts a cycle immediately and was not executed during this refactor.

**F. Remaining limitations**

- UDISE Advanced Search and direct PIN Code search require the official manual CAPTCHA process. Without `--manual-captcha SECONDS` or complete authorized `--school-list` responses, collection stops with a resumable manual-intervention report. PIN discovery is current/national; it does not prove historical-year completeness.
- Saved UDISE lists establish only their exported scope. Year/state catalogs are verified live. Unknown state/district selections and explicit year-ID mismatches are rejected.
- SARAS has no dedicated PIN search on the inspected directory; filtering uses the explicit detail `Pin Code` field after geographic discovery. Missing/conflicting PINs are excluded.
- Browser collection requires Playwright Chromium. Authorized API headers/session files may be configured; no credential is embedded or logged.
- Existing aggregate `_all_schools.json` and `_summary.json` files remain historical. New versioned reports are authoritative for new collection runs.
- Derived canonical/standardized paths retain their per-location layout. Intentionally processing another UDISE year regenerates that derived view; original raw yearly snapshots are retained.
- Historical validation counts remain historical baselines for old datasets; new states use discovered raw-file counts.

Sources: [UDISE+ official site](https://kys.udiseplus.gov.in/) and [CBSE SARAS directory](https://saras.cbse.gov.in/SARAS/AffiliatedList/ListOfSchdirReport).

**G. Final Git status**

Branch remains `refactor/dynamic-state-pincode`. Nothing was staged, committed, pushed, reset, or cleaned. Existing modified/untracked data, backups, reports and the scheduler log are retained. Live samples and detailed test logs are isolated under ignored `.refactor_validation/`.

```text
 M .gitignore
 M README.md
 M canonical_v4_mapper.py
 M daily_scheduler.py
 M data/CBSE_SARAS/delhi/_all_schools.json
 M data/CBSE_SARAS/delhi/_summary.json
 M data/CBSE_SARAS/mumbai/_all_schools.json
 M data/CBSE_SARAS/mumbai/_summary.json
 M data/Mumbai/2025-26/_summary.json
 M database/import_json_to_postgres.py
 M logs/daily_scheduler.log
 M saras_canonical_v4_mapper.py
 D saras_scraper.py
 D saras_scraper_mumbai.py
 D scraper.py
 M standardize_school_data.py
 D test_udise.py
 D udise_batch_refresh.py
 D udise_daily_refresh.py
 M validate_canonical_v4.py
 M validate_saras_canonical_v4.py
?? archive/
?? data/CBSE_SARAS/delhi/schools/B.R._INTERNATIONAL_SCHOOL_2730851.json
?? data/CBSE_SARAS/delhi/schools/GOVT._BOYS_SECONDARY_SCHOOL_NEB_SARAI_2720103.json
?? data/CBSE_SARAS/delhi/schools/K.M._INTERNATIONAL_SCHOOL_2730944.json
?? data/CBSE_SARAS/delhi/schools/SKV(UPDESH_KAUR),_DARIYAPUR_KALAN_2720102.json
?? data/CBSE_SARAS/mumbai/schools/MUMBAI_PUBLIC_SCHOOL_ASHISH_TALAO_(CBSE)_1120144.json
?? data/refresh_backups/UDISE/Andhra_Edn._Society_Sr._Sec._School,_1_DDU_Marg_(Rouse_Avenue)_New_Delhi_07060208102_20261023_215718_747777.json
?? data/refresh_backups/UDISE/Andhra_Education_Society_Sr._Sec._School,_Prasad_Nagar,_New_Delhi_07060209108_20261023_215724_576894.json
?? data/refresh_backups/UDISE/Anglo_Arabic_Model_School,_Ajmeri_Gate,Delhi_07060308202_20261023_215727_729979.json
?? data/refresh_backups/UDISE/Arya_Girls_Sr._Sec._School,_Karol_Bagh,_Reghar_Pura,_New_Delhi_07060209204_20261023_215735_333759.json
?? reports/dynamic_refactor_validation.md
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
