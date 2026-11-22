# Production safety foundation

Section 13B implements admission checks, bounded supervision and read-only reports. **Production remains blocked.** The local Windows PC is now the selected deployment target; [Section 13C's Windows runbook](WINDOWS_LOCAL_DEPLOYMENT.md) records its audit and remaining approvals. A production database, backup destination, key custodian and alert recipient remain unapproved. The original scheduler and database flags remain false. The existing Haryana SARAS example remains enabled inside the original configuration; do not enable that configuration wholesale.

The configured daily application schedule is `23:00` (11 PM) in `Asia/Kolkata`, equivalent to `17:30` UTC. The shared configuration supplies the schedule unless an explicit CLI override is provided. This time change does not enable scheduling, register a Windows task or change missed-run handling. `--once` still requests an immediate cycle and requires the existing approval and admission checks.

This is the current operating reference for database-only scheduling. It supersedes the older file-mode command examples in retained Section 11 runbooks. Those recovery artifacts remain unchanged. Legacy file-mode recovery is still available and deliberately separate.

## Approval and commands

Use a separately reviewed configuration and `config/production_safety.example.json` as a **nonfunctional template**. Its approvals are false and its deployment-dependent values are null. Never put passwords, authorization headers, browser cookies or private keys into this profile. Supply credentials through the approved process environment/secret mechanism; production admission does not load `.env`.

The original configuration remains disabled. This command is safe and expected to return a BLOCKED report with exit code 2, without source requests, database connection, scheduler locks or child launch:

```powershell
python daily_scheduler.py --source saras --state CHANDIGARH --limit 1 `
  --storage-mode database --new-only --database --child-timeout 180 `
  --production-profile config/production_safety.example.json --preflight-only
```

The values above describe only the earlier bounded pilot, not an approved production schedule. `--dry-run` always remains offline, including when a profile is supplied; its output says production preflight was not executed. It does not prove that deployment is safe.

For an operator-approved deployment, use the reviewed profile and separate scope configuration. Supply `--academic-year YYYY-YY` for UDISE and an authorized listing/access path. The source still verifies the year ID against its official catalogue. Use `--new-only` to skip committed SARAS affiliations and UDISE school/year pairs before details. Choose `--refresh` instead for an approved intentional refresh; they cannot be combined. Existing skips still consume `--limit` and do not prove fresh PIN/geographic coverage. A repeatedly bounded prefix does not establish full-location coverage.

Database-only **scheduled jobs always require the production profile**, even if the flag was forgotten; rejection happens before legacy journals/replay and child launch. Direct scraper CLIs retain the existing opt-in behavior, so they remain write-capable maintenance commands requiring separate operator authorization. A file-mode `--database` command is not a substitute for database-only scheduling.

`--preflight-only` performs admission checks and exits without scheduling. A future approved one-shot invocation adds `--once`; it still requires the separate scheduler configuration to be enabled and the profile's scheduler approval. This task does not enable either. For continuous operation, admission is re-evaluated before each cycle; all planned jobs must pass before any starts. No internal retry loop bypasses a failed admission decision.

## Startup contract

The profile requires:

- An approval reference and explicitly approved scheduler timeout; exact allowed source/state/district/PIN scope and policy (`new-only` or `refresh`), plus UDISE academic year. All-state and unlimited operation are rejected.
- Explicit host, non-default approved port, database name, login name, database OID and major version. Original test targets and port 5432 are blocked. Maintain the protected-target inventory. Optional data-directory/system-ID checks fail closed if requested but unavailable; do not give the writer broad server-file privileges to inspect them.
- Schema contract 1 and canonical schema 4.0. Contract 1 checks existing required tables/columns, JSONB types and PK/FK/unique constraints; it is not a newly invented migration ledger and applies no DDL.
- A non-owner, non-superuser writer with only required privileges. CREATE, broad server/data roles, DELETE/TRUNCATE/TRIGGER and immutable-event UPDATE are rejected. API read access and backup administration should use separate identities. Production LOGIN/HBA credentials remain an operator deployment check; isolated administrator tests do not approve them.
- Positive connection, statement and lock timeouts, with lock timeout no greater than statement timeout. These are passed to the real child connector as `DB_CONNECT_TIMEOUT_SECONDS`, `DB_STATEMENT_TIMEOUT_MS`, `DB_LOCK_TIMEOUT_MS`; `PYTHON_DOTENV_DISABLED=1` prevents alternate file-based routing.
- Either certificate-verified TLS (`verify-full`, explicit trusted root certificate) or an explicitly recorded local-loopback exception with approval and reason. The preflight verifies the session is encrypted when TLS is required. A local exception is not permission to use test trust/admin settings for a remote deployment.
- Approved backup/monitoring readiness. Default production admission requires a deployed monitoring provider. The supplied readiness module is a local prototype and cannot approve production merely because somebody wrote a fresh JSON file. Its explicit prototype exception works only for approved `section13b_test_*` databases on the verified isolated endpoint.

Changing profile values or a CLI timeout cannot silently widen the approved timeout cap. Profile changes are reloaded each cycle. Credentials stay in the private child environment; reports contain status/error codes, not the environment or raw connection exceptions.

## Supervision and shutdown

`--child-timeout SECONDS` is mandatory for production admission (it may be taken from the approved profile). A child reaching the deadline is a failed job even if it returns zero during shutdown. The execution budget is followed by bounded graceful shutdown and forced cleanup allowances (currently 5 and 3 seconds). Do not describe the budget alone as the total shutdown wall time.

On Windows, an isolated standard-library base-Python gate joins a private kill-on-close Job Object before it can launch the real venv interpreter. Timeout/cancellation terminates only that owned tree, including browser descendants. Failed containment or cleanup cannot report success. On POSIX, a separate process group is used; descendants deliberately creating new sessions can escape that group. Any future POSIX/cloud deployment needs its own cgroup/service containment tests before approval. Windows containment evidence does not certify a cloud host.

HTTP 429 and CAPTCHA/authentication restrictions continue to stop through the existing source safeguards. No solver, repeated access attempt, automatic scope expansion or retry loop is introduced. Source retry/backoff remains separate from process supervision. The scheduler never manufactures successful source checkpoints after timeout; independently committed records remain valid and are found on rerun.

To stop a future deployment: disable the approved trigger first, request graceful shutdown, let the reviewed supervisor enforce its bounded cleanup, verify no owned descendants remain, then preserve all reports/state. Never kill every Python/PostgreSQL process or remove another owner's lock. Check admission and unresolved receipts before restart. No supervisor/service/trigger is installed by this task.

## Read-only ACK reconciliation

`school_scraping.ack_reconciliation.reconcile_imports(connection, source=None, run_id=None, limit=1000)` accepts an independently approved **idle** database connection. It opens its own repeatable-read, read-only transaction and reports committed completions lacking a valid linked acknowledgement, inconsistent imported checkpoints, and inconsistent source/master/archive/yearly evidence. It verifies request/completion/ACK linkage and ordering. It never connects on import, repairs checkpoints, replays imports or fabricates ACKs.

Supply a connection only after an operator independently verifies its endpoint/identity and read-only inspection authorization. Do not connect to production simply to try this API. Tests use only newly provisioned disposable databases.

```python
from school_scraping.ack_reconciliation import reconcile_imports

# approved_idle_connection is supplied by the reviewed operator/test harness.
report = reconcile_imports(approved_idle_connection, source="saras", limit=1000)
# Review report; there is no apply/repair switch.
```

The report covers the selected completed import receipts and imported/completed checkpoint claims, not all historical legacy import styles. Truncation blocks PASS: partition a large inspection by approved source/run or develop reviewed pagination, rather than interpreting a bounded sample as healthy history. A missing ACK after data commit does not mean the school is absent. Validate durable data and evidence before considering any separately approved recovery implementation.

## Monitoring and readiness evidence

`school_scraping.readiness.check_readiness(settings)` is read-only and explicitly returns `mode=prototype`, `deployed_monitoring=false`. It performs no heartbeat delivery, uploads, encryption, database connection or alert registration. Hash pinning proves agreement with an operator-pinned digest, not authenticity or actual deployment of the evidence producer.

The profile's readiness settings require an explicit approval reference, absolute evidence root, relative evidence file, SHA-256 pin, age thresholds, disk thresholds and maximum error counters. Evidence and backup artifacts must be ordinary files confined to that root; redirected/traversal paths, changed checksums, future/naive timestamps, missing settings and stale proofs block PASS. It measures actual available disk capacity and verifies backup bytes/size/SHA.

Version 1 evidence is produced by an approved future monitor, not filled with invented successful values:

```json
{
  "version": 1,
  "observed_at": null,
  "heartbeat_at": null,
  "last_success_at": null,
  "database": {"status": null, "checked_at": null},
  "failed_imports": null,
  "missing_acknowledgements": null,
  "alert_delivery_failures": null,
  "alert_delivery_tested": false,
  "alert_delivery_tested_at": null,
  "backup": {
    "path": null, "sha256": null, "size_bytes": null,
    "completed_at": null, "encrypted": false,
    "encryption_verified_at": null,
    "off_machine_verified": false, "off_machine_verified_at": null
  }
}
```

Mandatory configurable checks cover scheduler heartbeat, last success, recent connectivity evidence, disk capacity, failed imports, unresolved ACKs, alert delivery failures and the recency/integrity of a completed backup. Encryption, off-machine retrieval/recovery and alert-delivery attestations have their own mandatory proof times; fresh metadata cannot revive stale tests. The preflight separately makes its own real read-only database identity/schema/role/TLS check. A future producer must reconcile database counters and independently test alert delivery; self-reported evidence is insufficient to mark this prototype deployed.

Choose an external health channel that can report a database outage even when PostgreSQL event logging is unavailable. Approve heartbeat cadence, log rotation/retention, disk thresholds, alert destination, acknowledgement/escalation owner and notification failure handling. No provider, account or paid service is chosen here.

## Encrypted backup and off-machine recovery plan

1. Approve scope, destination, retention, RPO/RTO, identities, encryption tool/recipients and key custody. The older 24-hour/4-hour objectives and retention generations remain proposals.
2. Define a consistent database recovery cut, including operational receipts/checkpoints and required external configuration/legacy evidence. Database-only school payloads live in PostgreSQL; retain historical raw files and legacy journals under their existing preservation policy.
3. Produce new recovery sets in approved private export paths. Record commit/runtime/schema identity, set ID, time, hashes and membership. Never overwrite old backups or prune shared historical dependencies.
4. Use approved authenticated encryption on new copies and verify decryption and tamper rejection. Compression or `pg_dump -Fc` alone is not archive encryption. Do not generate or copy keys until custody and access are approved; store recoverable keys separately from encrypted data.
5. Transfer only after explicit destination/upload authorization. Verify retrieval, digest and decryption from an independent host and restricted recovery identity.
6. Restore into new empty destinations, verify complete row/file/relationship/snapshot/sequence integrity, and reconcile receipts without replaying old queues. Record ACL/ownership recovery and timing against approved objectives.
7. Automate backup freshness, capacity and alert gates only after acceptance; implement retention with a reviewed dry-run inventory and operator approval. Runtime scraper privileges must not include backup deletion/administration.

No keys, encryption jobs, uploads, paid services, retention deletions or backup schedules are created here. Existing local artifacts are not relabeled encrypted/off-machine-ready.

## NTFS and secret handling

The read-only audit found inherited broad Users read and Authenticated Users modify allows on `.env`. They have **not** been changed automatically. An authorized operator should first inspect `Get-Acl -LiteralPath .env`, preserve its ACL metadata in an approved private location, identify the actual human/service accounts, and prepare a replacement ACL for review. Remove broad inherited/explicit grants only after checking that the owner, service, SYSTEM and designated administrators retain the intended access. Apply with explicit approval, verify effective access as those accounts, and retain a tested ACL rollback copy. Do not apply a generic username or inheritance-removal command blindly.

The expanded `.gitignore` protects environment variants, keys/certificates, authentication state, credential exports and runtime backups. Sanitized `.env.example`/`.env.template` remain visible. Active Python, SQL migrations, tests, docs and the public profile template remain reviewable. Public certificate fixtures can be deliberately reviewed and force-added; never force-add a real private key. Ignore rules do not remove already tracked secrets or replace ACLs/secret management. If a leak is confirmed, rotation and history remediation require a separate approved incident procedure.

## Deployment decisions and rollback

The local Windows PC has been selected; approve the operator and service model before installation. It needs reliable power/network, wake/catch-up policy and a legitimate attended access path. Any later cloud move needs its own tested Python/browser/OS provisioning, network/database controls and deployment-specific descendant containment; it does not eliminate CAPTCHA/manual authorization requirements. Approve sources, years, coverage and refresh cadence independently of infrastructure.

Before release, pin the reviewed commit and reproducible dependencies/browser runtime; verify a new compatible recovery set and admission in the chosen environment. Start only with an approved bounded one-shot plan and supervised shutdown. To roll back, stop the trigger and all owned writers, preserve failure evidence and committed receipts, select a reviewed compatible prior release, and re-run admission before any restart. Do not restore over an existing database, delete successful new records, reset checkpoints or replay historical queues to force a clean result. If data recovery is necessary, restore a new destination and perform an approved reconciled cutover. No schema change is introduced by this foundation.

Remaining blockers: approved production identity and real production LOGIN/TLS verification; restricted secret ACLs and Windows power policy; a deployed monitoring provider and real alerts; encrypted off-machine backup/recovery and retention approval; target-specific supervision; refreshed source authorization/discovery/coverage; and API deployment/view/access policy if included. [Section 14A](POSTGRESQL_LOCAL_SETUP.md) subsequently ran all 43 previously skipped PostgreSQL cases in a new SCRAM-only disposable cluster, including the 25 legacy cases. Core tests and isolated checks do not approve production activation.

## Foundation verification

The Section 13B focused run passed 168 tests and 237 subtests with zero failures/skips. The full offline regression run passed 338 tests and 378 subtests, with zero failures and 25 deliberately disabled legacy PostgreSQL cases. All four new guarded catalog/ACK cases and the 14 database-only PostgreSQL cases ran against newly provisioned disposable databases on the verified isolated restore cluster. The catalogue case rejects the administrator; positive least-privilege/TLS policy behavior is mocked, so it does not establish a production LOGIN or certificate deployment.

Harmless real Windows processes verify timeout/descendant termination, preservation of an unrelated owned tree, and isolation from a shadow `json.py` before gate release. No source site is used. Evidence is retained under `.refactor_validation/section13b_safety_foundation/`; that location is intentionally excluded from Git. Read-only reconciliation is not an automatic repair or a complete forensic integrity audit. The schema/role gate is a minimum application contract; deployment review must also examine RLS, extensions, security-definer functions and role memberships in the selected environment.

The later Section 14A rehearsal also verified a real restricted SCRAM login and both source preflights against its new database. Monitoring readiness was explicitly injected as a test fixture, and transport used a documented local-loopback exception. These results do not verify production monitoring, backups or certificates. Archive-history preservation remains application enforced; direct SQL history UPDATE is not separately prohibited by the current DDL.
