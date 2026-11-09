# Completed project cleanup

Date: 2026-11-09 (Asia/Kolkata). Workspace: `E:\GITHUB\PROJECTS\Web-Scrapping`. Branch: `refactor/dynamic-state-pincode`.

**Result: the root now contains exactly the three requested Python entry points.** Supporting functionality was relocated, duplicate code was actually removed after verified backups, and the working system was tested afterward.

## Root before cleanup

The root had 21 Python files plus two miscellaneous sample files:

```text
audit_chandigarh_list_vs_raw.py
audit_delhi_list_vs_raw.py
audit_mumbai_list_vs_raw.py
build_summary.py
canonical_v4_mapper.py
compare_raw_vs_standardized.py
compare_udise_delhi_lists.py
compare_udise_mumbai_lists.py
compare_udise_school_lists.py
count_current_data.py
daily_scheduler.py
missing_value_classifier.py
raw_data_audit.py
saras_canonical_v4_mapper.py
saras_dynamic_scraper.py
saras_raw_data_audit.py
section_level_audit.py
standardize_school_data.py
udise_dynamic_scraper.py
validate_canonical_v4.py
validate_saras_canonical_v4.py
gallery_data.json
standardized_sample.txt
```

Other existing root items included `.env`, `.gitignore`, README, requirements, data/database/packages/tests, historical archives, the environment, validation artifacts and root Python cache.

## Root after cleanup

```text
.refactor_validation/
.vscode/
archive/
config/
data/
database/
docs/
final_data-zipped/
logs/
reports/
school_scraping/
tests/
venv/
.env
.gitignore
daily_scheduler.py
README.md
requirements.txt
saras_dynamic_scraper.py
udise_dynamic_scraper.py
```

Root Python files:

```text
daily_scheduler.py
saras_dynamic_scraper.py
udise_dynamic_scraper.py
```

The three entry-point files themselves are unchanged from their pre-cleanup working copies. The root `__pycache__/` directory was removed after testing.

## Files physically moved

Tracked files used `git mv`; the previously ignored standardized sample used a verified filesystem move.

| Former path | Current path |
|---|---|
| `standardize_school_data.py` | `school_scraping/standardize_school_data.py` |
| `canonical_v4_mapper.py` | `school_scraping/canonical_v4_mapper.py` |
| `saras_canonical_v4_mapper.py` | `school_scraping/saras_canonical_v4_mapper.py` |
| `validate_canonical_v4.py` | `school_scraping/validate_canonical_v4.py` |
| `validate_saras_canonical_v4.py` | `school_scraping/validate_saras_canonical_v4.py` |
| `build_summary.py` | `school_scraping/maintenance/build_summary.py` |
| `compare_raw_vs_standardized.py` | `school_scraping/maintenance/compare_raw_vs_standardized.py` |
| `count_current_data.py` | `school_scraping/maintenance/count_current_data.py` |
| `missing_value_classifier.py` | `school_scraping/maintenance/missing_value_classifier.py` |
| `raw_data_audit.py` | `school_scraping/maintenance/raw_data_audit.py` |
| `saras_raw_data_audit.py` | `school_scraping/maintenance/saras_raw_data_audit.py` |
| `section_level_audit.py` | `school_scraping/maintenance/section_level_audit.py` |
| `gallery_data.json` | `data/samples/gallery_data.json` |
| `standardized_sample.txt` | `data/samples/standardized_sample.txt` |

The five processing modules remain required by `database/scrape_pipeline.py`; its imports and tests now use `school_scraping`. Seven distinct standalone utilities remain under `school_scraping/maintenance/`. All relocated processing/maintenance modules have safe module `--help`, and project/data paths remain anchored correctly from another working directory.

## Files actually removed

The following six verified duplicate root code files were removed with `git rm`, after their exact working contents were backed up:

- `audit_chandigarh_list_vs_raw.py` (recoverable at `archive/cleanup_backups/20261109_171652/root/audit_chandigarh_list_vs_raw.py`)
- `audit_delhi_list_vs_raw.py` (recoverable at `archive/cleanup_backups/20261109_171652/root/audit_delhi_list_vs_raw.py`)
- `audit_mumbai_list_vs_raw.py` (recoverable at `archive/cleanup_backups/20261109_171652/root/audit_mumbai_list_vs_raw.py`)
- `compare_udise_delhi_lists.py` (recoverable at `archive/cleanup_backups/20261109_171652/root/compare_udise_delhi_lists.py`)
- `compare_udise_mumbai_lists.py` (recoverable at `archive/cleanup_backups/20261109_171652/root/compare_udise_mumbai_lists.py`)
- `compare_udise_school_lists.py` (recoverable at `archive/cleanup_backups/20261109_171652/root/compare_udise_school_lists.py`)

