# Local Windows deployment preparation

The selected deployment target is the local Windows PC at `E:\GITHUB\PROJECTS\Web-Scrapping`. **Production activation remains blocked.** Section 13C prepares a deployment; it does not create a production database/login, install monitoring, register a task, change system settings or permit a live run. Keep `scheduler.enabled=false` and `database.enabled=false` in the original configuration.

The confirmed schedule is daily **23:00 Asia/Kolkata (11 PM; 17:30 UTC)**. This supersedes the 02:00 wording in Section 13C's original phase 6. Existing source/location/year filters, database-only admission, hard supervision and authorization safeguards remain in force. The existing enabled Haryana SARAS/PIN 134001 example is not an approved production scope; never enable the original configuration wholesale.

Use this document with [production safety](PRODUCTION_READINESS.md), [database-only operation](database_only.md) and the deliberately unapproved [profile template](../config/production_safety.example.json). Historical recovery runbooks and evidence remain unchanged.

## Evidence and readiness boundaries

The Windows readiness CLI inspects prerequisites without connecting to PostgreSQL or changing Windows settings. Its configuration checks establish only whether approved identity settings and readiness evidence are present; they do not verify a live production login, HBA, TLS certificate or database identity. The existing production preflight performs the separately authorized read-only database verification.

The checker currently accepts the reviewed 64-bit Python 3.14.x runtime. It checks installed requirement declarations and browser files without launching a browser. `preparation_status` can pass when the supplied preparation evidence is complete; the overall status remains BLOCKED, exit code remains 2, and activation stays false because this tool never performs the required live database preflight. A different Python version needs compatibility review rather than an invented successful result.

```powershell
# Read-only deployment inventory; no source or database connection.
& .\venv\Scripts\python.exe -B -m school_scraping.windows_readiness

# Existing offline command preview; no browser, database, lock or school payload.
& .\venv\Scripts\python.exe -B .\daily_scheduler.py `
  --config .\config\states_pincodes.json --dry-run
