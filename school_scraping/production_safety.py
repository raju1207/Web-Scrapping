"""Fail-closed, read-only approval gate for supervised database-only jobs.

No deployment is approved by this module. Callers provide an operator-reviewed
profile and a readiness checker; environment values never enter the report.
Schema contract 1 describes the existing DDL, not a migration-ledger table.
"""
import math
import os
from pathlib import Path

from psycopg.rows import dict_row


TABLES = ('json_file_archive', 'school_entities', 'school_source_records',
    'school_yearly_snapshots', 'school_match_candidates', 'scrape_runs',
    'scrape_tasks', 'scrape_events', 'scrape_errors', 'scrape_checkpoints')
JSONB_COLUMNS = {'json_file_archive': 'payload', 'school_source_records': 'entity_resolution',
    'school_yearly_snapshots': 'snapshot_payload', 'school_match_candidates': 'evidence',
    **{name: 'payload' for name in TABLES if name.startswith('scrape_')}}
REQUIRED_COLUMNS = {
    'json_file_archive': 'file_id relative_path dataset_layer source_system region academic_year file_name content_sha256 file_size_bytes json_root_type raw_json payload file_modified_at imported_at updated_at',
    'school_entities': 'entity_id preferred_school_name region state district address pincode latitude longitude phone email website entity_status merged_into_entity_id created_at updated_at',
    'school_source_records': 'source_record_pk entity_id archive_file_id source_system region source_record_id school_name normalized_school_name udise_code cbse_affiliation_number state district address pincode latitude longitude phone email website entity_resolution created_at updated_at',
    'school_yearly_snapshots': 'snapshot_id source_record_pk source_archive_file_id academic_year total_students total_teachers student_teacher_ratio infra_score pass_percentage_x pass_percentage_xii schema_version source snapshot_payload imported_at',
    'school_match_candidates': 'candidate_id udise_source_record_pk saras_source_record_pk match_status match_method name_match pincode_match address_match website_match evidence review_notes reviewed_by reviewed_at created_at updated_at',
    'scrape_runs': 'run_id source scope_key status started_at finished_at last_event_at payload',
    'scrape_tasks': 'task_id run_id source academic_year school_identifier scope_key status attempts next_retry_at last_event_at payload',
    'scrape_events': 'event_id run_id source kind occurred_at received_at payload',
    'scrape_errors': 'error_id run_id task_id source error_category error_message sanitized_traceback occurred_at payload resolution_status resolved_at resolution_notes retry_count',
    'scrape_checkpoints': 'source academic_year school_identifier scope_key checkpoint_status last_detail_check last_successful_check last_successful_save next_check_at last_known_content_hash raw_path imported last_event_at payload'}
KEYS = {
    ('json_file_archive', 'u', ('relative_path',)),
    ('school_source_records', 'u', ('archive_file_id',)),
    ('school_source_records', 'u', ('source_system', 'region', 'source_record_id')),
    ('school_yearly_snapshots', 'u', ('source_record_pk', 'academic_year')),
    ('school_match_candidates', 'u', ('udise_source_record_pk', 'saras_source_record_pk')),
    ('scrape_checkpoints', 'p', ('source', 'academic_year', 'school_identifier', 'scope_key')),
    *{(table, 'p', (column,)) for table, column in (
        ('json_file_archive', 'file_id'), ('school_entities', 'entity_id'),
        ('school_source_records', 'source_record_pk'), ('school_yearly_snapshots', 'snapshot_id'),
        ('school_match_candidates', 'candidate_id'), ('scrape_runs', 'run_id'),
        ('scrape_tasks', 'task_id'), ('scrape_events', 'event_id'), ('scrape_errors', 'error_id'))}}
