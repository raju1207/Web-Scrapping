# Completed project reorganization and cleanup inventory

Cleanup date: 2026-11-09. Paths below are relative to the project root.

The root has been reduced from 21 Python files to exactly these three active entry points:

- `udise_dynamic_scraper.py`
- `saras_dynamic_scraper.py`
- `daily_scheduler.py`

This cleanup physically moved supporting modules and removed duplicate root scripts. Explorer exclusions are a separate visibility setting and are not counted as file removal.

## Recoverable backup

Before moving or removing code, the working copies were preserved under `archive/cleanup_backups/20261109_171652/`. Its manifest records original paths, backup paths, and SHA-256 hashes for 48 protected files. It includes uncommitted working content rather than replacing it with the Git version. The previous inventory is preserved at `archive/cleanup_backups/20261109_171652/support/docs/cleanup_inventory.md`.

The earlier `archive/legacy_scrapers/` backups and their manifest remain retained. No backup archive was deleted.

## Required supporting modules moved

| Former root file | Current module file | Why retained |
|---|---|---|
| `standardize_school_data.py` | `school_scraping/standardize_school_data.py` | Standardized schema and existing per-source transformations used by automatic imports. |
| `canonical_v4_mapper.py` | `school_scraping/canonical_v4_mapper.py` | UDISE canonical mapping and yearly snapshot construction. |
| `saras_canonical_v4_mapper.py` | `school_scraping/saras_canonical_v4_mapper.py` | SARAS canonical mapping. |
| `validate_canonical_v4.py` | `school_scraping/validate_canonical_v4.py` | Existing UDISE validation functions and explicit batch validation. |
| `validate_saras_canonical_v4.py` | `school_scraping/validate_saras_canonical_v4.py` | Existing SARAS validation functions and explicit batch validation. |

Imports in the database pipeline and tests now use the package paths. Shared project/data path constants keep outputs under the original project data directories after relocation. These supporting modules are not extra root collection workflows.

## Distinct maintenance utilities moved

| Former root file | Current module file | Why retained |
|---|---|---|
| `build_summary.py` | `school_scraping/maintenance/build_summary.py` | Explicit rebuilding of source summaries. |
| `compare_raw_vs_standardized.py` | `school_scraping/maintenance/compare_raw_vs_standardized.py` | Raw/standardized field comparison. |
| `count_current_data.py` | `school_scraping/maintenance/count_current_data.py` | Current dataset counts. |
| `missing_value_classifier.py` | `school_scraping/maintenance/missing_value_classifier.py` | Missing-value classification and reusable audit helpers. |
| `raw_data_audit.py` | `school_scraping/maintenance/raw_data_audit.py` | Historical UDISE raw-data quality checks. |
| `saras_raw_data_audit.py` | `school_scraping/maintenance/saras_raw_data_audit.py` | SARAS raw-data quality checks. |
| `section_level_audit.py` | `school_scraping/maintenance/section_level_audit.py` | Section-level completeness inspection. |

The maintenance package is importable without running a report. Its commands are explicit maintenance operations; collection and scheduling do not launch them automatically. Running processing/audit commands may regenerate their derived summaries or reports, so preview `--help` first.

## Verified duplicate root code removed

All six files below were backed up before removal. Their shared implementations were consolidated into two location-parameterized maintenance commands, preserving the historical input/output conventions and report structures.

| Removed root files | Shared replacement | Dependency check |
|---|---|---|
| `audit_chandigarh_list_vs_raw.py`, `audit_delhi_list_vs_raw.py`, `audit_mumbai_list_vs_raw.py` | `school_scraping/maintenance/audit_list_vs_raw.py` | Same list-vs-raw audit behavior with location-specific defaults; now `--location`, `--year`, and explicit path overrides. |
| `compare_udise_school_lists.py`, `compare_udise_delhi_lists.py`, `compare_udise_mumbai_lists.py` | `school_scraping/maintenance/compare_udise_lists.py` | Same saved/fresh list comparison with location-specific filenames; the former generic-named script was the Chandigarh copy. |

Imports, tests, subprocess/script references and active README commands were inspected and updated. Historical backup files and prior reports may still contain original filenames as recovery/history evidence; those are not active dependencies.

Removal scope was code duplicates only. No original scraped dataset, credential, historical record, important log, ZIP, virtual environment or existing archive was deleted by this cleanup.

## Sample files moved unchanged

| Former root file | Current path | Safety check |
|---|---|---|
| `gallery_data.json` | `data/samples/gallery_data.json` | Preserved unrelated university-gallery sample; not used by active school collectors. |
| `standardized_sample.txt` | `data/samples/standardized_sample.txt` | Preserved standardized UDISE example; its ignored status is retained. |

The bulk JSON importer excludes `data/samples/` and runtime recovery/cache JSON. Relocating the gallery sample therefore does not silently add unrelated university data or operational events to school imports.

## Retained directories and Explorer visibility

| Directory / file group | Retention reason |
|---|---|
| `school_scraping/`, `database/`, `config/`, `tests/` | Active shared functionality, school model/API, migration, configuration and regression tests. |
| `data/` | Original and derived school data, snapshots, source lists, refresh backups and preserved samples. |
| `reports/`, `logs/`, `docs/` | Historical evidence, operational logs, validation results and documentation. |
| `archive/` | Recoverable legacy and pre-cleanup source backups. |
| `.refactor_validation/` | Important test results, live samples, JSONL recovery evidence, pre-change source copies and isolated PostgreSQL test data/logs. |
| `final_data-zipped/` | Historical dataset collection; not an obsolete temporary folder. |
| `venv/`, local `.env` | Existing dependencies and private credentials; neither is removed. |
| `.scraping_state/` or configured `--state-dir` | Durable recovery journal, acknowledgements and immutable import snapshots. Retain and back up; not disposable cache. |

