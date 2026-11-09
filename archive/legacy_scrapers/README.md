# Preserved legacy collection workflows

These files were moved from the project root after dependency inspection. The
`.py.disabled` extension keeps them out of the active Python workflow. Their bytes
include all working-tree edits present when the refactor began; nothing was reset.
`manifest.json` records the original paths and SHA-256 hashes.

| Original file | Replacement |
|---|---|
| `scraper.py` | `udise_dynamic_scraper.py` |
| `saras_scraper.py`, `saras_scraper_mumbai.py` | `saras_dynamic_scraper.py` |
| `udise_daily_refresh.py`, `udise_batch_refresh.py` | UDISE dynamic scraper with `--refresh` and a selected scope |
| `test_udise.py` | Live diagnostic preserved here; offline tests are in `tests/` |

`daily_scheduler.py.before_refactor.txt` preserves the working scheduler before
the refactor. Archived scripts retain historical imports/settings for reference;
they are not used by the new code. Run the commands in the root README instead.
Existing data, backups, reports and logs were not moved with these scripts.
