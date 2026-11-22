"""Offline checks of an inert proposal, not PostgreSQL parsing or execution.

These tests inspect supplied text/configuration and the existing Python approval
gate. Operator review and a separately approved PostgreSQL rehearsal are still
required before treating the new SQL/server/HBA templates as executable policy.
"""
import copy
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from school_scraping.production_safety import production_preflight
from school_scraping.production_safety import TABLES, KEYS, FOREIGN_KEYS, JSONB_COLUMNS


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / 'config/production_safety.production.example.json'
SERVER = ROOT / 'config/postgresql/production/postgresql.conf.example'
HBA = ROOT / 'config/postgresql/production/pg_hba.conf.example'
SQL_ROOT = ROOT / 'database/provisioning/production'
PROTECTED_PORTS = {5432, 55439, 55449, 59487}


def text(path):
    return path.read_text(encoding='utf-8-sig')


def active_lines(path):
    """Remove only ordinary full-line comments; this is not a SQL/HBA parser."""
    return [line.strip() for line in text(path).splitlines()
            if line.strip() and not line.lstrip().startswith(('#', '--'))]


def options():
    return SimpleNamespace(storage_mode='database', database=True, new_only=True,
        refresh=False, scheduler_enabled=False, state=['CHANDIGARH'], district=[], pincode=[],
        academic_year='2025-26', all_states=False, unlimited=False, sync_pending=False, limit=1)


def synthetic_complete_profile():
    """Only a mock: no target, login, credential or approval is provisioned."""
    value = json.loads(text(PROFILE))
    value.update(approved=True, approval_reference='SYNTHETIC OFFLINE NEGATIVE TEST')
    value['scheduler'].update(approved=True, hard_timeout_seconds=180)
    value['allowed_jobs'] = [{'source': source, 'states': ['CHANDIGARH'], 'districts': [],
        'pincodes': [], 'policy': 'new-only', 'academic_year': '2025-26'} for source in ('saras', 'udise')]
    value['database'].update(port=55459, name='schools_proposal_fixture', user='scraper_proposal_fixture',
        database_oid=123456, timeouts={'connect_seconds': 5, 'statement_ms': 5000, 'lock_ms': 1000},
        tls={'mode': 'approved-local', 'approved_local_exception': True,
             'reason': 'Synthetic isolated negative test; never a deployment approval'})
    return value


