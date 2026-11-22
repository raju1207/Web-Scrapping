# PostgreSQL production approval and configuration plan

Section 14B prepares reviewable configuration and future manual procedures only. **No production provisioning or activation is authorized.** No database connection, production database/role/password creation, migration, service/task change, secret change, upload or live scraping is part of this preparation. Keep the original `scheduler.enabled=false`, `database.enabled=false` and daily **23:00 Asia/Kolkata / 17:30 UTC** schedule unchanged.

The [production profile template](../config/production_safety.production.example.json) is deliberately unapproved. `127.0.0.1` and PostgreSQL major 17 describe the proposed topology, not a deployed identity. Port, database/login names, OID, system identifier, paths, timeouts, TLS bindings and operational approvals remain unresolved. Do not copy rehearsal identities into those target fields.

Use [Section 14A's rehearsal record](POSTGRESQL_LOCAL_SETUP.md), [Windows preparation](WINDOWS_LOCAL_DEPLOYMENT.md), [production admission](PRODUCTION_READINESS.md) and [database-only behavior](database_only.md) as supporting references. Historical files, profiles, databases and runbooks remain preserved.

## What the rehearsal established

Section 14A verified the existing schema and real restricted SCRAM connector in a new disposable PostgreSQL 17.5 cluster, including actual imports, annual snapshots, canonical 4.0 validation, transactional completion before ACK, denied destructive privileges and a local dump/restore. Its full run passed 414 tests and 506 subtests, with all 43 previously skipped PostgreSQL cases executed and no failures/skips. That cluster is stopped and retained as evidence.

These results do not approve a production target. Rehearsal monitoring was explicitly mocked; transport used a test-only loopback exception; its dump was local and unencrypted. Production certificates/login, Windows runtime identity, restricted NTFS access, power policy, encrypted off-machine recovery, monitoring delivery and retention remain separate decisions. Application history preservation is not database-level protection against every manual history UPDATE.

## Approval matrix

Record a named approver, approval reference, exact selected value and verified evidence for each gate. Null fields and false approval flags are intentional; a draft file or successful unit test is not an approval.

| Gate | Required decision/evidence | Current state |
|---|---|---|
| Production identity | Dedicated new cluster/data path and unused explicit port; approved database/login names; generated system ID/OID captured at the correct stage | Unapproved; target values unresolved |
| Protected inventory | Deny 5432, 55439, 55449 and 59487; preserve every historical/test/restore database, including unfamiliar names | Required; none is a production candidate |
| Windows identities | Separate PostgreSQL service, migration and application responsibilities; permitted logon/startup/recovery model | Unapproved |
| Filesystem/ACLs | Normal new paths, no redirected ancestors; private data/certificate/secret locations; reviewed effective rights and rollback metadata | Unapproved; no ACL changes |
| Authentication | SCRAM-only HBA/password policy and approved private credential provisioning | Proposed; no credentials created |
| TLS | Verified TLS/certificate/root/host binding, or a separately approved loopback exception with reason and reviewed replacement HBA | Unapproved; no automatic exception |
| SQL roles/grants | Separate migration administrator, restricted scraper, reporting and backup duties; real positive/negative LOGIN tests | Templates only |
| SQL/runtime budgets | Positive approved connection/statement/lock and hard child budgets, with lock no greater than statement | Unresolved; do not reuse pilot numbers as approval |
| Durability | `fsync`, full-page writes, synchronous WAL commit and suitable storage/power policy | Proposed; actual target unverified |
| Source operation | Exact source/state/district/PIN, UDISE year, candidate bound, refresh/new-only policy, delay/retries and legitimate access | No production scope approved |
| Backup/recovery | Encryption recipients/tool, off-machine destination, key custody, integrity/freshness, independent restore, retention and RPO/RTO | Unapproved; no keys/uploads |
| Monitoring/logs | Deployed producer, heartbeat/error/ACK/backup/disk checks, tested real alert recipient, escalation and rotation/retention | Unapproved; prototype is insufficient |
| Windows operation | Power/sleep/wake decision, firewall review, service registration/startup and later task/overlap/missed-run policy | Unapproved; no system changes |
| Release/cutover | Reviewed commit/dependencies/schema, compatible recovery set, rollback owner and separate execution authorization | Unapproved |

Provisioning, database-write acceptance and scheduler activation are separate authorizations. Approving one does not silently approve the others. Keep the real production profile unapproved until its actual gates have been independently verified.

## Prepared artifacts and safe review

| Artifact | Intended use |
|---|---|
| `config/production_safety.production.example.json` | Inactive application admission template; not an approved runtime profile |
| `config/postgresql/production/postgresql.conf.example` | Unresolved PostgreSQL 17 server proposal; no active server configuration is changed |
| `config/postgresql/production/pg_hba.conf.example` | TLS-first loopback SCRAM proposal with cleartext/remote rejection; no active HBA is changed |
| `database/provisioning/production/bootstrap.sql` | Guarded future bootstrap template; no production execution in this phase |
| `database/provisioning/production/migrate.sql` | Guarded single-session migration wrapper; verifies the new target and changes effective role before including existing DDL |
| `database/provisioning/production/privileges.sql` | Guarded future minimum-grants template, applied only after the existing schema |
| `database/provisioning/production/verify.sql` | Future read-only catalogue/identity/privilege inspection |
| `database/provisioning/production/README.md` | Exact template bindings, guard semantics and execution order |

Inspect and bind templates only in a separately approved private deployment copy. Do not load example server files into an existing cluster, replace an active HBA, run unbound SQL, use `GRANT ALL` or change original `.env` routing. There is no new offline validator CLI; repository tests review the templates without provisioning a server.

The server/HBA render requires this explicit binding matrix; a token is never a deployed value:

| Server/HBA token | Approval and cross-check |
|---|---|
| `__APPROVED_PORT__` | New approved unused port; match profile and SQL identity; exclude all four protected ports |
| `__APPROVED_SERVER_CERTIFICATE__`, `__APPROVED_SERVER_PRIVATE_KEY__` | Reviewed TLS certificate/key paths and host identity; server identity reads its private key, scraper does not need that key |
| `__APPROVED_STATEMENT_TIMEOUT_MS__`, `__APPROVED_LOCK_TIMEOUT_MS__` | Positive integer milliseconds matching approved profile/runtime budgets; lock no greater than statement |
| `__APPROVED_SERVER_LOG_DIRECTORY__` | Private new log location with approved service access and capacity |
| `__APPROVED_LOG_ROTATION_AGE__`, `__APPROVED_LOG_ROTATION_SIZE__` | Approved PostgreSQL duration/size values; retention is a separate unapproved policy |
| `__APPROVED_MAINTENANCE_DATABASE__`, `__APPROVED_BOOTSTRAP_ADMIN__` | Newly initialized cluster's actual bootstrap database/admin, pinned separately from the application identity |
| `__APPROVED_DATABASE__`, `__APPROVED_MIGRATION_LOGIN__`, `__APPROVED_SCRAPER_LOGIN__` | Exact new database and separate role bindings; scraper equals the future profile's `database.user` |
| `__APPROVED_REPORTING_ROLE__`, `__APPROVED_BACKUP_ROLE__` | Separate NOLOGIN capability groups; future member LOGIN credentials/authority require approval |

The server proposal keeps UTC timestamps independently of the application's 23:00 Asia/Kolkata schedule. Its TLS-on/SCRAM/durable-WAL values remain proposals until the deployment copy is reviewed. Logging suppresses ordinary SQL statements and error parameters, including `log_min_error_statement=panic`; an operator must approve and test this privacy-versus-diagnostics tradeoff. It does not remove the need for private logs, sanitized client errors or real failure alerts. No unresolved placeholder may reach a live server.

These existing review commands do not provision or activate anything and are not executed as part of this document:

```powershell
# Read-only Windows prerequisite/configuration report; no database connection.
& .\venv\Scripts\python.exe -B -m school_scraping.windows_readiness `
  --profile .\config\production_safety.production.example.json

# Offline schedule/command preview; no database, browser, lock or child launch.
& .\venv\Scripts\python.exe -B .\daily_scheduler.py `
  --config .\config\states_pincodes.json --dry-run
```

The inactive profile is expected to remain BLOCKED. A preview may show the retained Haryana example; that output is not permission to enable or collect it. Do not turn approval flags on merely to make a check pass.

## Future SQL bindings and guarded invocation

The shared `verify.sql` gate is read-only and supports internal stages `bootstrap`, `migrate`, `privileges` and full `verify`. The three write-capable wrappers set their fixed stage and include that gate **before DDL**. An operator running the final read-only verifier must leave its default full stage unchanged; manually selecting a weaker precheck is not verification of a completed deployment.

Every future invocation requires these nonsecret, approved `psql` variables, not guessed production values:

| Binding | Meaning |
|---|---|
| `provisioning_approved=yes`, `approval_reference` | Separate approved database-provisioning authorization; not scheduler/collection approval |
| `approved_host`, `approved_port`, `approved_server_major` | Proposed host `127.0.0.1`, approved new port, major 17; all compared with the real session |
| `approved_system_id`, `approved_data_directory` | Actual new cluster identity/path, independently observed and approved |
| `approved_admin`, `approved_database` | Exact privileged inspection/bootstrap identity and new application database name |
| `migration_role`, `scraper_role`, `report_role`, `backup_role` | Four distinct approved role bindings; never passwords |
| `tls_policy=verify-full` | These SQL templates require verified TLS; they do not automatically accept the profile's possible local exception |
| `approved_maintenance_database`, `approved_maintenance_oid` | Additional bootstrap-only identity of the new cluster's maintenance database |
| `new_database_oid` | Required after creation, pinned from the actual newly created database before migrate/privileges/final verification |

The caller must also supply explicit host/port/user/database, `-X -w`, `ON_ERROR_STOP=1`, approved TLS/timeouts and the real private credential mechanism. `PGSSLMODE=verify-full` and the approved `PGSSLROOTCERT` are required by the proposed SQL route. Choosing a local TLS exception would require a separately reviewed revision of these templates as well as the server/HBA proposal; it is not a switch to bypass these checks.

**Client routing must be validated before starting `psql`.** A SQL guard runs only after the first connection. The reviewed argument builder must reject unset/invalid host, port, database or user; deny ports 5432/55439/55449/59487; and reject conflicting `PGSERVICE`/`PGHOSTADDR` routing before any client launch. Do not rely on a later SQL identity mismatch to protect an original endpoint, or pass an arbitrary prebuilt argument array around that client check.

Use only `bootstrap.sql`, then `migrate.sql`, `privileges.sql` and full `verify.sql` in their approved stages. Stop after bootstrap to review/pin the new OID; stop on any error. Exact binding construction and stage command forms are centralized in the [SQL procedure](../database/provisioning/production/README.md). Do not supply a password through `-v`, change the guard stage in an end-user command or chain raw schema files around the wrapper.

This future **read-only** command shape is deliberately blocked and is not executed. `$ReviewedCommonArguments` means the fully reviewed nonsecret connection/variable array from that SQL procedure; it is not created in this session. No secret value belongs in that array.

```powershell
& {
  throw 'Section 14B is preparation only. Future connection/execution needs separate approval.'
  $ParsedPort = 0
  if ($ApprovedHost -ne '127.0.0.1' -or
      -not [int]::TryParse([string]$ApprovedPort, [ref]$ParsedPort) -or
      $ParsedPort -lt 1 -or $ParsedPort -gt 65535 -or
      @(5432, 55439, 55449, 59487) -contains $ParsedPort) {
    throw 'Approved explicit new client route is required before psql startup.'
  }
  if ($env:PGSERVICE -or $env:PGHOSTADDR) {
    throw 'Conflicting libpq routing must be resolved before psql startup.'
  }
  if ([string]::IsNullOrWhiteSpace($ApprovedDatabaseName) -or
      [string]::IsNullOrWhiteSpace($ApprovedAdmin)) {
    throw 'Explicit approved database and inspection identity are required.'
  }
  $env:PGSSLMODE = 'verify-full'
  $env:PGSSLROOTCERT = $ApprovedClientRootCertificate
  & $ApprovedPsqlPath @ReviewedCommonArguments `
    --host $ApprovedHost --port $ParsedPort --username $ApprovedAdmin `
    --dbname $ApprovedDatabaseName --set "new_database_oid=$ApprovedNewDatabaseOid" `
    --file .\database\provisioning\production\verify.sql
  if ($LASTEXITCODE -ne 0) { throw 'Approved verification stopped; preserve sanitized evidence.' }
}
```

Current checks are static/offline configuration and SQL-structure review, not a PostgreSQL parser/server execution proof. The new guarded templates still need separately authorized disposable execution, syntax/error/negative-case testing and real TLS/login acceptance before any production use. Section 14A's successful execution of the existing schema/pipeline does not execute these new wrappers.

## Future target creation and identity gates

All steps below require a later operator-approved execution record. They are not commands to run during Section 14B.

1. Approve a new production namespace, private data/log/backup paths, exact PostgreSQL 17 binaries and an unused port outside 5432/55439/55449/59487. Prove the intended data directory does not exist, its ancestors are normal directories, and the target cannot collide with historical recovery or tracked source. Recheck the port immediately before a future start; a collision is a stop condition, not permission to change routing.
2. Approve Windows account/NTFS/service and power decisions before execution. Preserve relevant ACL metadata and review effective rights before any later remediation. A PostgreSQL service identity needs private cluster/certificate access; the scraper identity needs approved code/read and output/write access, not filesystem ownership of cluster data. Do not modify existing services/firewall/power settings through this plan.
3. Arrange credentials and, for the TLS-first proposal, trusted certificate/key material through the approved mechanism. No password/key is generated in this session. Keep secrets out of SQL templates, profiles, `psql -v` arguments, shell history, ordinary logs and Git. An initdb password-input artifact needs independently verified private Windows ACLs; POSIX permission switches are not an NTFS policy.
4. A later approved administrator initializes only the verified new directory with PostgreSQL 17 and explicit SCRAM authentication. Apply the reviewed bound server/HBA settings before listening, retaining explicit loopback/port, durable WAL behavior and approved timeouts. The TLS-first HBA must not be made permissive to get through startup. No existing cluster is initialized again or converted into production.
5. After an authorized new-cluster start, perform read-only verification of its actual address/port/version/data path/system ID and bootstrap connection identity before write-capable bootstrap SQL. The bootstrap maintenance database must belong to this verified new cluster; never use a historical `postgres` database or let a default connection select a server.
6. Create roles/database only through the approved guarded bootstrap procedure. Its roles start with `PASSWORD NULL`; separately approved private password provisioning is a later step, not a password embedded in SQL/psql variables. A new database's OID cannot be known before creation: capture its actual OID immediately afterward, compare its name/owner/cluster with the signed plan, and pin that identity **before schema/import writes**. Do not invent an OID or copy the disposable system identifier. Bind approved inspection/profile inputs from observed values while leaving unverified production/scheduler approvals false.
7. Stop on any unexpected identity, privilege, SQL or file check. Preserve the failed new environment and sanitized evidence; do not drop, truncate, reinitialize or switch ports to manufacture success.

SCRAM authentication is required independently of TLS. The server proposal is TLS-first; the client should use certificate/host verification with an explicit trusted root. An operator choosing the existing profile's local-loopback exception must separately approve its reason and a reviewed alternative HBA/server policy. Do not merely disable SSL under a hostssl-only proposal, and do not silently add trust or remote rules. See [PostgreSQL 17 password authentication](https://www.postgresql.org/docs/17/auth-password.html), [TLS](https://www.postgresql.org/docs/17/libpq-ssl.html) and [initdb](https://www.postgresql.org/docs/17/app-initdb.html).

## Schema deployment and minimum duties

Use the guarded `migrate.sql` wrapper on the newly verified empty application namespace. It checks the actual admin session/new database OID/system/data-directory/TLS identity, then uses `SET ROLE` to the non-superuser migration owner before including the existing files in order:

1. `database/schema.sql`.
2. `database/schema_school_tables.sql`.
3. `database/migrations/001_scraping_memory.sql`.
After that wrapper succeeds, apply the separately reviewed guarded privileges template, then read-only catalogue/role/identity verification and real restricted-login acceptance. The wrapper refuses a nonempty application namespace and is not a general upgrade/retry mechanism.

Use `psql -X -w -v ON_ERROR_STOP=1` with explicit host/port/database/user and approved TLS/timeouts; no implicit default endpoint. Do not substitute a separate precheck connection followed by raw `-f database/schema.sql` commands: the guarded wrapper retains identity and effective ownership in one connection. It respects each existing file's BEGIN/COMMIT boundaries, so do not add `--single-transaction` or claim the complete sequence is atomic. `IF NOT EXISTS` is not proof that an existing schema matches. Runtime scrapers apply no DDL. New objects after a future migration need reviewed owner/default-privilege/grant verification; do not solve a missing grant by assigning the scraper an owner role.

Bootstrap role creation and `CREATE DATABASE` also have separate transaction boundaries. If creation fails after roles exist, preserve and investigate that partial state; do not automatically drop roles or rerun guards against it. Effective `SET ROLE` ownership in a verified admin session does not prove that the migration LOGIN can authenticate; real migration/scraper credential and privilege tests remain separate future acceptance steps.

| Duty | Minimum intended boundary |
|---|---|
| Bootstrap administrator | Only future cluster/role/database creation; never the application identity |
| Migration administrator/owner | Own and deploy the approved schema; separate credentials/role membership from the scraper |
| Scraper | CONNECT, application-schema USAGE, required SELECT/INSERT/UPDATE; `scrape_events` SELECT/INSERT only; no unnecessary sequence privileges |
| Reporting | NOLOGIN internal-audit group with SELECT on all ten application tables, including raw/canonical JSONB and contact data; no public/API/report-login membership before separate data-scope approval |
| Backup | Separate read/sequence/export capability needed by the approved dump; no scraper membership or retention-deletion/storage administration |

For the scraper, prove denial of superuser/CREATEDB/CREATEROLE, database/schema/table ownership, unnecessary CREATE, broad server-file/program/data roles, DELETE/TRUNCATE/TRIGGER, immutable-event UPDATE and `SET ROLE` into the migration owner. Check inherited roles, PUBLIC privileges, RLS/security-definer/extension exposure and actual sequence requirements; table-level grants are not a complete security audit. Current generated identity columns required no scraper sequence grants in the rehearsal. Separate backup sequence reads and future SERIAL/explicit-nextval requirements must be measured rather than copied as blanket grants. See [PostgreSQL 17 privileges](https://www.postgresql.org/docs/17/ddl-priv.html).

The reporting template is broad internal audit access, not a privacy-filtered analytics/API role. This phase adds no redacted views or schema. Any reporting LOGIN membership or public/API use needs separate data-scope/access approval and acceptance. The backup NOLOGIN group receives table SELECT and sequence SELECT for dumps, without scraper membership; future RLS and dump permissions must be retested in the actual approved environment.

Archive history and latest views share `json_file_archive`; the current application needs UPDATE there. History preservation is enforced by application paths/hashes, while direct SQL history UPDATE remains possible under those table grants. This phase adds **no history-protection migration**. Any future row-level policy, trigger, role split or append-only archive redesign needs its own compatibility/security proposal and explicit approval; it must preserve the current canonical/pipeline model and be tested separately. Do not describe the current archive as database-enforced tamper-proof storage.

## Integrity, durability and application acceptance

Keep the existing Canonical 4.0 model. Raw/canonical JSONB, immutable observations, source/master relationships, yearly snapshots and match review use the existing pipeline. Source SQL uniqueness is the actual `(source_system, region, source_record_id)` composite plus archive linkage; source lookup/advisory locking handles existing identifier resolution. UDISE annual uniqueness is `(source_record_pk, academic_year)`. Do not invent a new global affiliation constraint or claim cross-source candidates automatically merge.

The acceptance plan must independently verify new imports and existing pre-detail skips; a completed process alone proves neither. Check original PIN preservation, canonical validation, raw/canonical/history hashes, foreign keys, UDISE year eligibility, duplicate/concurrent behavior, rollback and real commit visibility before ACK. A missing ACK can follow a successful commit: the existing reconciliation API is read-only and must not repair, replay or fabricate checkpoints/receipts automatically.

Approve and verify positive connection/statement/lock budgets and a bounded hard process timeout; lock must not exceed statement. Production admission passes the chosen runtime policy through the private child environment with dotenv disabled. Retain `fsync=on`, `full_page_writes=on` and synchronous durable WAL commit semantics, and verify actual settings/storage behavior rather than assuming defaults. Do not accelerate tests by weakening durability. Power-loss recovery and certificate validation remain real deployment checks. See [PostgreSQL 17 WAL settings](https://www.postgresql.org/docs/17/runtime-config-wal.html).

Future `--preflight-only` is read-only but requires actual operator approval, an approved profile and a separately authorized scoped scheduler configuration; it is not a way to bypass disabled safeguards. The original configuration remains false. No admission profile should claim deployed monitoring on the strength of mocked or self-authored successful JSON. Complete SQL-backed verification, current legitimate source authorization and operational acceptance before considering a separately approved bounded one-shot. No live acceptance or scheduler command is executed now.

## Backup, monitoring and recovery approval

Before production writes, approve encryption recipients/tool, destination and transfer authority, key custody/rotation/recovery, access controls, backup timing, integrity/freshness thresholds, independent restore, retention and RPO/RTO. No external drive/cloud account, encryption key, policy value or paid service is selected here. A custom-format dump is not encryption; the retained rehearsal dump does not satisfy this gate.

The future process must produce new consistent recovery sets covering PostgreSQL payloads/receipts/checkpoints and required external release/configuration/historical evidence. Pin code/schema/target identity, set IDs and hashes; encrypt new copies, test tamper rejection/decryption, obtain explicit transfer authorization, and prove independent retrieval and restore to **new empty** database/filesystem destinations. Reapply and verify intended owners/grants as a separate reviewed step when the dump omits them. Measure complete recovery against approved objectives. Never overwrite a database, delete an older backup or replay an old queue during recovery inspection.

Require real monitoring for heartbeat/start/finish, last success, database outages, disk capacity, failed imports, missing ACKs, backup freshness/integrity and alert-delivery failure. Verify actual receipt by the approved recipient and escalation owner through a channel independent of PostgreSQL. Approve private log readers, safe rotation and retention; the scheduler's append-only log is not an installed rotation service. Prototype evidence and local simulations do not become deployed monitoring by changing an approval flag.

## Safe release, rollback and activation boundary

Before a future release, approve Windows service account/startup/recovery, NTFS remediation, power/sleep/wake/firewall decisions, exact runtime dependencies and source scope/year. Preserve rollback ACL metadata and verify access as the actual identities. Do not copy a generic inheritance-removal command or enable the existing Haryana example wholesale.

For rollback, first disable only the approved trigger, quiesce/reap owned writers, preserve committed data/receipts and sanitized failure evidence, and select a reviewed compatible release. Repeat identity/admission and missing-ACK review before restart. If data recovery is required, restore a coordinated verified set to new destinations and obtain an approved reconciled cutover. Do not drop successful records, rewind sequences, reset checkpoints or fabricate ACKs to force a clean state.

The schedule remains daily 23:00 Asia/Kolkata. An eventual external trigger would invoke an approved supervised `--once` plan; no Windows task/service is created or changed here. Existing internal scheduling selects a strictly future occurrence, has no automatic catch-up and provides only in-process daily deduplication. A new missed-run/wake/retry policy requires separate approval. HTTP 429, CAPTCHA and authentication restrictions continue to stop without bypass or automatic scope expansion.

**Section 14B outcome: reviewable preparation only. Production provisioning: NOT AUTHORIZED. Production credentials created: NO. Production scheduler enabled: NO.** Unresolved decisions in the approval matrix must remain visible; no test result or template grants authorization to provision or operate production.

## Offline preparation verification

On 22 November 2026, focused configuration/security review passed 39 tests and 219 subtests. The full offline regression suite passed 392 tests and 618 subtests, with zero failures and 43 PostgreSQL cases deliberately skipped because no dedicated database target was authorized for this phase. Three PowerShell procedure examples passed syntax parsing without execution. The test runner recorded zero unmocked database/network attempts; original settings, application/schema files, `.env` and the existing scheduler log remained unchanged.

The SQL review checked guard order, stage bindings, fresh-object checks, quoted inputs, schema/key/JSONB contracts and proposed grant boundaries. This is static review, not server parsing or execution. Separately authorized disposable SQL execution, negative-case testing and real TLS/login acceptance remain prerequisites before production use. No production identity, credential, TLS exception or approval was created by these tests.