FOREIGN_KEYS = {
    ('school_entities', ('merged_into_entity_id',), 'school_entities', ('entity_id',)),
    ('school_source_records', ('entity_id',), 'school_entities', ('entity_id',)),
    ('school_source_records', ('archive_file_id',), 'json_file_archive', ('file_id',)),
    ('school_yearly_snapshots', ('source_record_pk',), 'school_source_records', ('source_record_pk',)),
    ('school_yearly_snapshots', ('source_archive_file_id',), 'json_file_archive', ('file_id',)),
    ('school_match_candidates', ('udise_source_record_pk',), 'school_source_records', ('source_record_pk',)),
    ('school_match_candidates', ('saras_source_record_pk',), 'school_source_records', ('source_record_pk',)),
    ('scrape_events', ('run_id',), 'scrape_runs', ('run_id',)),
    ('scrape_tasks', ('run_id',), 'scrape_runs', ('run_id',)),
    ('scrape_errors', ('error_id',), 'scrape_events', ('event_id',)),
    ('scrape_errors', ('run_id',), 'scrape_runs', ('run_id',)),
    ('scrape_errors', ('task_id',), 'scrape_tasks', ('task_id',))}


def _positive(value, *, integer=False):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
        and math.isfinite(value) and value > 0 and (not integer or isinstance(value, int)))


def _scope(values):
    if not isinstance(values, (list, tuple)) or any(not isinstance(value, str) for value in values):
        raise ValueError('Invalid scope')
    return frozenset(part.strip().casefold() for value in values for part in value.split(',') if part.strip())


def _rows(connection, statement, parameters=()):
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(statement, parameters)
        return cursor.fetchall()


def _one(connection, statement, parameters=()):
    rows = _rows(connection, statement, parameters)
    if len(rows) != 1:
        raise ValueError('Unexpected catalog response')
    return rows[0]