```

Retain the generated safe report with its observation time. Report inaccessible commands/services/ACLs as unknown or blocked; absence of permission is not proof of an absent prerequisite. Never dump process environments, task action arguments, service command lines, session files or `.env` contents into the report.

The project facts verified by implementation review are:

| Item | Verified fact / boundary |
|---|---|
| Project location | `E:\GITHUB\PROJECTS\Web-Scrapping` |
| Application schedule | Daily 23:00, explicitly `Asia/Kolkata`; no clock/timezone adjustment |
| Original configuration | Scheduler/database disabled; unrelated Haryana example retained |
| Storage | Files remain the legacy default; scheduled database-only collection requires explicit flags and approved admission |
| Process supervision | Windows PID-gated, isolated base-Python launcher joins a private kill-on-close Job Object before starting the real venv/source/browser tree |
| Existing PostgreSQL endpoints | Historical isolated clusters on 55439/55449 are protected evidence, not production assignments |
| Production routing/login/TLS | Not provisioned or verified by this preparation |
| Backup/monitoring | Existing readiness checker is a local prototype, not a deployed provider |

The read-only local audit on **21 November 2026** recorded:

| Component | Observed state | Deployment boundary |
|---|---|---|
| Windows | Windows 11 Home Single Language, version 10.0.26300/build 26300, 64-bit | Existing host, not a newly provisioned service environment |
| Python/venv | Python 3.14.6, 64-bit, project `venv`; base installation `C:\Python314` | Pin the reviewed interpreter/runtime when a release is approved |
| Dependencies/browser | `pip check` found no broken requirements; audited runtime packages satisfy the requirements; Chromium executable exists | No browser was launched; the actual unattended identity/session remains untested |
| PostgreSQL tools | `psql`, `pg_dump`, `pg_restore`, `pg_isready` version 17.5 under `C:\Program Files\PostgreSQL\17\bin` | Client availability is not production database identity or login verification |
| PostgreSQL services | `postgresql-x64-17` and `postgresql-x64-18` running, automatic startup | Existing services were not changed; neither is approved as the production target |
| Scheduler/firewall services | `Schedule` and `MpsSvc` running, automatic startup | No task/service was installed or activated |
| Firewall | Domain, Private and Public profiles enabled; default actions reported `NotConfigured` | This does not prove the effective inbound-rule policy; review it before provisioning |
| Disk | E: free 127,860,125,696 bytes (about 119.08 GiB); C: free 23,622,369,280 bytes (about 22 GiB) | Values are point-in-time; thresholds, backup sizing and reserve remain unapproved |
| Timezone | Windows `India Standard Time`, equivalent to application `Asia/Kolkata` | No clock/timezone change; retain explicit application timezone |
| Power | Balanced plan; AC sleep 300 seconds, battery sleep 180 seconds; AC wake timers enabled, battery wake timers disabled; battery hibernate 57,600 seconds | Current sleep settings block reliable unattended operation; do not change them without owner approval |
| Project tasks | No existing scheduled task targeting this project found | No trigger to update; creation remains separately unapproved |
| NTFS | Inherited Authenticated Users modify and Users read/execute grants observed on `.env`, project root, venv and logs | Runtime code/credentials/outputs require an approved effective-access review; no ACL changed |

These observations establish component availability only. They do not verify production access, login durability, source authorization, backup encryption/retrieval, delivered alerts or deployed supervision under the future runtime identity. A later audit must refresh them; do not infer current machine state from historical test reports.

The new [local deployment plan template](../config/windows_local_deployment.example.json) and production safety template are deliberately unapproved. Default Windows readiness returns **BLOCKED / exit 2**, with zero database connections and no child launch. Its optional `--project-root`, `--profile`, `--plan` and `--config` paths select inspection inputs; none authorizes an activation. Keep real secrets out of both templates.

## Approval record before provisioning

An operator must approve all of the following before a DBA or Windows administrator makes changes:

- Dedicated production database/cluster choice, explicit approved port other than 5432, database name/OID, expected major version, login, protected-target inventory and identity-verification authority. Do not select 55439/55449 or reuse a historical test database for production.
- Named Windows runtime identity, account/logon policy, secret provisioning, required filesystem access, source session ownership and an operator who can restore legitimate source access. Do not run the scraper as an administrator or PostgreSQL owner.
- Exact source/state/district/PIN scope, UDISE academic year, collection policy, candidate limit, source delay/retries, hard timeout and any approved attended authorization procedure. Do not infer approval from an enabled example location.
- PostgreSQL TLS/local-loopback policy, connection/statement/lock budgets and durability policy.
- Backup destination, encryption method/recipients, key custodians, retrieval test, retention, disk thresholds, RPO/RTO and change/release recovery requirements.
- Monitoring producer, heartbeat frequency, actual alert channel/recipient, escalation owner, log rotation/retention and notification-failure handling.
- Task trigger/principal, working directory, no-overlap policy, power/network conditions, outer execution limit, missed-run policy and shutdown acceptance test.

No production name, port, account, timeout, retention period or recovery objective is invented here. The prior pilot limits and older 24-hour/four-hour RPO/RTO proposals are evidence of proposals only.

## Dedicated PostgreSQL plan

1. An authorized DBA reviews the PostgreSQL 17 application/tool compatibility and provisions a **new approved production target**, separately from every retained test database. This document does not authorize creation. Keep loopback-only connectivity unless another topology is separately approved; do not open the firewall or start/stop a PostgreSQL service during readiness inspection.
2. Separate schema ownership/migration administration, the restricted scraper LOGIN, API readers and backup administrators. Test the real login/HBA and role memberships. The scraper requires the existing application table operations; it must not own the database/schema/tables or have superuser, CREATE, broad server-file/program/data roles, DELETE/TRUNCATE/TRIGGER or immutable-event UPDATE privileges. Existing schema contract checks are a minimum, not a review of every extension, RLS policy or security-definer function.
3. Apply reviewed DDL only to that verified new target, using the migration identity: `database/schema.sql`, `database/schema_school_tables.sql`, then `database/migrations/001_scraping_memory.sql`. Capture tool/version/target identity and errors, stop on an error, and review actual schema/constraints afterward. The scraper never applies migrations. No schema changes are needed merely to use the database-only path.
4. Choose certificate-verified TLS with a trusted root and matching host/IP identity, or record the existing profile's explicit `approved-local` loopback exception and reason. The latter is not encrypted transport and does not authorize remote access or test trust/admin credentials. `verify-full` verifies both the chain and server identity; `require` alone does not provide the same verification. See [PostgreSQL 17 TLS](https://www.postgresql.org/docs/17/libpq-ssl.html).
5. Approve positive connection, statement and lock timeouts, with lock timeout no greater than statement timeout. Production admission passes these and the TLS policy to the real child connector; disable dotenv loading. Supply credentials through the approved private process environment, never CLI arguments, a committed profile or ordinary logs. Do not modify the original `.env` to redirect production.
6. Verify durable commits rather than weakening persistence for speed. The intended production policy should preserve `fsync=on`, `full_page_writes=on` and synchronous WAL commit semantics, with appropriate storage/power-loss protection. These are DBA deployment checks, not settings changed by the readiness CLI. PostgreSQL explains the crash-recovery and acknowledged-commit implications in [WAL settings](https://www.postgresql.org/docs/17/runtime-config-wal.html).

After creation is separately authorized, an authorized operator may run an explicitly targeted read-only identity/durability inspection. `$Approved*` values below come from the approval record; do not substitute historical endpoints or allow a default connection. A credential must already be supplied privately; `-w` prevents prompting.

```powershell
# FUTURE, separately authorized read-only inspection; not run by Section 13C.
# These approved nonsecret settings affect this PowerShell process only.
$env:PGSSLMODE = $ApprovedLibpqSslMode
$env:PGCONNECT_TIMEOUT = [string]$ApprovedConnectionTimeoutSeconds
$env:PGOPTIONS = "-c default_transaction_read_only=on -c statement_timeout=$ApprovedStatementTimeoutMs -c lock_timeout=$ApprovedLockTimeoutMs"
# For verify-full, also supply the approved CA path as PGSSLROOTCERT.
& $ApprovedPsqlPath -X -w -v ON_ERROR_STOP=1 `
  --host $ApprovedDatabaseHost --port $ApprovedDatabasePort `
  --dbname $ApprovedDatabaseName --username $ApprovedInspectionRole `
  --command "BEGIN READ ONLY; SELECT current_database(), current_user, host(inet_server_addr()), inet_server_port(), (SELECT oid::text FROM pg_database WHERE datname=current_database()) AS database_oid, current_setting('server_version'), current_setting('fsync'), current_setting('full_page_writes'), current_setting('synchronous_commit'); ROLLBACK;"
```