class ProductionProposalProfileTests(unittest.TestCase):
    def setUp(self):
        guard = patch('psycopg.connect', side_effect=AssertionError('Database access is forbidden in proposal tests'))
        guard.start()
        self.addCleanup(guard.stop)

    def test_profile_keeps_approvals_and_activation_disabled(self):
        value = json.loads(text(PROFILE))
        self.assertIs(value['approved'], False)
        self.assertIsNone(value['approval_reference'])
        self.assertIs(value['scheduler']['approved'], False)
        self.assertIsNone(value['scheduler']['hard_timeout_seconds'])
        self.assertEqual(value['allowed_jobs'], [])
        self.assertIs(value['readiness']['enabled'], False)
        self.assertIsNone(value['readiness']['approval_reference'])
        self.assertIsNone(value['database']['tls']['mode'])
        self.assertIs(value['database']['tls']['approved_local_exception'], False)

    def test_unknown_production_identity_has_no_rehearsal_defaults(self):
        database = json.loads(text(PROFILE))['database']
        self.assertEqual(database['host'], '127.0.0.1')
        self.assertEqual(database['major_version'], 17)
        for key in ('port', 'name', 'user', 'database_oid', 'data_directory', 'system_identifier'):
            with self.subTest(key=key):
                self.assertIsNone(database[key])
        self.assertTrue(all(value is None for value in database['timeouts'].values()))
        self.assertNotIn('section14a_app_', json.dumps(database))
        self.assertNotIn('.refactor_validation', json.dumps(database))

    def test_all_default_and_historical_ports_are_explicitly_protected(self):
        targets = json.loads(text(PROFILE))['protected_targets']
        self.assertEqual({row['port'] for row in targets}, PROTECTED_PORTS)
        self.assertTrue(all(row.get('host') == '*' and not row.get('name') for row in targets))

    def test_inactive_example_stops_in_existing_gate_before_connection_or_readiness(self):
        profile = json.loads(text(PROFILE))
        connector = Mock(side_effect=AssertionError('No database connection permitted'))
        readiness = Mock(side_effect=AssertionError('No external readiness probe permitted'))
        before = text(ROOT / 'config/states_pincodes.json')
        with patch.dict(os.environ, {}, clear=True):
            report = production_preflight(options(), 'saras', profile,
                                          connect=connector, readiness_checker=readiness)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('PROFILE_NOT_APPROVED', report['errors'])
        self.assertIn('DATABASE_IDENTITY_INCOMPLETE', report['errors'])
        connector.assert_not_called()
        readiness.assert_not_called()
        self.assertEqual(before, text(ROOT / 'config/states_pincodes.json'))

    def test_changing_approval_flags_cannot_fill_missing_target_or_scope(self):
        value = json.loads(text(PROFILE))
        value.update(approved=True, approval_reference='SYNTHETIC INCOMPLETE TEST')
        value['scheduler']['approved'] = True
        args = options()
        args.scheduler_enabled = True
        connector = Mock()
        with patch.dict(os.environ, {}, clear=True):
            report = production_preflight(args, 'udise', value, connect=connector)
        self.assertEqual(report['status'], 'BLOCKED')
        self.assertIn('DATABASE_IDENTITY_INCOMPLETE', report['errors'])
        self.assertIn('SOURCE_SCOPE_OR_YEAR_NOT_APPROVED', report['errors'])
        connector.assert_not_called()

    def test_each_protected_port_blocks_an_otherwise_synthetic_approved_plan(self):
        for port in sorted(PROTECTED_PORTS):
            with self.subTest(port=port):
                value = synthetic_complete_profile()
                value['database']['port'] = port
                args = options()
                args.scheduler_enabled = True
                connector = Mock()
                environment = {'DB_HOST': '127.0.0.1', 'DB_PORT': str(port),
                    'DB_NAME': value['database']['name'], 'DB_USER': value['database']['user'],
                    'DB_PASSWORD': 'synthetic-unit-marker'}
                with patch.dict(os.environ, environment, clear=True):
                    report = production_preflight(args, 'saras', value, connect=connector)
                self.assertEqual(report['status'], 'BLOCKED')
                self.assertIn('PROTECTED_DATABASE_TARGET', report['errors'])
                self.assertNotIn('synthetic-unit-marker', json.dumps(report))
                connector.assert_not_called()

    def test_shared_schedule_and_default_database_remain_disabled(self):
        value = json.loads(text(ROOT / 'config/states_pincodes.json'))
        self.assertIs(value['scheduler']['enabled'], False)
        self.assertIs(value['database']['enabled'], False)
        self.assertEqual(value['scheduler']['time'], '23:00')
        self.assertEqual(value['scheduler']['timezone'], 'Asia/Kolkata')