def _configuration(args, source, profile, errors):
    if not isinstance(profile, dict) or type(profile.get('schema_version')) is not int or profile['schema_version'] != 1:
        errors.append('PROFILE_VERSION_UNSUPPORTED')
        return None
    if profile.get('approved') is not True:
        errors.append('PROFILE_NOT_APPROVED')
    if not isinstance(profile.get('approval_reference'), str) or not profile['approval_reference'].strip():
        errors.append('PROFILE_APPROVAL_REFERENCE_REQUIRED')
    if source not in ('saras', 'udise') or getattr(args, 'storage_mode', None) != 'database' or getattr(args, 'database', None) is not True:
        errors.append('EXPLICIT_DATABASE_ONLY_MODE_REQUIRED')
    if getattr(args, 'all_states', False) or getattr(args, 'unlimited', False) or getattr(args, 'sync_pending', False):
        errors.append('UNBOUNDED_OR_LEGACY_OPERATION_FORBIDDEN')
    if not _positive(getattr(args, 'limit', None), integer=True):
        errors.append('FINITE_CANDIDATE_LIMIT_REQUIRED')
    try:
        requested = (_scope(getattr(args, 'state', [])), _scope(getattr(args, 'district', [])), _scope(getattr(args, 'pincode', [])))
        year = getattr(args, 'academic_year', None) if source == 'udise' else None
        from school_scraping.datasets import academic_year
        if source == 'udise':
            academic_year(year)
        jobs = profile.get('allowed_jobs') or []
        policy = ('new-only' if getattr(args, 'new_only', False) and not getattr(args, 'refresh', False)
                  else 'refresh' if getattr(args, 'refresh', False) and not getattr(args, 'new_only', False) else None)
        matched = any(isinstance(job, dict) and job.get('source') == source
            and policy is not None and job.get('policy') == policy
            and (_scope(job.get('states', [])), _scope(job.get('districts', [])), _scope(job.get('pincodes', []))) == requested
            and (source != 'udise' or job.get('academic_year') == year) for job in jobs)
        if not requested[0] or not matched:
            errors.append('SOURCE_SCOPE_OR_YEAR_NOT_APPROVED')
    except (ValueError, TypeError):
        errors.append('SOURCE_SCOPE_OR_YEAR_NOT_APPROVED')
    scheduler = profile.get('scheduler') or {}
    if scheduler.get('approved') is not True:
        errors.append('SCHEDULER_NOT_APPROVED')
    if getattr(args, 'scheduler_enabled', None) is not True:
        errors.append('SCHEDULER_DISABLED')
    if not _positive(scheduler.get('hard_timeout_seconds')):
        errors.append('HARD_EXECUTION_TIMEOUT_REQUIRED')
    database = profile.get('database') or {}
    mandatory = ('host', 'port', 'name', 'user', 'major_version', 'database_oid')
    if any(not database.get(key) for key in mandatory):
        errors.append('DATABASE_IDENTITY_INCOMPLETE')
    if type(database.get('schema_version')) is not int or database['schema_version'] != 1 or database.get('canonical_schema_version') != '4.0':
        errors.append('SCHEMA_CONTRACT_NOT_APPROVED')
    port = database.get('port')
    if not _positive(port, integer=True) or port > 65535:
        errors.append('EXPLICIT_DATABASE_PORT_REQUIRED')
    host = str(database.get('host') or '').strip().casefold()
    name = str(database.get('name') or '')
    if not host or any(character.isspace() or character in ',/\\=' for character in host):
        errors.append('SINGLE_DATABASE_HOST_REQUIRED')
    if (port in (5432, 55439) or name.casefold() in ('postgres', 'template0', 'template1',
            'section10a_restored_postgres', 'section10b_scheduler_fresh')
            or name.casefold().startswith(('section11b_recovery', 'database_only_test_', 'section12a_', 'section13a_'))):
        errors.append('PROTECTED_DATABASE_TARGET')
    protected = profile.get('protected_targets')
    if not isinstance(protected, list) or not protected:
        errors.append('PROTECTED_TARGET_INVENTORY_REQUIRED')
    else:
        for target in protected:
            if not isinstance(target, dict):
                errors.append('PROTECTED_TARGET_INVENTORY_INVALID')
                break
            if (str(target.get('host', '')).casefold() in (host, '*') and target.get('port') == port
                    and (not target.get('name') or target['name'] == name)):
                errors.append('PROTECTED_DATABASE_TARGET')
                break
    timeouts = database.get('timeouts') or {}
    if not all(_positive(timeouts.get(key), integer=True) for key in ('connect_seconds', 'statement_ms', 'lock_ms')):
        errors.append('DATABASE_TIMEOUTS_REQUIRED')
    elif timeouts['lock_ms'] > timeouts['statement_ms']:
        errors.append('LOCK_TIMEOUT_EXCEEDS_STATEMENT_TIMEOUT')
    tls = database.get('tls') or {}
    if tls.get('mode') == 'verify-full':
        certificate = tls.get('root_certificate')
        if not isinstance(certificate, str) or not certificate or not Path(certificate).is_file():
            errors.append('TLS_ROOT_CERTIFICATE_REQUIRED')
    elif tls.get('mode') == 'approved-local':
        if (host not in ('127.0.0.1', '::1') or tls.get('approved_local_exception') is not True
                or not isinstance(tls.get('reason'), str) or not tls['reason'].strip()):
            errors.append('LOCAL_TLS_EXCEPTION_NOT_APPROVED')
    else:
        errors.append('TLS_POLICY_REQUIRED')
    expected_environment = {'DB_HOST': database.get('host'), 'DB_PORT': port,
        'DB_NAME': name, 'DB_USER': database.get('user')}
    if any(not os.getenv(key) or str(expected) != os.environ[key] for key, expected in expected_environment.items()) or not os.getenv('DB_PASSWORD'):
        errors.append('EXPLICIT_DATABASE_ENVIRONMENT_MISMATCH')
    return database


