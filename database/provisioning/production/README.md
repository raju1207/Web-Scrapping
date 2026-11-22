# Future guarded PostgreSQL provisioning templates

These files are preparation artifacts. **No production identity or execution is approved.** They have not been executed or server-parsed in Section 14B. Static review/tests do not establish runtime SQL acceptance. The current example profiles remain unapproved, and the scheduler/database flags remain disabled at 23:00 Asia/Kolkata.

Use these only after the operator separately approves a new dedicated PostgreSQL 17 local installation, endpoint, data directory/system identifier, bootstrap administrator, database, role names, TLS certificates, source scope, recovery and monitoring arrangements. They never initialize/start a cluster, install a service, change HBA/firewall/ACLs, create passwords, run a scraper or approve a production profile.

| File | Future stage |
|---|---|
| `bootstrap.sql` | Guard the approved fresh maintenance connection; create four new password-disabled roles transactionally, then create a new database outside that transaction and restrict its connection privileges. |
| `migrate.sql` | Guard the new database/OID and empty application namespace, then use `SET ROLE` to apply the existing three DDL files in one pinned administrator session. |
| `privileges.sql` | Guard the reviewed owner/schema and empty tables, then apply initial minimum grants transactionally. |
| `verify.sql` | Default read-only final verification; also supplies explicitly named stage-specific prechecks to the other three wrappers. No schema, role, data or grant writes. |

Do not run the original DDL files directly as a substitute for `migrate.sql`. It includes `../../schema.sql`, `../../schema_school_tables.sql`, then `../../migrations/001_scraping_memory.sql`. Each retains its original BEGIN/COMMIT boundary; the entire migration is not one atomic transaction. Administrator `SET ROLE` proves effective DDL ownership, not successful authentication as the migration LOGIN. Test that LOGIN/HBA/TLS separately after secure password provisioning.

## Required nonsecret bindings

Supply psql variables using `--set name=value`. There are no approved value defaults. Never put passwords, password verifiers, secret connection strings or private keys into these variables, source files, command arguments or logs.

- Every stage: `provisioning_approved` must literally be `yes`; nonempty `approval_reference`; `approved_host` must be `127.0.0.1`; explicit `approved_port`, `approved_server_major` (`17`), `approved_system_id`, `approved_data_directory`, `approved_admin`, `approved_database`, `migration_role`, `scraper_role`, `report_role`, `backup_role`, `tls_policy` (`verify-full`).
- Bootstrap: `approved_maintenance_database` and its independently verified `approved_maintenance_oid`. The proposed database and all four role names must be absent. The maintenance cluster must otherwise be fresh: no other user databases/roles or application namespaces/tables.
- Migrate, privileges and final verify: `new_database_oid`, captured from successful bootstrap and independently reviewed before any application DDL. Do not guess or force a database OID before creation.
- Actual client environment: `PGSSLMODE=verify-full`, explicit existing trusted `PGSSLROOTCERT`, positive connection/statement/lock timeouts, and a separately approved private authentication mechanism. No dotenv routing or public test credential is appropriate.

The portable new database/role names are lowercase ASCII identifiers of at most 63 characters; role names must be distinct from each other and from the bootstrap administrator. Historical/test names, `.refactor_validation` data directories and ports 5432, 55439, 55449 and 59487 are rejected. The server must match the approved host/port/major/system ID/data directory and administrator, use TLS and loopback-only listening, and show enabled fsync/full_page_writes/synchronous_commit. Non-reject TCP HBA entries must be hostssl/SCRAM on the exact IPv4 loopback address/mask. HBA load/application and real client authentication still require independent operator acceptance.

Presence checks use psql's Boolean `:{?variable}` expansion; all supplied SQL values use `:'variable'` and identifiers use `:"variable"`. Boolean comparisons are performed with SELECT and `\gset`, not expressions in `\if`. Guard failure intentionally raises a fixed, nonsensitive SQL error with ON_ERROR_STOP enabled. No `\quit 3` behavior or interpolation inside dollar-quoted DO bodies is relied on. See [psql 17](https://www.postgresql.org/docs/17/app-psql.html).

## Pre-client safety check and future invocation pattern

SQL guards run **after** the first connection. Before starting psql, the reviewed launcher must reject unset/non-loopback settings, protected ports, routing overrides and missing client TLS files. The following is deliberately inert until all null values are supplied from a separately approved plan. It is a future pattern, not an instruction to execute during Section 14B. Review the executable and all paths/ownership/ACLs separately; do not copy a rehearsal identity.