class ProductionServerProposalTests(unittest.TestCase):
    def settings(self):
        return {key.strip(): value.strip().strip("'") for key, value in
                (line.split('=', 1) for line in active_lines(SERVER))}

    def test_server_requires_rendered_target_certificates_timeouts_and_log_approvals(self):
        values = self.settings()
        for key in ('port', 'ssl_cert_file', 'ssl_key_file', 'statement_timeout', 'lock_timeout',
                    'log_directory', 'log_rotation_age', 'log_rotation_size'):
            with self.subTest(key=key):
                self.assertRegex(values[key], r'^__APPROVED_[A-Z_]+__$')
        self.assertEqual(values['listen_addresses'], '127.0.0.1')
        self.assertFalse(any(key.startswith('include') for key in values))

    def test_server_proposes_tls_scram_and_durable_commits(self):
        values = self.settings()
        self.assertEqual(values['password_encryption'], 'scram-sha-256')
        for key in ('ssl', 'fsync', 'full_page_writes', 'synchronous_commit'):
            with self.subTest(key=key):
                self.assertEqual(values[key], 'on')

    def test_log_proposal_does_not_record_sql_or_error_parameters(self):
        values = self.settings()
        self.assertEqual(values['log_statement'], 'none')
        self.assertEqual(values['log_min_error_statement'], 'panic')
        self.assertEqual(values['log_parameter_max_length_on_error'], '0')

    def test_hba_allows_only_named_tls_scram_loopback_rules(self):
        rules = [line.split() for line in active_lines(HBA)]
        allows = [row for row in rules if row[-1] != 'reject']
        self.assertEqual(len(allows), 6)
        for rule in allows:
            with self.subTest(rule=rule):
                self.assertEqual(len(rule), 5)
                self.assertEqual(rule[0], 'hostssl')
                self.assertRegex(rule[1], r'^__APPROVED_[A-Z_]+__$')
                self.assertRegex(rule[2], r'^\+?__APPROVED_[A-Z_]+__$')
                self.assertEqual(rule[3:], ['127.0.0.1/32', 'scram-sha-256'])
        self.assertEqual(rules[-3:], [['hostnossl', 'all', 'all', '127.0.0.1/32', 'reject'],
                                     ['host', 'all', 'all', '0.0.0.0/0', 'reject'],
                                     ['host', 'all', 'all', '::/0', 'reject']])

    def test_config_templates_contain_no_active_rehearsal_paths_or_credentials(self):
        for path in (SERVER, HBA):
            with self.subTest(path=path):
                content = '\n'.join(active_lines(path)).casefold()
                self.assertNotIn('.refactor_validation', content)
                self.assertNotIn('section14a_scraper_', content)
                self.assertNotIn('memory_test_admin', content)
                self.assertNotRegex(content, r'\b(trust|md5|password)\s*$')