Compare actual database/user/host/port/version/OID with the approval record. Optional data-directory/system-ID verification requires an authorized DBA when the restricted login lacks permission; do not grant broad server-file access merely to inspect metadata. Reject mismatches before write-capable startup. Test backup/restore and the real non-owner login in independently approved disposable fixtures before production admission is accepted.

## Future scoped startup, not activation

Create a separately reviewed collection configuration and private nonsecret safety profile only after approvals exist. Disable unrelated sources/locations in that copy; keep the original configuration and historical queues unchanged. The copy's scheduler flag and the profile's approval are separate gates. Fresh operational data/state/report/log paths must not refer to old import queues or recovered evidence.

The following are review templates, not approved runnable deployment values. An operator supplies all variables from the signed plan; no production connection is authorized by copying the template. First run the Windows audit and an offline dry-run. A later authorized `--preflight-only` uses read-only database checks and must PASS before a one-shot run is considered.

```powershell
# FUTURE SARAS admission only; no child is launched.
& $ApprovedVenvPython -B .\daily_scheduler.py `
  --config $ApprovedCollectionConfig --source saras --state $ApprovedState `
  --time 23:00 --timezone Asia/Kolkata `
  --storage-mode database --new-only --database `
  --limit $ApprovedCandidateLimit --delay $ApprovedDelaySeconds `
  --retries $ApprovedTransientRetries --child-timeout $ApprovedChildTimeoutSeconds `
  --data-dir $ApprovedDataDirectory --state-dir $ApprovedStateDirectory `
  --report-dir $ApprovedReportDirectory --log-file $ApprovedSchedulerLog `
  --production-profile $ApprovedSafetyProfile --preflight-only
```

Include the exact approved district/PIN options where applicable; omitting them is not permission to widen scope. UDISE additionally requires `--academic-year $ApprovedAcademicYear`, an authorized `--udise-school-list $ApprovedList` and any approved private headers path. The scraper verifies the year ID against the official source; the year is not guessed. The checked scope must exactly match an allowed job in the profile. Choose approved `--refresh` instead of `--new-only` only when that policy is authorized; never combine them.

Keep the authorized SARAS session path in the separately reviewed source configuration and test access as the actual runtime account. Approve `--headless` only when legitimate session access works without a visible challenge. A logged-out batch task is not an attended browser session. If current source access requires manual CAPTCHA or authentication, keep unattended operation blocked and arrange a separate attended procedure; do not solve, replay or bypass the challenge.