```powershell
$Psql = $null                 # Approved PostgreSQL 17 psql.exe, absolute path.
$TrustedCa = $null            # Approved trusted public CA file, absolute path.
$ConnectSeconds = $null
$StatementMilliseconds = $null
$LockMilliseconds = $null
$SqlVariables = @{
    provisioning_approved = $null; approval_reference = $null
    approved_host = $null; approved_port = $null; approved_server_major = $null
    approved_system_id = $null; approved_data_directory = $null
    approved_admin = $null; approved_database = $null
    migration_role = $null; scraper_role = $null; report_role = $null; backup_role = $null
    tls_policy = $null
    approved_maintenance_database = $null; approved_maintenance_oid = $null
    new_database_oid = $null
}

function Get-ApprovedPsqlArguments([string]$Stage) {
    if ($Stage -notin @('bootstrap','migrate','privileges','verify')) { throw 'Unknown future stage' }
    $required = @('provisioning_approved','approval_reference','approved_host','approved_port',
        'approved_server_major','approved_system_id','approved_data_directory','approved_admin',
        'approved_database','migration_role','scraper_role','report_role','backup_role','tls_policy')
    $required += if ($Stage -eq 'bootstrap') {
        @('approved_maintenance_database','approved_maintenance_oid')
    } else { @('new_database_oid') }
    foreach ($key in $required) {
        if ([string]::IsNullOrWhiteSpace([string]$SqlVariables[$key])) { throw 'Required approved binding missing' }
    }
    if ($SqlVariables.provisioning_approved -cne 'yes' -or
        $SqlVariables.approved_host -cne '127.0.0.1' -or
        [string]$SqlVariables.approved_server_major -cne '17' -or
        $SqlVariables.tls_policy -cne 'verify-full') { throw 'Approval or loopback/TLS policy missing' }
    $portNumber = 0
    if ([string]$SqlVariables.approved_port -notmatch '^[0-9]{1,5}$' -or
        -not [int]::TryParse([string]$SqlVariables.approved_port, [ref]$portNumber) -or
        $portNumber -lt 1 -or $portNumber -gt 65535 -or
        $portNumber -in @(5432,55439,55449,59487)) { throw 'Unapproved database port' }
    if ((Test-Path Env:PGSERVICE) -or (Test-Path Env:PGHOSTADDR)) { throw 'Routing override must be removed and reviewed' }
    $identifierKeys = @('approved_admin','approved_database','migration_role','scraper_role','report_role','backup_role')
    if ($Stage -eq 'bootstrap') { $identifierKeys += 'approved_maintenance_database' }
    foreach ($key in $identifierKeys) {
        if ([string]$SqlVariables[$key] -notmatch '^[a-z_][a-z0-9_]{0,62}$') {
            throw 'Approved identifiers only; connection strings and URIs are forbidden'
        }
    }
    if (-not $Psql -or -not [IO.Path]::IsPathRooted($Psql) -or
        -not (Test-Path -LiteralPath $Psql -PathType Leaf) -or
        -not $TrustedCa -or -not [IO.Path]::IsPathRooted($TrustedCa) -or
        -not (Test-Path -LiteralPath $TrustedCa -PathType Leaf)) { throw 'Approved executable or CA missing' }
    foreach ($value in @($ConnectSeconds,$StatementMilliseconds,$LockMilliseconds)) {
        if ([string]$value -notmatch '^[1-9][0-9]*$') { throw 'Positive timeout approval required' }
    }
    if ([long]$LockMilliseconds -gt [long]$StatementMilliseconds) { throw 'Lock timeout exceeds statement timeout' }
    $env:PGSSLMODE = 'verify-full'
    $env:PGSSLROOTCERT = $TrustedCa
    $env:PGCONNECT_TIMEOUT = [string]$ConnectSeconds
    $env:PGOPTIONS = "-c statement_timeout=$StatementMilliseconds -c lock_timeout=$LockMilliseconds"
    $database = if ($Stage -eq 'bootstrap') { $SqlVariables.approved_maintenance_database } else { $SqlVariables.approved_database }
    $arguments = @('-X','-w','--host',$SqlVariables.approved_host,'--port',[string]$portNumber,
        '--username',$SqlVariables.approved_admin,'--dbname',$database,'--set','ON_ERROR_STOP=on')
    foreach ($key in $required) { $arguments += @('--set',($key + '=' + [string]$SqlVariables[$key])) }
    return $arguments
}

# Future reviewed execution only, one selected stage at a time:
# $arguments = @(Get-ApprovedPsqlArguments -Stage 'bootstrap')
# & $Psql @arguments --file database/provisioning/production/bootstrap.sql
# if ($LASTEXITCODE -ne 0) { throw 'Provisioning stopped; preserve partial-state evidence' }
```