They were replaced by:

- `school_scraping/maintenance/audit_list_vs_raw.py` ? one configurable list-versus-raw audit.
- `school_scraping/maintenance/compare_udise_lists.py` ? one configurable saved/fresh comparison.

Report field names, default filenames, source-ID padding and historical location behavior are preserved. Mumbai comparison reports now correctly say Mumbai. This removes duplicate implementations without removing audit/comparison capability.

Regenerable caches were also physically removed: **10 directories, 164 compiled files, 3231412 bytes**. Absolute targets were verified within the workspace; linked or non-bytecode content was refused. See `reports/cleanup_cache_removals.json` for exact paths/counts.

## Backup and retained data

Recoverable code/documentation backup: `archive/cleanup_backups/20261109_171652/`. All 48 manifest SHA-256 checks passed. The earlier disabled legacy archive was retained.

All 24 pre-existing modified/untracked raw-data, report and log files captured at the start of this cleanup retain their SHA-256 hashes. The two moved sample files retain their original bytes. The bulk JSON importer now excludes samples and runtime recovery/cache JSON while continuing to include school datasets and refresh backups.

Retained directories:

| Directory | Reason |
|---|---|
| `school_scraping/`, `database/`, `config/`, `tests/` | Required implementation, API/model/migration/configuration and tests. |
| `data/`, `final_data-zipped/` | Existing raw/derived data, source lists, snapshots and historical datasets. |
| `reports/`, `logs/`, `docs/` | Historical/operational evidence and documentation. |
| `archive/` | Recoverable legacy and current-code backups. |
| `.refactor_validation/` | Important results, code snapshots, live raw samples, JSONL recovery and stopped test database data/logs. |
| `venv/`, `.env` | Existing dependencies and credentials; not deleted or replaced. |

`.vscode/settings.json` hides retained bulky/history folders and caches in Explorer/search. Visibility changes are separate from the actual moves/removals above. The settings file is reviewable; other local IDE files stay ignored.

Four ZIP files were already marked deleted at cleanup start (`data.zip`, `final_data-zipped.zip`, `school.zip`, `School_Data_Final_CrossCheck.zip`). This cleanup did not remove or restore them. Their existing Git status remains visible.

## Validation after cleanup

**Passed:**

- **150 tests, zero failures and zero skipped in the final opt-in run**, including the existing **25 isolated PostgreSQL integration tests**. Evidence: `.refactor_validation/cleanup_results/postgres_tests_final.txt`.
- The default offline run also passed; its 25 PostgreSQL-only cases were skipped until the explicit isolated test connection was supplied.
- 42 Python syntax/bytecode checks and 24 import checks.
- Nine root entry-point help/dry-run checks, plus relocated processing/maintenance help and foreign-working-directory checks in the suite.
- Standardization/canonical mapping, yearly propagation, validation, source-ID duplicate handling, incremental refresh and retry behavior.
- PostgreSQL logging, transactional importing, matching preservation, rollback, checkpoint correctness, JSONL fallback and replay using the isolated PG17 server at loopback port 55439.
- Generic audits/comparisons against temporary fixtures for Chandigarh, Delhi, Mumbai and explicit new scope inputs; source input bytes stayed unchanged.
- Exact three-root-Python invariant and AST scan showing no active imports use removed root module paths.
- Sample/import-selection regression: example/recovery JSON is excluded; raw/canonical school data and refresh backup JSON remain eligible.
- Backup/data/sample hashes and changed-file whitespace checks.

**Failed:** none in final validation.

**Not executed:** nationwide/live scraping, production database writes/migrations, a continuously running scheduler, or report-generating maintenance/batch mains against the existing datasets. The isolated test server was stopped after testing, and its data/logs were retained.

## PostgreSQL activation remains explicit

`config/states_pincodes.json` still has `database.enabled: false`; location examples remain disabled. No migration or production write was applied during cleanup. Review the existing migration, choose the intended database and authorized writer role, then explicitly enable database integration:

```powershell
psql --dbname YOUR_REVIEWED_DATABASE -v ON_ERROR_STOP=1 --file database/migrations/001_scraping_memory.sql
python saras_dynamic_scraper.py --state Punjab --limit 2 --database
```

The migration, memory logs, checkpoints, fallback queue and existing importer/model remain connected through the relocated package modules. Credentials stay in the existing private configuration.

## Exact commands

Safe previews for the three root entry points:

```powershell
python udise_dynamic_scraper.py --state Punjab --state Haryana --dry-run
python saras_dynamic_scraper.py --state Haryana --pincode 134001 --dry-run
python daily_scheduler.py --config config/states_pincodes.json --dry-run
```

Controlled source runs and scheduler activation, when intended:

