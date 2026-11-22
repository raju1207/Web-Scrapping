# Local PostgreSQL provisioning rehearsal and production plan

Section 14A rehearses the existing database-first/database-only architecture in a **new disposable PostgreSQL 17 cluster** on this Windows PC. A rehearsal is not production. No production database, production credential, Windows service or scheduled task is approved or created by this plan. Keep the original scheduler and database flags false; the daily application time remains **23:00 Asia/Kolkata / 17:30 UTC**.

Use [Windows preparation](WINDOWS_LOCAL_DEPLOYMENT.md), [production admission](PRODUCTION_READINESS.md), [database-only behavior](database_only.md) and the unapproved [safety profile](../config/production_safety.example.json) alongside this runbook. Previous recovery evidence and runbooks remain unchanged.

## Execution record

The final retained Section 14A report is **PASSED**, with an empty error list. These are completed disposable rehearsal observations, not production approval. Local evidence is retained at `.refactor_validation/section14a_pg17_rehearsal_8c4b28d88f88/rehearsal_report.json`; it is private operational evidence excluded from Git.

| Check | Current evidence |
|---|---|
| Rehearsal cluster path | `E:\GITHUB\PROJECTS\Web-Scrapping\.refactor_validation\section14a_pg17_rehearsal_8c4b28d88f88\pgdata`; initialized once in a verified new directory |
| Loopback endpoint / cluster | `127.0.0.1:59487`, PostgreSQL 17.5, system identifier `7710673811983194180` |
| New databases | `section14a_app_8c4b28d88f88` OID 16392; `section14a_legacy_8c4b28d88f88` OID 16393; `section14a_catalog_8c4b28d88f88` OID 16394; separate restore target `section14a_restore_8c4b28d88f88` |
| Rehearsal identities | `section14a_admin_8c4b28d88f88`; non-superuser migration LOGIN `section14a_migrator_8c4b28d88f88`; restricted LOGIN `section14a_scraper_8c4b28d88f88`; NOLOGIN read-only groups `section14a_reporter_8c4b28d88f88` and `section14a_backup_8c4b28d88f88` |
| Secret handling | Random test-only credentials, new protected NTFS staging restricted to the current user/SYSTEM/administrators, DPAPI-protected persistence; bootstrap input removed after initialization; original `.env` unchanged |
| SCRAM / negative authentication | Real application connector and restricted password login passed; wrong and missing passwords rejected; local/127.0.0.1/::1 HBA entries require SCRAM; actual listen address restricted to `127.0.0.1` |
| Schema | Existing three SQL files applied as the non-superuser migration LOGIN in the new targets; application objects owned by that migrator; all three databases initially had ten application tables and empty school records |
| Catalogue contract | Ten tables, 32 indexes, 38 constraints: 10 primary keys, 12 foreign keys, 5 unique and 11 check constraints; required columns/JSONB and identity relationships verified |
| Restricted grants | Writer has required SELECT/INSERT/UPDATE, except `scrape_events` SELECT/INSERT only; writer needs no identity-sequence grants and has no ownership, administrator or broad-role privileges |
| Negative permission tests | DELETE, TRUNCATE, immutable-event UPDATE, application-schema DDL, public-schema DDL and `SET ROLE` to migrator denied with SQLSTATE `42501` |
| Actual startup preflight | Both source scopes passed actual read-only identity/user/OID/schema/role/TLS-policy/timeout checks with an explicit rehearsal test profile; monitoring was an **injected mock** reporting `deployed_monitoring=true`, which proves neither deployed monitoring nor encrypted/off-machine backup |
| TLS/local policy | Explicit test-only local-loopback exception; no claim of encrypted TLS transport or production certificate validation |
| Timeouts | Real application connector: 5-second connection timeout, 60,000 ms statement timeout and 1,000 ms lock timeout; statement and lock timeout enforcement actively tested |
| Durability | `fsync=on`, `full_page_writes=on`, `synchronous_commit=on` observed; this is not a power-loss recovery drill |
| Real-connector mocked-source imports | Three restricted-login imports passed: one SARAS affiliation and one UDISE code for both 2024-25 and 2025-26; existing identity/year skip and missing-year eligibility verified |
| Pipeline integrity / ACKs | Canonical 4.0, original PIN, raw/canonical/history JSONB and checksums, source/entity relationships and two yearly snapshots verified; an independent observer confirmed school/completion COMMIT before ACK; validation rollback and idempotence passed |
| Source / payload isolation | Zero live source requests and zero local school payload files from the database-only rehearsal |
| Historical preservation | PASS: stopped 55439/55449 PGDATA sets of 1,454 and 3,997 files matched SHA-256, size and modification time; 61,900 historical raw/config/log/backup files unchanged. Old clusters were never started; historical SQL row/catalogue baselines remain unavailable |
| Backup / restore exercise | PASS: real `pg_dump` exercised the NOLOGIN backup group through `SET ROLE`; separate new restore database matched rows, sequences and 32 indexes. Dump is intentionally unencrypted/local only; application grants require separate reapplication |
| Focused tests | 175 passed, 0 failed, 0 skipped; 150 subtests |
| Full suite | 414 passed, 0 failed, 0 skipped; 506 subtests |
| Previously skipped PostgreSQL cases | All 43 executed: 25 legacy administrator-fixture cases, 14 database-only cases with restricted SCRAM writer, 3 ACK administrator-fixture cases and 1 catalogue administrator-fixture case |
| Final rehearsal cluster state | STOPPED after checks; new data and private evidence retained; no Windows service or task changed |
| Original configuration | `.env` and source configuration unchanged; scheduler disabled at 23:00 Asia/Kolkata, default database integration disabled and actual production templates unapproved |