Only a separate explicit activation approval could replace `--preflight-only` with `--once` and enable the scoped configuration/profile. No live startup command, task or service is installed here. A successful skip or exit 0 is not proof of a new import; examine committed source/master/raw/canonical/yearly evidence, completed checkpoint and post-commit ACK separately.

## Windows Task Scheduler design

| Setting | Future reviewed design |
|---|---|
| Principal | Named restricted runtime account; approved batch-logon/session/secret policy; no blanket highest-privilege setting |
| Trigger | Once daily 23:00 Asia/Kolkata, equivalent 17:30 UTC; verify Windows trigger timezone and application timezone agree |
| Program | Exact approved `venv\Scripts\python.exe`; no shell activation or ambiguous PATH interpreter |
| Start in | Exact reviewed project/release directory |
| Arguments | Reviewed `daily_scheduler.py --once` plan with scoped configuration/profile, database-only flags, exact scope/year, bounds and private operational paths |
| No overlap | Task `MultipleInstancesPolicy=IgnoreNew` plus the application's existing same-root source/scheduler locks |
| Budgets | Approved `--child-timeout`, its bounded shutdown allowance and a separately reviewed outer task limit that permits cleanup; do not rely on Task Scheduler's broad default limit |
| Retries | No task restart/repeating trigger that automatically reattempts HTTP 429, failed authorization or a blocked preflight; operator-reviewed retry only |
| Power/network | Wake, AC/battery, sleep/hibernate, network and logged-on-session behavior require owner decisions and real-account tests; change none during audit |
| Output/exit | Preserve sanitized source reports and structured scheduler events; retain the actual nonzero failure/blocked/timeout exit status |

Microsoft documents [IgnoreNew](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-multipleinstancespolicy-settingstype-element) as suppressing another task instance while one is running. It does not provide application-wide daily-run deduplication or protect different data roots. Review the Windows task's [execution limit](https://learn.microsoft.com/en-us/windows/win32/taskschd/task-scheduler-schema) in addition to the application's tested owned-tree supervisor. No task registration/XML import/enable command is supplied or executed by this preparation.

The external trigger must invoke `--once`; that mode runs immediately rather than waiting for the application's next 23:00. The internal daily loop instead arms a strictly future occurrence: an already-running scheduler armed at 22:59 can trigger at 23:00; a first call/restart at or after 23:00 chooses the next day. In-process `last_run_date` prevents duplicate cycles only within that process; no persistent daily-run dedupe was added. Source completion checkpoints/committed identities still govern repeat detail work.