Select the guarded file matching the stage; do not pass `--single-transaction`/`-1`, an outer BEGIN, a different database/role, a raw shell-built command or a password argument. The executable wrapper overwrites `provisioning_stage`; standalone `verify.sql` defaults to the full `verify` stage. Stage precheck readback is **not** the final post-grant/SCRAM verification. Do not bypass a failed stage by setting another stage manually.

Use a fresh batch psql connection for each selected stage, as in the invocation pattern. Do not include these files into an interactive session with pending work: their explicit transaction boundaries are not a safe interface for a caller-owned transaction.

Actual server TLS observation cannot prove the client's CA/hostname verification or a loaded HBA policy. Independently verify the certificate chain/matching hostname, actual libpq invocation, new-cluster identity, applicable loaded HBA rules and positive/negative restricted login behavior. No broad pg_monitor/server-file grants are needed for the application: privileged metadata is checked by the separately approved bootstrap administrator.

## Role and grant boundary

`migration_role` and `scraper_role` are LOGIN roles created with PASSWORD NULL. Reporter and backup roles are NOLOGIN, without members. All four are NOSUPERUSER/NOCREATEDB/NOCREATEROLE/NOINHERIT/NOREPLICATION/NOBYPASSRLS. No administrator/migration/report/backup membership is granted to the scraper. The migration role owns the new database/schema/objects; the scraper owns none.

The scraper receives schema USAGE and SELECT/INSERT/UPDATE on nine application tables. `scrape_events` receives SELECT/INSERT only: UPDATE, DELETE, TRUNCATE, TRIGGER and unnecessary DDL remain unavailable. Final review also rejects unnecessary REFERENCES and PostgreSQL 17 MAINTAIN privileges for runtime/read-only roles. Existing identity INSERTs need no scraper sequence privileges. Backup metadata gets SELECT on the currently reviewed sequences only, with no sequence UPDATE/USAGE and no automatic future-object grants. Any future serial/default-nextval or migration changes require an explicit grant review.

Report and backup groups receive SELECT on all ten internal tables, including original raw JSONB, operational payloads and contact data. They are **not** a public/API or redacted reporting interface. Approve data scope and actual identities before granting any future login membership. A backup group is not an encrypted backup service, approved off-machine destination or key custodian.

After separate credential authorization, provision each required password through an approved private mechanism (for example psql's interactive `\password`, without a password command argument or checked-in SQL). PASSWORD NULL is deliberate; do not replace it with a sample secret. Default final verification checks only Boolean SCRAM-verifier presence and never prints a verifier. A Boolean verifier check is not a successful restricted authentication/TLS test. See [CREATE ROLE 17](https://www.postgresql.org/docs/17/sql-createrole.html).

## Failure, history and future migrations

Use an exclusive approved provisioning window. The role transaction can commit before CREATE DATABASE: PostgreSQL [does not permit CREATE DATABASE inside a transaction](https://www.postgresql.org/docs/17/sql-createdatabase.html). Database creation or a later DDL file may fail after an earlier stage committed. Stop and preserve the partial state; these are fresh-only templates, not general rerun/migration/repair scripts. Never auto-drop roles/databases, reinitialize a directory, weaken a guard or overwrite existing data to force a retry.

The unchanged schema enforces source identity `(source_system,region,source_record_id)`, archive linkage and requested-year uniqueness `(source_record_pk,academic_year)`. Raw source identifiers have ordinary indexes, not new global unique constraints. The existing application protects history through content-addressed preservation and pipeline logic. Its required archive UPDATE grant does not enforce row-level history immutability against arbitrary SQL; no history trigger/RLS policy is introduced here. Preserve that remaining boundary explicitly.

Future migrations require a new reviewed recovery set, schema/ownership/constraint and grant review, restricted-role retest and independently approved admission. No ALTER DEFAULT PRIVILEGES blanket writer grant is provided. Keep all production profile approvals false until actual deployment, monitoring, backup/recovery and source-access acceptance is complete.