Evidence must name actual generated paths/ports/databases and observed identities, with test counts and skip reasons. Never substitute an old passing acceptance result. Do not put credentials, SCRAM verifiers, connection strings containing passwords or secret environment values into this document.

The initial startup encountered an inherited output-handle wait; only the new cluster was stopped and restarted with a file-backed log, without reinitialization. A metadata-harness `NameError` was corrected after a read-only identity/schema check. No SQL deployment error remained, and the completed final integration, test, restore and preservation checks passed. These harness corrections did not change historical targets or initialize the new cluster again.

The database-backed parts of preflight were exercised against the restricted SCRAM login. Its monitoring input was explicitly injected and mocked; real monitoring, encrypted/off-machine backups, production TLS and operator approvals remain unverified. Passing administrator-fixture tests does not establish least privilege; that evidence comes from the separate real writer imports and permission denials. Archive-history UPDATE remains possible at the database privilege level and is application-controlled; immutable-event UPDATE was denied.

## Protected inventory and creation gates

The existing loopback clusters at **127.0.0.1:55439** and **127.0.0.1:55449** contain historical source data, restores, acceptance databases and recovery evidence. Preserve every existing database there, including `postgres`, templates and names not previously recognized. An unfamiliar database is not disposable. Never connect to port 5432 for this task.

Before creating a rehearsal, record the read-only service/listener/database inventory and any safely accessible identity/row baseline. Do not connect to an unapproved endpoint merely to complete the inventory. Protect old raw files, snapshots, dumps, logs and manifests; hashes/counts do not authorize changing them. Record unavailable baseline checks honestly.

Creation requires all of these gates:

1. A unique run directory outside tracked source and historical recovery locations; its intended new `pgdata` must not already exist. Resolve the path and reject junctions, symlinks, traversal and redirected ancestors. Never initialize into an existing data directory, even one that appears empty.
2. A verified unused, explicit port other than 5432/55439/55449, and a unique database/role namespace for that run. Recheck before startup; if the port becomes occupied, stop rather than redirecting to another server.
3. Exact PostgreSQL 17 binaries and a new cluster system identity. Starting this cluster must use its own `pg_ctl -D` path/log and explicit loopback listen address/port; no registration, restart or change of an existing Windows service.
4. Secure password authentication and private credential provisioning that do not print secrets or use the project's `.env`. Restrictive NTFS access must be verified for any new credential-bearing staging artifact; no assumption that POSIX permission options secure Windows files.
5. A retained audit identifying the only targets this execution may mutate. If any gate cannot be verified, stop provisioning and record the operator action needed.