Missed-run catch-up remains unapproved. Windows [StartWhenAvailable](https://learn.microsoft.com/en-us/windows/win32/taskschd/tasksettings-startwhenavailable) permits delayed execution after a missed scheduled time; enabling it would change effective operating behavior. Do not set it, automatic wake or recovery triggers without explicit policy approval. An operator may review a missed run and request a separately approved bounded one-shot; do not manufacture a successful heartbeat for a run that never occurred.

## Monitoring, backup and local security

Before activation, install and test an approved producer/channel for scheduler heartbeat, missing start/finish, last successful execution, database connectivity, disk capacity, backup freshness/integrity, failed imports, missing ACKs and alert-delivery failures. A database outage must be reportable through a channel independent of PostgreSQL. Define the recipient, escalation/acknowledgement owner, thresholds and delivery-retest cadence. Local JSON evidence and simulated notifications are not delivered alerts; the supplied readiness prototype cannot by itself admit production.

The current scheduler defaults to `logs\daily_scheduler.log`, with source reports under `reports\scraping` and data under `data`. These existing locations and their contents are preserved. Approve separate operational output paths and NTFS access for the deployment identity; database-only school payloads stay in PostgreSQL, while sanitized operational reports/logs may still be local files.

Approve structured-log rotation by size/time, retention, private NTFS readers and disk thresholds for the actual data/log/backup volumes. `RunLog` appends; no deployed log-rotation service is implied. Rotate only through a reviewed writer-safe mechanism; preserve incident/recovery evidence and referenced legacy state. Do not delete backups, truncate a live journal or discard unresolved import evidence to regain space.

Use the existing `reconcile_imports()` read-only API only with a separately authorized idle connection and verified identity. It reports missing post-commit ACKs and inconsistent checkpoints/source evidence; truncation is not a clean bill of health. Do not replay imports, fabricate ACKs, edit checkpoints or implement automatic repair. A missing ACK can follow a successful commit, so preserve receipts and confirm durable data before deciding recovery action.

The encrypted/off-machine backup plan remains blocked until destination, encryption recipients/tool, key custody/recovery, access, retention and RPO/RTO are approved. Do not assume an external drive/cloud account exists. PostgreSQL custom-format compression is not encryption. Preserve DB-only JSONB/receipts/checkpoints and necessary configuration plus all historically required external evidence in a coherent recovery set; database-only operation does not make old raw files/journals disposable.

After separate authorization, create new exports, hash/manifest them, encrypt new copies, test decryption/tamper rejection, transfer only to the approved destination and prove independent retrieval. Keep recoverable keys separately under approved custody. Restore to a **new empty database and new filesystem directory**, verify rows/identities/relationships/snapshots/sequences/checksums/ACL requirements and read-only receipt reconciliation, then measure recovery against approved objectives. Never restore over an existing target, use destructive cleanup to manufacture one or replay old queues during a restore inspection.

An authorized Windows operator must review effective NTFS rights on the project/release, venv, operational outputs and private credential/session locations. Preserve ACL metadata privately, identify the actual runtime/human/SYSTEM/administrator accounts, prepare the intended ACL change and obtain approval before applying it. Remove broad inherited or explicit grants only after verifying required owners/operators retain access; test effective access as the real runtime account and retain a tested ACL rollback. Do not apply a generic `icacls /reset`, inheritance-removal or ACL replacement automatically. No credential contents or account secrets belong in evidence.

## Stop, rollback and troubleshooting

1. Disable only the approved trigger first. Identify the owned scheduler/source/browser tree, request graceful shutdown and let the existing Job Object supervisor enforce its bounded cleanup. Verify no owned descendants remain; never terminate Python/PostgreSQL processes by name or remove a live owner's lock.
2. Preserve reports, receipts, approved profiles, actual identities and last good recovery-set references. A timeout is a failed cycle even if some records committed; retain those records and their evidence.
3. For a release rollback, choose a reviewed compatible prior release and separate approved scoped configuration. Recheck runtime identity, source authorization, admission, backup freshness and missing ACKs before considering a restart. Do not change the original disabled flags to make a test pass.
4. If data recovery is necessary, restore a verified coordinated set into new destinations, reconcile without repairs and obtain explicit cutover approval. Do not delete successful new records, rewind sequences, reset checkpoints or overwrite historical databases.
5. For blocked readiness/preflight, correct the actual missing approval/component or stale proof; do not mark the JSON prototype deployed or fabricate evidence. For HTTP 429/CAPTCHA/authentication, stop and obtain legitimate access without automatic retries or bypass. For task failures, inspect actual principal/path/timezone, safe exit code, event sequence and source report; do not repeatedly trigger a blocked job.

Approval to prepare this Windows plan is not approval to provision, change ACLs/power/firewall/services, create credentials/keys, upload, schedule or scrape. Until the remaining deployment gates are evidenced and separately approved, keep the scheduler disabled and report readiness as **BLOCKED**.

## Preparation verification

On 21 November 2026, the focused offline run passed 191 tests and 300 subtests. The full offline suite passed 362 tests and 466 subtests, with zero failures and 43 PostgreSQL cases skipped because their dedicated DSNs were deliberately unset. Scheduler safety, mocked database-only source flows, actual in-memory Canonical v4 validation and simulated commit-before-ACK ordering were exercised; no PostgreSQL server was connected. The real read-only Windows audit confirmed runtime/client availability and reported sleep, NTFS and missing deployment approvals as blockers. Original configuration, Section 13B controls, `.env` and the existing scheduler log remained unchanged.

The subsequent [Section 14A PostgreSQL rehearsal](POSTGRESQL_LOCAL_SETUP.md) executed all 43 PostgreSQL cases against a new SCRAM-authenticated disposable cluster and verified a restricted application login. The full suite passed 414 tests with zero failures/skips. This resolves the earlier test-environment coverage gap; Windows power/ACL remediation and production identity, TLS, monitoring and backup approvals remain outstanding. The rehearsal cluster was stopped and retained, without changing historical databases or the original disabled configuration.