def _database_checks(connection, database, checks, errors):
    connection.execute('SET TRANSACTION READ ONLY')
    identity = _one(connection, "SELECT current_database() AS database_name,current_user AS user_name,"
        "host(inet_server_addr()) AS server_address,inet_server_port() AS server_port,"
        "current_setting('server_version_num')::int/10000 AS major_version,"
        "(SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid,"
        "(SELECT setting::bigint FROM pg_settings WHERE name='statement_timeout') AS statement_timeout_ms,"
        "(SELECT setting::bigint FROM pg_settings WHERE name='lock_timeout') AS lock_timeout_ms")
    if (identity['database_name'] != database['name'] or identity['user_name'] != database['user']
            or identity['server_port'] != database['port'] or identity['major_version'] != database['major_version']
            or str(identity['database_oid']) != str(database['database_oid'])
            or (database['host'] in ('127.0.0.1', '::1') and identity['server_address'] != database['host'])):
        errors.append('DATABASE_IDENTITY_MISMATCH')
        return
    if database.get('data_directory'):
        actual = _one(connection, "SELECT current_setting('data_directory') AS value")
        if Path(actual['value']).resolve() != Path(database['data_directory']).resolve():
            errors.append('DATABASE_DATA_DIRECTORY_MISMATCH')
            return
    if database.get('system_identifier'):
        actual = _one(connection, 'SELECT (pg_control_system()).system_identifier::text AS value')
        if actual['value'] != str(database['system_identifier']):
            errors.append('DATABASE_SYSTEM_IDENTIFIER_MISMATCH')
            return
    checks['database_identity'] = 'PASS'
    if (identity['statement_timeout_ms'] != database['timeouts']['statement_ms']
            or identity['lock_timeout_ms'] != database['timeouts']['lock_ms']):
        errors.append('DATABASE_TIMEOUTS_NOT_APPLIED')
    else:
        checks['database_timeouts'] = 'PASS'
    role = _one(connection, "SELECT r.rolsuper,r.rolcreatedb,r.rolcreaterole,r.rolreplication,r.rolbypassrls,"
        "pg_has_role(current_user,d.datdba,'MEMBER') AS owns_database,"
        "pg_has_role(current_user,n.nspowner,'MEMBER') AS owns_schema,"
        "has_database_privilege(current_user,d.oid,'CREATE') AS database_create,"
        "has_schema_privilege(current_user,n.oid,'CREATE') AS schema_create,"
        "(pg_has_role(current_user,'pg_read_server_files','MEMBER') "
        "OR pg_has_role(current_user,'pg_write_server_files','MEMBER') "
        "OR pg_has_role(current_user,'pg_execute_server_program','MEMBER') "
        "OR pg_has_role(current_user,'pg_write_all_data','MEMBER')) AS broad_write_or_server_access,"
        "has_schema_privilege(current_user,n.oid,'USAGE') AS schema_usage "
        "FROM pg_roles r JOIN pg_database d ON d.datname=current_database() "
        "JOIN pg_namespace n ON n.nspname=%s WHERE r.rolname=current_user", ('school_scraping',))
    if any(role[key] for key in ('rolsuper', 'rolcreatedb', 'rolcreaterole', 'rolreplication', 'rolbypassrls',
            'owns_database', 'owns_schema', 'database_create', 'schema_create', 'broad_write_or_server_access')) or not role['schema_usage']:
        errors.append('DATABASE_ROLE_NOT_LEAST_PRIVILEGED')
    tables = _rows(connection, "SELECT c.relname AS table_name,pg_has_role(current_user,c.relowner,'MEMBER') AS owned,"
        "has_table_privilege(current_user,c.oid,'SELECT') AS can_select,"
        "has_table_privilege(current_user,c.oid,'INSERT') AS can_insert,"
        "has_table_privilege(current_user,c.oid,'UPDATE') AS can_update,"
        "has_table_privilege(current_user,c.oid,'DELETE') AS can_delete,"
        "has_table_privilege(current_user,c.oid,'TRUNCATE') AS can_truncate,"
        "has_table_privilege(current_user,c.oid,'TRIGGER') AS can_trigger "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=%s AND c.relname=ANY(%s) AND c.relkind IN ('r','p')", ('school_scraping', list(TABLES)))
    if {row['table_name'] for row in tables} != set(TABLES):
        errors.append('REQUIRED_TABLES_MISSING')
    if any(row['owned'] or not row['can_select'] or not row['can_insert']
           or row['can_delete'] or row['can_truncate'] or row['can_trigger']
           or (row['table_name'] == 'scrape_events' and row['can_update'])
           or (row['table_name'] != 'scrape_events' and not row['can_update']) for row in tables):
        errors.append('DATABASE_TABLE_PRIVILEGES_UNSUITABLE')
    columns = _rows(connection, "SELECT c.relname AS table_name,a.attname AS column_name,t.typname AS type_name "
        "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "JOIN pg_type t ON t.oid=a.atttypid WHERE n.nspname=%s AND c.relname=ANY(%s) "
        "AND a.attnum>0 AND NOT a.attisdropped", ('school_scraping', list(TABLES)))
    available = {(row['table_name'], row['column_name'], row['type_name']) for row in columns}
    if not all(any(item[:2] == (table, column) for item in available)
               for table, names in REQUIRED_COLUMNS.items() for column in names.split()):
        errors.append('SCHEMA_REQUIRED_COLUMNS_MISSING')
    if not all((table, column, 'jsonb') in available for table, column in JSONB_COLUMNS.items()):
        errors.append('SCHEMA_JSONB_CONTRACT_MISMATCH')
    constraints = _rows(connection, "SELECT c.relname AS table_name,k.contype AS kind,"
        "ARRAY(SELECT a.attname FROM unnest(k.conkey) WITH ORDINALITY x(attnum,pos) "
        "JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.attnum ORDER BY x.pos) AS columns,"
        "rc.relname AS referenced_table,rn.nspname AS referenced_schema,"
        "ARRAY(SELECT a.attname FROM unnest(k.confkey) WITH ORDINALITY x(attnum,pos) "
        "JOIN pg_attribute a ON a.attrelid=rc.oid AND a.attnum=x.attnum ORDER BY x.pos) AS referenced_columns "
        "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_class rc ON rc.oid=k.confrelid LEFT JOIN pg_namespace rn ON rn.oid=rc.relnamespace "
        "WHERE n.nspname=%s AND c.relname=ANY(%s) AND k.convalidated", ('school_scraping', list(TABLES)))
    keys = {(row['table_name'], row['kind'], tuple(row['columns'])) for row in constraints}
    foreign = {(row['table_name'], tuple(row['columns']), row['referenced_table'], tuple(row['referenced_columns']))
        for row in constraints if row['kind'] == 'f' and row['referenced_schema'] == 'school_scraping'}
    if not KEYS.issubset(keys) or not FOREIGN_KEYS.issubset(foreign):
        errors.append('SCHEMA_KEY_CONSTRAINTS_MISSING')
    sequences = _rows(connection, "SELECT has_sequence_privilege(current_user,s.oid,'USAGE') "
        "OR has_sequence_privilege(current_user,s.oid,'UPDATE') AS usable FROM pg_class s "
        "JOIN pg_depend d ON d.objid=s.oid AND d.deptype='a' JOIN pg_class c ON c.oid=d.refobjid "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE s.relkind='S' AND n.nspname=%s "
        "AND c.relname=ANY(%s)", ('school_scraping', list(TABLES)))
    if any(not row['usable'] for row in sequences):
        errors.append('SERIAL_SEQUENCE_PRIVILEGES_MISSING')
    ssl = _one(connection, 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
    if database['tls']['mode'] == 'verify-full' and ssl['ssl'] is not True:
        errors.append('VERIFIED_TLS_CONNECTION_REQUIRED')
    if not any(code.startswith('DATABASE_ROLE') or code.startswith('DATABASE_TABLE') or code.startswith('SERIAL_') for code in errors):
        checks['role_privileges'] = 'PASS'
    if not any(code.startswith('SCHEMA_') or code == 'REQUIRED_TABLES_MISSING' for code in errors):
        checks['schema_contract_v1'] = 'PASS'
    checks['tls_policy'] = 'PASS' if 'VERIFIED_TLS_CONNECTION_REQUIRED' not in errors else 'BLOCKED'


def production_preflight(args, source, profile, *, connect=None, readiness_checker=None):
    """Return safe PASS/BLOCKED evidence. Never commits or applies schema changes."""
    errors, checks = [], {}
    try:
        database = _configuration(args, source, profile, errors)
    except Exception:
        errors.append('PROFILE_INVALID')
        database = None
    if errors:
        return {'status': 'BLOCKED', 'errors': sorted(set(errors)), 'checks': checks}
    checks['configuration'] = 'PASS'
    try:
        if readiness_checker is None:
            from school_scraping.readiness import check_readiness
            readiness_checker = check_readiness
        readiness = readiness_checker(profile.get('readiness') or {})
        if not isinstance(readiness, dict) or readiness.get('status') != 'PASS':
            errors.append('BACKUP_OR_MONITORING_NOT_READY')
        else:
            checks['readiness_mode'] = readiness.get('mode', 'prototype')
            checks['deployed_monitoring'] = readiness.get('deployed_monitoring') is True
            prototype_test = ((profile.get('testing') or {}).get('allow_prototype_readiness') is True
                and database['host'] == '127.0.0.1' and database['port'] == 55449
                and str(database['name']).startswith('section13b_test_'))
            if not checks['deployed_monitoring'] and not prototype_test:
                errors.append('DEPLOYED_MONITORING_NOT_VERIFIED')
            else:
                checks['backup_and_monitoring'] = 'PASS'
                if prototype_test and not checks['deployed_monitoring']:
                    checks['prototype_test_exception'] = True
    except Exception:
        errors.append('READINESS_CHECK_FAILED')
    if errors:
        return {'status': 'BLOCKED', 'errors': sorted(set(errors)), 'checks': checks}
    timeouts, tls = database['timeouts'], database['tls']
    settings = {'host': database['host'], 'port': database['port'], 'dbname': database['name'],
        'user': database['user'], 'password': os.environ['DB_PASSWORD'], 'connect_timeout': timeouts['connect_seconds'],
        'sslmode': 'verify-full' if tls['mode'] == 'verify-full' else 'disable',
        'options': '-c default_transaction_read_only=on -c statement_timeout=' + str(timeouts['statement_ms'])
            + ' -c lock_timeout=' + str(timeouts['lock_ms']),
        'application_name': 'school_scraping_production_preflight', 'row_factory': dict_row}
    if tls['mode'] == 'verify-full':
        settings['sslrootcert'] = tls['root_certificate']
    connection = None
    try:
        if connect is None:
            import psycopg
            connect = psycopg.connect
        connection = connect(**settings)
        _database_checks(connection, database, checks, errors)
    except Exception:
        errors.append('DATABASE_PREFLIGHT_FAILED')
    finally:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                errors.append('DATABASE_CONNECTION_CLEANUP_FAILED')
            finally:
                try:
                    connection.close()
                except Exception:
                    errors.append('DATABASE_CONNECTION_CLEANUP_FAILED')
    return {'status': 'PASS' if not errors else 'BLOCKED', 'errors': sorted(set(errors)), 'checks': checks}


preflight = production_preflight


def runtime_environment(profile):
    """Whitelist approved nonsecret libpq/runtime policy settings for a child."""
    database = profile.get('database') or {}
    tls, timeouts = database.get('tls') or {}, database.get('timeouts') or {}
    if (profile.get('approved') is not True or not isinstance(profile.get('approval_reference'), str)
            or not profile['approval_reference'].strip()
            or (profile.get('scheduler') or {}).get('approved') is not True
            or not all(_positive(timeouts.get(key), integer=True)
                       for key in ('connect_seconds', 'statement_ms', 'lock_ms'))
            or tls.get('mode') not in ('verify-full', 'approved-local')):
        raise ValueError('RUNTIME_PROFILE_NOT_APPROVED')
    result = {'PYTHON_DOTENV_DISABLED': '1', 'DB_CONNECT_TIMEOUT_SECONDS': str(timeouts['connect_seconds']),
        'DB_STATEMENT_TIMEOUT_MS': str(timeouts['statement_ms']), 'DB_LOCK_TIMEOUT_MS': str(timeouts['lock_ms']),
        'DB_SSLMODE': 'verify-full' if tls['mode'] == 'verify-full' else 'disable'}
    if tls['mode'] == 'verify-full':
        if not tls.get('root_certificate') or not Path(tls['root_certificate']).is_file():
            raise ValueError('TLS_ROOT_CERTIFICATE_REQUIRED')
        result['DB_SSLROOTCERT'] = str(tls['root_certificate'])
    elif (database.get('host') not in ('127.0.0.1', '::1') or tls.get('approved_local_exception') is not True
            or not tls.get('reason')):
        raise ValueError('LOCAL_TLS_EXCEPTION_NOT_APPROVED')
    result['PGSSLMODE'] = result['DB_SSLMODE']
    if 'DB_SSLROOTCERT' in result:
        result['PGSSLROOTCERT'] = result['DB_SSLROOTCERT']
    return result