The rehearsal's generated identity must never be copied into a production approval profile. Do not extend a test guard by accepting arbitrary ports/databases: its exception must remain pinned to the new disposable identity and run path.

## Authentication, network and secret handling

Use explicit `scram-sha-256` authentication during initialization and in the new cluster's applicable TCP HBA rules; do not accept `initdb`'s convenience trust default. `--auth-host` selects TCP authentication, while Windows file permissions need separate review. If a bootstrap password input file is used, protect it, never log its contents, and keep it outside source control. See [PostgreSQL 17 initdb](https://www.postgresql.org/docs/17/app-initdb.html).

Set the new cluster's password-encryption policy to SCRAM before setting rehearsal passwords. Verify HBA rules, reject wrong/missing credentials where appropriate, and check verifier format as a boolean only; never print `pg_authid` data or a verifier. Changing password-encryption configuration does not retroactively convert an existing verifier. Use compatible libpq/psycopg clients. See [PostgreSQL 17 password authentication](https://www.postgresql.org/docs/17/auth-password.html).

Bind the rehearsal to `127.0.0.1` only and the recorded new port. Do not add wildcard network rules, open the firewall or enable trust for remote connections. Supply secrets through the guarded child's private environment or approved private mechanism; keep `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` explicit and disable dotenv loading. No production credential is created by a disposable test.

SCRAM authenticates a password; it is not TLS encryption. The rehearsal must explicitly record either verified TLS or an approved test-only loopback exception. Production must separately approve `verify-full` with a trusted root and matching server identity, or the profile's narrowly documented `approved-local` loopback exception. Do not call a plaintext loopback connection encrypted or treat `sslmode=require` as equivalent to certificate identity verification. See [PostgreSQL 17 TLS](https://www.postgresql.org/docs/17/libpq-ssl.html).

## Schema and existing pipeline

Apply only the current reviewed files, in this order, to the verified new rehearsal target:

1. `database/schema.sql`: `school_scraping.json_file_archive` and archive indexes.
2. `database/schema_school_tables.sql`: entities, source records, annual snapshots and match-review relationships.
3. `database/migrations/001_scraping_memory.sql`: runs, tasks, events, errors, checkpoints and their indexes.

Run with explicit host/port/database/user and stop on the first SQL error. Preserve failure evidence and the new environment; never drop/reinitialize it to hide a failure. Respect the scripts' existing transaction boundaries: do not claim all three files are one atomic migration merely because each invocation succeeded. Runtime scraper startup never applies DDL.

Verify all ten required tables, required columns/JSONB types, primary/foreign/unique keys and indexes from the catalogue. The actual source uniqueness constraint is `(source_system, region, source_record_id)` plus unique archive linkage; affiliation/code matching and advisory locking remain existing loader behavior. Do not invent a new global SQL uniqueness constraint. Annual snapshot uniqueness is `(source_record_pk, academic_year)`. Cross-source match candidates remain subject to review, not automatic merging.

`import_scraped_record()` remains the existing mapping/validation/loader path. Database mode supplies the record in memory, preserving original source PIN values; raw/canonical JSONB, content-addressed history, source/master relationships and UDISE snapshots use the current model. Completion and checkpoint changes commit with school data, then a separate transaction writes the acknowledgement. Database errors stop collection without a school-file fallback.

Archive latest/history rows share `json_file_archive`, which requires UPDATE for the current loader. Application history preservation and content hashes do not, by themselves, prove that every manually issued SQL UPDATE to a history row is impossible. Report the actual negative privilege tests and any row-level enforcement separately. Immutable `scrape_events` UPDATE must remain denied. Do not add a new history architecture or claim untested tamper protection.

## Role design and permission verification

Use separate duties; the exact production names remain unapproved. Suggested naming patterns such as `<project>_prod`, `<project>_owner`, `<project>_scraper`, `<project>_report` and `<project>_backup` are conventions, not created production objects or permission to reuse rehearsal names.

| Duty | Intended boundary |
|---|---|
| Cluster/bootstrap administrator | Rehearsal provisioning only; never the application identity |
| Schema/migration owner | Separate owner/administrator; production ownership and migration login approved independently |
| Scraper LOGIN | Non-owner, non-superuser; only actual application operations and private SCRAM credential |
| Future reporting | Rehearsal read-only NOLOGIN group exists; a production reporting LOGIN and permissions remain separately unapproved |
| Future backup/recovery | Rehearsal read-only NOLOGIN group exists; production backup administration/storage rights and credentials remain separately unapproved |

For the real restricted rehearsal LOGIN, prove allowed CONNECT/schema USAGE and required table SELECT/INSERT/UPDATE operations, while denying superuser/CREATEDB/CREATEROLE, database/schema/table ownership, unnecessary CREATE, broad server-file/program/data roles, DELETE/TRUNCATE/TRIGGER and immutable-event UPDATE. Check inherited memberships and PUBLIC grants, not only direct grants; do not use `GRANT ALL` or grant options as shortcuts.

Measure sequence requirements using the actual loader. Current IDs use generated identity columns; do not assume every sequence needs blanket application access. Separately test serial/explicit `nextval` requirements if a future migration introduces them. Grants on tables and sequences are distinct, and ownership carries additional authority. See [PostgreSQL 17 GRANT](https://www.postgresql.org/docs/17/sql-grant.html).

Negative tests must fail safely, without permanently removing a required object or modifying historical data. Record rollback limits: PostgreSQL sequence allocations can advance even when a transaction rolls back. A passing rehearsal LOGIN is not a deployed Windows runtime identity, production HBA/TLS certificate or backup administrator.

## Identity, timeouts, durability and admission

Use the real `school_scraping.database_only` connection factory with a child-only environment. Verify actual database, restricted user, server address/port/version, database OID and cluster identity before writes. Compare with the generated audit; do not force a match by substituting expected values. Optional cluster metadata inspection may require the separate administrator rather than broadening scraper privileges.

Rehearsal timeout values must be explicitly recorded and tested: positive connection, statement and lock budgets, lock no greater than statement. Verify actual settings through the application's connector and exercise safe timeout/rollback behavior where possible. A process hard timeout and SQL timeouts solve different failure cases; retain both.

Inspect `fsync`, `full_page_writes` and `synchronous_commit`; preserve durable synchronous WAL commit behavior for the design being rehearsed. Do not disable durability to make a test faster. A commit/ACK ordering test is not a power-loss or storage-controller recovery test. See [PostgreSQL 17 WAL settings](https://www.postgresql.org/docs/17/runtime-config-wal.html).

Run startup preflight with a clearly identified disposable test profile and the actual restricted login. A test-only monitoring exception must remain constrained to that approved test route; it does not mark the readiness prototype deployed. Keep the real production profile unapproved. Report actual PASS/BLOCKED outcomes rather than editing approval fields to conceal a failing prerequisite.

## Mocked-source verification and reporting

Use source fixtures only; no live SARAS/UDISE site, browser session or CAPTCHA request is part of this rehearsal. Test new SARAS affiliation import and existing pre-detail skip; new UDISE code/year import, existing school/year skip and eligibility when the requested year snapshot is missing. Verify canonical 4.0, source PIN, raw/canonical JSONB, history, relationships, annual uniqueness and no local school raw/canonical/payload files.

Exercise rollback on validation/import failure, no ACK on failed commit, missing ACK after a confirmed commit, retry/concurrent idempotence, safe lookup failure and completed checkpoint consistency. Missing ACK detection is read-only reporting, not permission to replay or repair receipts. Preserve all fixtures/results in the new disposable environment.

Enable each test DSN only after its own guard verifies the new cluster/database identity. Some tests require a different handler/schema contract or stronger administrative fixture; report those exact incompatibilities and skips rather than pointing them at an old database or loosening safety assertions. Keep normal source configuration unchanged and distinguish focused, previously skipped integration, and full-suite counts/subtests.

The completed rehearsal used the explicit `SCHOOL_SCRAPING_SECTION14A_GUARD` manifest under the new run directory. It pins the run root/PGDATA, nonce, new nonhistorical port, database names/OIDs/users, PostgreSQL major version and system identifier. A separate private administrator DSN supports the identity check; restricted-writer identity is verified independently. Existing guard behavior is preserved when this opt-in is absent. DSN values are never documentation or report content.

After testing, compare every available historical baseline and protected file hash, source configuration and disabled defaults. Record any unavailable preservation checks. Retain the new cluster's final running/stopped state and an explicit safe resume procedure; never stop or restart an unrelated Windows service.

The retained cluster is stopped. A future authorized rehearsal resume must recheck the exact normal-directory path, PostgreSQL 17 version, protected-target inventory, unused recorded port and private credential/guard integrity before starting only that `pg_ctl -D` target with explicit loopback/port and a file-backed log. Verify actual system/database identities again before tests. Do not reinitialize it, register a service or treat retained test credentials as production credentials.

## Production transition, backup and rollback

Production remains a separate installation decision. Approve a new target identity/port/path, restricted Windows account and database LOGIN, NTFS effective access, HBA, TLS policy, positive timeouts, durability, exact migration/release and source scope/year. Do not reuse the disposable cluster, historical 55439/55449 targets or port 5432. Review Windows service identity/startup/recovery before a future authorized service installation; this rehearsal makes no service changes.

Before provisioning production, approve encrypted/off-machine backup scope and destination, encryption recipients/tool, key custody/recovery, retention, RPO/RTO, capacity thresholds and alert delivery. No external drive/cloud account, key, policy value or uploaded backup is assumed to exist. Custom-format `pg_dump` compression is not encryption.

After separate approval, create new coordinated recovery sets, record identity/code/schema/set IDs and hashes, encrypt new copies, prove independent retrieval/decryption and restore into a new empty target. Verify full row identities/relationships/yearly data/sequences and read-only receipt reconciliation; preserve required historical external files/journals even though new school payloads reside in PostgreSQL. Record what was actually restored and measured; an unencrypted local dump is not production off-machine readiness.

For a migration/release, take a verified compatible recovery set, quiesce approved writers, check identity and actual schema state, apply reviewed DDL as the migration identity, then retest the restricted application role and admission. `IF NOT EXISTS` does not validate an existing table's complete definition. Grant future objects explicitly; do not give the runtime ownership to avoid reviewing grants.

For rollback, disable only the approved future trigger, stop/reap the owned writers, preserve reports and committed receipts, and select a reviewed compatible prior release. Re-run admission before any restart. If data recovery is necessary, restore to a **new** destination and obtain an approved reconciled cutover. Never restore over existing databases, remove committed school records, reset checkpoints, fabricate ACKs or replay historical queues to manufacture a clean result.

The Windows readiness CLI remains read-only with no automatic database connection. Scheduler admission remains mandatory for scheduled database-only jobs; the original scheduler stays disabled at 23:00 Asia/Kolkata and default database integration stays disabled. A successful disposable rehearsal supports review; it does not approve production database creation, credentials, service/task installation, live collection or continuous scheduling.

**Rehearsal: PASSED. Production database provisioning: WITH CONDITIONS. Scheduler activation: NO.** Required conditions include an explicitly approved new production identity and restricted runtime/login, TLS or local exception, NTFS and power remediation, verified encrypted/off-machine backup/recovery, deployed monitoring/alerts and operator sign-off. None is satisfied merely by copying the disposable profile or the injected monitoring result.