class ProductionSqlReviewTests(unittest.TestCase):
    """Review safety invariants in text; no SQL is submitted to PostgreSQL."""
    def normalized(self, name):
        return ' '.join(active_lines(SQL_ROOT / name))

    def test_every_write_wrapper_runs_its_fixed_shared_gate_first(self):
        for name, stage in (('bootstrap.sql', 'bootstrap'), ('migrate.sql', 'migrate'),
                            ('privileges.sql', 'privileges')):
            with self.subTest(name=name):
                content = self.normalized(name)
                self.assertIn('\\set ON_ERROR_STOP on', content)
                self.assertIn('\\set provisioning_stage ' + stage, content)
                gate = content.index('\\ir verify.sql')
                change = re.search(r'\b(CREATE ROLE|CREATE DATABASE|REVOKE|SET (?:LOCAL )?ROLE)\b', content)
                self.assertIsNotNone(change)
                self.assertLess(gate, change.start())

    def test_shared_gate_is_readonly_and_requires_all_nonsecret_bindings(self):
        content = self.normalized('verify.sql')
        for name in ('provisioning_approved', 'approval_reference', 'approved_host',
                     'approved_port', 'approved_server_major', 'approved_system_id',
                     'approved_data_directory', 'approved_admin', 'approved_database',
                     'migration_role', 'scraper_role', 'report_role', 'backup_role',
                     'tls_policy', 'approved_maintenance_database', 'approved_maintenance_oid',
                     'new_database_oid'):
            with self.subTest(name=name):
                self.assertIn(':{?' + name + '}', content)
        self.assertIn('BEGIN READ ONLY;', content)
        self.assertTrue(content.endswith('COMMIT;'))
        self.assertIn('\\set provisioning_stage verify', content)
        self.assertIn('READ_ONLY_STAGE_GATE_PASSED_NOT_PRODUCTION_ACTIVATION', content)
        self.assertRegex(content, r'SELECT CAST\(\x27BLOCKED_[A-Z_]+\x27 AS integer\);')
        self.assertNotRegex(content, r'\\(?:q|quit)\s+\d')
        for line in active_lines(SQL_ROOT / 'verify.sql'):
            self.assertNotRegex(line, r'^(?:CREATE|ALTER|DROP|GRANT|REVOKE|INSERT|UPDATE|DELETE|TRUNCATE)\b')

    def test_gate_checks_actual_cluster_and_strict_tls_scram_loopback(self):
        content = self.normalized('verify.sql')
        for fragment in ("current_user = :'approved_admin'", "session_user = :'approved_admin'",
                         "host(inet_server_addr()) = :'approved_host'", 'inet_server_port()',
                         "current_setting('server_version_num')", '(pg_control_system()).system_identifier',
                         "current_setting('data_directory')", "current_setting('listen_addresses') = '127.0.0.1'",
                         "current_setting('ssl') = 'on'", 'pg_stat_ssl',
                         "current_setting('password_encryption') = 'scram-sha-256'",
                         "type LIKE 'host%'", "netmask IS DISTINCT FROM '255.255.255.255'",
                         "address IS DISTINCT FROM '127.0.0.1'",
                         "NOT IN (5432,55439,55449,59487)",
                         "current_setting('fsync') = 'on'", "current_setting('full_page_writes') = 'on'",
                         "current_setting('synchronous_commit') = 'on'"):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, content)
        self.assertIn("bool_and((rolpassword LIKE 'SCRAM-SHA-256$%') IS TRUE)", content)
        self.assertIn(":'_runtime_sslmode' = 'verify-full'", content)

    def test_bootstrap_refuses_existing_objects_and_creates_password_disabled_roles(self):
        gate = self.normalized('verify.sql')
        self.assertIn("NOT EXISTS (SELECT 1 FROM pg_database WHERE datname=:'approved_database')", gate)
        self.assertIn('BLOCKED_NEW_CLUSTER_DATABASE_OR_ROLE_NAMES_NOT_FRESH', gate)
        self.assertIn('BLOCKED_DATABASE_OID_OWNER_OR_ROLE_BOUNDARY', gate)
        content = self.normalized('bootstrap.sql')
        roles = re.findall(r'CREATE ROLE :"(\w+)" ([^;]+);', content)
        self.assertEqual({name for name, _ in roles}, {'migration_role', 'scraper_role', 'report_role', 'backup_role'})
        for name, attributes in roles:
            with self.subTest(role=name):
                expected = 'LOGIN' if name in ('migration_role', 'scraper_role') else 'NOLOGIN'
                self.assertTrue(attributes.startswith(expected + ' PASSWORD NULL '))
                for flag in ('NOSUPERUSER', 'NOCREATEDB', 'NOCREATEROLE', 'NOINHERIT', 'NOREPLICATION', 'NOBYPASSRLS'):
                    self.assertIn(flag, attributes)
        creation = content.index('CREATE DATABASE')
        self.assertLess(content.index('COMMIT;'), creation)
        self.assertIn('ALLOW_CONNECTIONS false', content)
        self.assertLess(content.index('REVOKE ALL ON DATABASE'), content.index('ALLOW_CONNECTIONS true'))

    def test_migration_includes_only_unchanged_schema_inputs_as_owner_in_order(self):
        lines = active_lines(SQL_ROOT / 'migrate.sql')
        includes = [line.removeprefix('\\ir ') for line in lines if line.startswith('\\ir ')]
        resolved = [(SQL_ROOT / name).resolve() for name in includes]
        self.assertEqual(resolved, [SQL_ROOT / 'verify.sql', ROOT / 'database/schema.sql',
            ROOT / 'database/schema_school_tables.sql', ROOT / 'database/migrations/001_scraping_memory.sql'])
        content = self.normalized('migrate.sql')
        self.assertLess(content.index('SET ROLE :"migration_role";'), content.index('\\ir ../../schema.sql'))
        self.assertLess(content.index('\\ir ../../migrations/001_scraping_memory.sql'), content.index('RESET ROLE;'))
        self.assertIn("to_regnamespace('school_scraping') IS NULL", self.normalized('verify.sql'))

    def test_scraper_grants_match_actual_pipeline_and_events_are_insert_only(self):
        content = self.normalized('privileges.sql')
        statements = re.findall(r'GRANT (SELECT, INSERT(?:, UPDATE)?) ON TABLE (.+?) TO :"scraper_role";', content)
        grants = {}
        for rights, tables in statements:
            for name in tables.split(','):
                grants[name.strip().removeprefix('school_scraping.')] = set(rights.split(', '))
        self.assertEqual(set(grants), set(TABLES))
        for name, rights in grants.items():
            with self.subTest(table=name):
                self.assertEqual(rights, {'SELECT', 'INSERT'} if name == 'scrape_events' else {'SELECT', 'INSERT', 'UPDATE'})
        self.assertIn('REVOKE ALL ON SCHEMA public FROM PUBLIC;', content)
        self.assertIn('REVOKE ALL ON ALL SEQUENCES', content)
        self.assertIn('GRANT SELECT ON ALL SEQUENCES IN SCHEMA school_scraping TO :"backup_role";', content)
        self.assertNotIn('ALTER DEFAULT PRIVILEGES', content)
        self.assertNotRegex(content, r'GRANT ALL\b')

    def test_catalogue_guard_tracks_existing_jsonb_and_key_contracts(self):
        compact = re.sub(r'\s+', '', self.normalized('verify.sql'))
        def array(values):
            return 'ARRAY[' + ','.join("'" + value + "'" for value in values) + ']'
        for table, column in JSONB_COLUMNS.items():
            with self.subTest(table=table):
                self.assertIn("('" + table + "','" + column + "')", compact)
        for table, kind, columns in KEYS:
            with self.subTest(key=(table, kind, columns)):
                self.assertIn("('" + table + "','" + kind + "'," + array(columns) + ',', compact)
        for table, columns, target, target_columns in FOREIGN_KEYS:
            with self.subTest(foreign=(table, columns)):
                self.assertIn("('" + table + "','f'," + array(columns) + ",'" + target + "'," + array(target_columns) + ')', compact)

    def test_role_and_sequence_verification_detects_extra_privileges(self):
        content = self.normalized('verify.sql')
        self.assertIn('r.oid=m.member', content)
        self.assertIn('r.oid=m.roleid', content)
        for role in ('scraper_role', 'report_role', 'backup_role'):
            for privilege in ('SELECT', 'USAGE', 'UPDATE'):
                with self.subTest(role=role, privilege=privilege):
                    fragment = "has_sequence_privilege(:'" + role + "',c.oid,'" + privilege + "')"
                    self.assertIn(fragment, content)
        self.assertIn("has_any_column_privilege(:'scraper_role',c.oid,'UPDATE')", content)
        self.assertIn("has_any_column_privilege(readers.name,c.oid,'INSERT')", content)
        for privilege in ('DELETE', 'TRUNCATE', 'TRIGGER', 'MAINTAIN'):
            self.assertIn("has_table_privilege(:'scraper_role',c.oid,'" + privilege + "')", content)

    def test_sql_has_no_unquoted_input_expansion_shell_commands_or_destructive_cleanup(self):
        for path in SQL_ROOT.glob('*.sql'):
            with self.subTest(file=path.name):
                lines = active_lines(path)
                sql = '\n'.join(line for line in lines if not line.startswith('\\'))
                self.assertNotRegex(sql, r'(?<!:):[a-zA-Z_]')
                self.assertNotRegex(sql, r'(?im)^\s*(?:DROP|DELETE|TRUNCATE|ALTER SYSTEM)\b')
                self.assertFalse(any(line.startswith(('\\!', '\\connect', '\\gexec')) for line in lines))


if __name__ == '__main__':
    unittest.main()