```powershell
python udise_dynamic_scraper.py --state Punjab --district Ludhiana --limit 2 --manual-captcha 300
python saras_dynamic_scraper.py --state Punjab --limit 2
python daily_scheduler.py --source saras --state Punjab --time 02:00 --limit 2
```

Relocated utility examples:

```powershell
python -m school_scraping.standardize_school_data --help
python -m school_scraping.canonical_v4_mapper --help
python -m school_scraping.saras_canonical_v4_mapper --help
python -m school_scraping.validate_canonical_v4 --help
python -m school_scraping.validate_saras_canonical_v4 --help
python -m school_scraping.maintenance.audit_list_vs_raw --location Delhi --dry-run
python -m school_scraping.maintenance.compare_udise_lists --location Mumbai --dry-run
python -m school_scraping.maintenance.count_current_data
```

See README and `docs/cleanup_inventory.md` for all old-to-new commands. Historical reports/backups retain original filenames as historical evidence; they are not active references. Source access restrictions and UDISE manual CAPTCHA requirements are unchanged.

## Final Git status

`git mv` and `git rm` staged the requested path changes and duplicate removals. Follow-up content/import fixes and new replacement/helper/test/documentation files remain working-tree changes; review the staged and unstaged changes together before any future commit. Existing unrelated changes were preserved. Nothing was committed or pushed.

```text
 M .gitignore
 M README.md
 D School_Data_Final_CrossCheck.zip
D  audit_chandigarh_list_vs_raw.py
D  audit_delhi_list_vs_raw.py
D  audit_mumbai_list_vs_raw.py
D  compare_udise_delhi_lists.py
D  compare_udise_mumbai_lists.py
D  compare_udise_school_lists.py
 M daily_scheduler.py
 D data.zip
 M data/CBSE_SARAS/delhi/_all_schools.json
 M data/CBSE_SARAS/delhi/_summary.json
 M data/CBSE_SARAS/mumbai/_all_schools.json
 M data/CBSE_SARAS/mumbai/_summary.json
 M data/Mumbai/2025-26/_summary.json
R  gallery_data.json -> data/samples/gallery_data.json
 M database/import_json_to_postgres.py
 M database/load_school_tables.py
 D final_data-zipped.zip
 M logs/daily_scheduler.log
 D saras_scraper.py
 D saras_scraper_mumbai.py
 D school.zip
RM canonical_v4_mapper.py -> school_scraping/canonical_v4_mapper.py
RM build_summary.py -> school_scraping/maintenance/build_summary.py
RM compare_raw_vs_standardized.py -> school_scraping/maintenance/compare_raw_vs_standardized.py
RM count_current_data.py -> school_scraping/maintenance/count_current_data.py
RM missing_value_classifier.py -> school_scraping/maintenance/missing_value_classifier.py
RM raw_data_audit.py -> school_scraping/maintenance/raw_data_audit.py
RM saras_raw_data_audit.py -> school_scraping/maintenance/saras_raw_data_audit.py
RM section_level_audit.py -> school_scraping/maintenance/section_level_audit.py
RM saras_canonical_v4_mapper.py -> school_scraping/saras_canonical_v4_mapper.py
RM standardize_school_data.py -> school_scraping/standardize_school_data.py
RM validate_canonical_v4.py -> school_scraping/validate_canonical_v4.py
RM validate_saras_canonical_v4.py -> school_scraping/validate_saras_canonical_v4.py
 D scraper.py
 D test_udise.py
 D udise_batch_refresh.py
 D udise_daily_refresh.py
?? .vscode/
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
?? reports/cleanup_cache_removals.json
?? reports/dynamic_refactor_validation.md
?? reports/incremental_scraping_validation.md
?? reports/postgres_json_import_failures.csv
?? reports/project_cleanup.md
?? reports/udise_refresh/batch_udise_20261023_214130_001255.json
?? reports/udise_refresh/udise_07060208102_20261023_214108_327612.json
?? reports/udise_refresh/udise_07060208307_20261023_214120_345716.json
?? reports/udise_refresh/udise_07060209108_20261023_214110_899778.json
?? reports/udise_refresh/udise_07060209204_20261023_214129_957442.json
?? reports/udise_refresh/udise_07060308202_20261023_214115_000653.json
?? saras_dynamic_scraper.py
?? school_scraping/__init__.py
?? school_scraping/common.py
?? school_scraping/config.py
?? school_scraping/datasets.py
?? school_scraping/incremental.py
?? school_scraping/maintenance/__init__.py
?? school_scraping/maintenance/audit_list_vs_raw.py
?? school_scraping/maintenance/compare_udise_lists.py
?? school_scraping/memory.py
?? tests/
?? udise_dynamic_scraper.py
```
