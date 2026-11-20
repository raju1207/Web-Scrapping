# Web-Scrapping — Project Progress Report

Updated: 20 November 2026

## Project Objective

Build a school-data collection platform integrating UDISE+ and
CBSE SARAS with Canonical School Schema v4, PostgreSQL, safe
incremental collection, recovery and scheduled automation.

## Completed Development

- Dynamic UDISE+ scraping integration
- Dynamic CBSE SARAS scraping integration
- Raw data collection and validation
- Canonical School Schema v4 mapping
- PostgreSQL school entity and source identity management
- UDISE academic-year snapshots
- Immutable import history
- Import acknowledgements and checkpoints
- Retry, recovery and duplicate protection
- HTTP 429 rate-limit safety handling
- Daily scheduler integration

## Verified Integration

- Delhi SARAS: 2,255 collected records verified
- SARAS controlled live scraping: PASSED
- UDISE controlled live scraping: PASSED
- Both source scheduler integration tests: PASSED
- PostgreSQL import and acknowledgement ordering: PASSED

## Backup and Recovery

- PostgreSQL independent backup/restore: PASSED
- Database rows restored and verified: 19,399
- Full filesystem recovery: 25,034 files verified
- File checksum mismatches: 0
- Scoped database/filesystem recovery consistency: PASSED
- Duplicate prevention: PASSED

## Historical Section 11 Automated Testing

- Full offline regression suite: 213 tests passed
- Subtests: 172 passed
- Failed: 0
- Database-write tests skipped: 25

## Current Status

Core development: COMPLETE
Controlled live integration: PASSED
Database backup and restoration: VERIFIED
Filesystem recovery: VERIFIED
Production readiness: CONDITIONAL
Continuous scheduler: DISABLED

## Remaining Production Requirements

- Approved encrypted off-machine backups
- Production database credentials and TLS
- Production least-privilege role deployment
- Coordinated backup automation
- Monitoring, watchdog and alert delivery
- Final production deployment verification
- Fresh UDISE listing discovery and full geographic coverage
  remain unverified

## Database-only Development

Database-first and database-only architecture:

Website listing
-> Identifier validation
-> PostgreSQL existing-record lookup
-> Skip existing / Fetch new
-> Canonical v4 validation
-> Direct PostgreSQL storage

Implemented as an opt-in mode for both sources and scheduler command generation:
`--storage-mode database --database --new-only`.

The existing Canonical v4 pipeline now accepts in-memory records and stores
school payloads in PostgreSQL JSONB without local school payload files. Data
and completion evidence commit together; acknowledgements follow commit.
Legacy file storage/recovery remains available. See `database_only.md` for
refresh, failure handling, prerequisites and isolated test routing.

## Safety

Section 13B adds production admission, configured child supervision, read-only
ACK reconciliation and prototype readiness checks. The production profile
template remains unapproved with deployment-specific values unset. No source
scraping, scheduler activation, production connection, credential/ACL change,
backup upload or historical queue replay is performed by this foundation.
The current operating and release rollback reference is `PRODUCTION_READINESS.md`;
older recovery runbooks are retained unchanged. Infrastructure decisions and
deployed monitoring/backup verification remain blockers.

Section 13B isolated verification: focused 168 passed, 0 failed, 0 skipped,
237 subtests; full regression 338 passed, 0 failed, 25 legacy PostgreSQL cases
skipped, 378 subtests. All four new PostgreSQL safety/reconciliation cases and
the 14 existing database-only PostgreSQL cases ran in new disposable databases.

Production scheduler and default database integration remain
disabled. Historical data, recovery artifacts and existing
backups must be preserved.