The isolated PostgreSQL test cluster under `.refactor_validation/memory_pg17/` was stopped during the artifact audit. Test startup/shutdown affects only this explicit isolated instance; production is not modified. Important results and cluster data/logs are retained.

`.vscode/settings.json` hides `venv`, `.venv`, `.refactor_validation`, `archive`, `final_data-zipped`, `**/__pycache__`, and `**/*.pyc` in Explorer/search. These settings do not delete those paths or newly Git-ignore archives. The shared settings file remains reviewable while other local VS Code files stay ignored.

### Regenerable caches removed after tests

After all 150 tests passed, the cleanup removed **10 cache directories containing 164 compiled files and 3,231,412 bytes**. The original audit found eight directories/114 files; verification created additional compiled files that were included in the final recount. Exact deletion evidence is in `reports/cleanup_cache_removals.json`.

- `__pycache__/`
- `database/__pycache__/`
- `database/api/__pycache__/`
- `school_scraping/__pycache__/`
- `school_scraping/maintenance/__pycache__/`
- `tests/__pycache__/`
- `tests/support/__pycache__/`
- `.refactor_validation/bytecode/`
- `.refactor_validation/incremental_results/bytecode/`
- `.refactor_validation/cleanup_results/bytecode/`

Each absolute target was checked to remain within the workspace, and each removed directory was checked to contain compiled Python files only. Linked directories/files were refused. Source backups, test results, raw samples, recovery journals, PostgreSQL data/logs and virtual-environment contents were excluded. Future Python runs may regenerate caches; Explorer exclusions keep those out of view.

## Command migration

Run package utilities with `python -m` from the project root. They are no longer standalone root script paths.

| Previous command | Current command |
|---|---|
| `python standardize_school_data.py --year 2025-26` | `python -m school_scraping.standardize_school_data --year 2025-26` |
| `python canonical_v4_mapper.py --year 2025-26` | `python -m school_scraping.canonical_v4_mapper --year 2025-26` |
| `python saras_canonical_v4_mapper.py` | `python -m school_scraping.saras_canonical_v4_mapper` |
| `python validate_canonical_v4.py --year 2025-26` | `python -m school_scraping.validate_canonical_v4 --year 2025-26` |
| `python validate_saras_canonical_v4.py` | `python -m school_scraping.validate_saras_canonical_v4` |
| `python audit_chandigarh_list_vs_raw.py` | `python -m school_scraping.maintenance.audit_list_vs_raw --location Chandigarh` |
| `python audit_delhi_list_vs_raw.py` | `python -m school_scraping.maintenance.audit_list_vs_raw --location Delhi` |
| `python audit_mumbai_list_vs_raw.py` | `python -m school_scraping.maintenance.audit_list_vs_raw --location Mumbai` |
| `python compare_udise_school_lists.py` | `python -m school_scraping.maintenance.compare_udise_lists --location Chandigarh` |
| `python compare_udise_delhi_lists.py` | `python -m school_scraping.maintenance.compare_udise_lists --location Delhi` |
| `python compare_udise_mumbai_lists.py` | `python -m school_scraping.maintenance.compare_udise_lists --location Mumbai` |
| `python build_summary.py` | `python -m school_scraping.maintenance.build_summary` |
| `python compare_raw_vs_standardized.py` | `python -m school_scraping.maintenance.compare_raw_vs_standardized` |
| `python count_current_data.py` | `python -m school_scraping.maintenance.count_current_data` |
| `python missing_value_classifier.py` | `python -m school_scraping.maintenance.missing_value_classifier` |
| `python raw_data_audit.py` | `python -m school_scraping.maintenance.raw_data_audit` |
| `python saras_raw_data_audit.py` | `python -m school_scraping.maintenance.saras_raw_data_audit` |
| `python section_level_audit.py` | `python -m school_scraping.maintenance.section_level_audit` |

Safe previews for the three unchanged root entry-point names:

```powershell
python udise_dynamic_scraper.py --state Punjab --state Haryana --dry-run
python saras_dynamic_scraper.py --state Haryana --pincode 134001 --dry-run
python daily_scheduler.py --config config/states_pincodes.json --dry-run
```

Both generalized comparison commands also offer `--help` and `--dry-run` without data/report writes.

## Git and database boundaries

Tracked moves and verified duplicate removals use `git mv` / `git rm`, so Git may show staged renames/removals plus unstaged follow-up import/path edits. Nothing is committed or pushed. Existing unrelated modified/untracked files remain preserved.

Production PostgreSQL writes remain disabled by default in shared configuration. Activation still requires reviewing/applying `database/migrations/001_scraping_memory.sql` to the intended database with an authorized writer role, then choosing `--database` or enabling it in config. Cleanup does not apply a production migration or alter production records. README contains the exact activation procedure and memory-log queries.

Four ZIP deletion entries were already present before this cleanup: `data.zip`, `final_data-zipped.zip`, `school.zip`, and `School_Data_Final_CrossCheck.zip`. They were left as found and are not part of this cleanup's deletion inventory.
