"""Approval gate tests never connect unless a dedicated disposable DSN is set."""
import copy
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from school_scraping import production_safety as safety
from school_scraping.database_only import DatabaseOnlyError, _environment_connection


def options():
    return SimpleNamespace(storage_mode='database', database=True, new_only=True, refresh=False,
        scheduler_enabled=True, state=['Punjab'], district=[], pincode=[], academic_year='2025-26',
        all_states=False, unlimited=False, sync_pending=False, limit=1)


def profile():
    return {'schema_version': 1, 'approved': True, 'approval_reference': 'SYNTHETIC UNIT TEST ONLY',
        'scheduler': {'approved': True, 'hard_timeout_seconds': 180},
        'allowed_jobs': [{'source': source, 'states': ['Punjab'], 'districts': [], 'pincodes': [],
            'policy': 'new-only', 'academic_year': '2025-26'} for source in ('saras', 'udise')],
        'protected_targets': [{'host': '*', 'port': 55439}],
        'database': {'host': '127.0.0.1', 'port': 55449, 'name': 'section13b_test_mock',
            'user': 'synthetic_writer', 'major_version': 17, 'database_oid': 42000,
            'schema_version': 1, 'canonical_schema_version': '4.0',
            'timeouts': {'connect_seconds': 5, 'statement_ms': 5000, 'lock_ms': 1000},
            'tls': {'mode': 'approved-local', 'approved_local_exception': True,
                    'reason': 'Explicit synthetic loopback test exception'}},
        'readiness': {'enabled': True}}


def environment(value):
    database = value['database']
    return {'DB_HOST': database['host'], 'DB_PORT': str(database['port']),
        'DB_NAME': database['name'], 'DB_USER': database['user'], 'DB_PASSWORD': 'synthetic-secret-value'}


class ProductionPreflightTests(unittest.TestCase):
    def setUp(self):
        self.args, self.profile = options(), profile()
        self.connection = Mock()
        self.factory = Mock(return_value=self.connection)
        self.readiness = Mock(return_value={'status': 'PASS', 'mode': 'synthetic', 'deployed_monitoring': True})
        self.identity = {'database_name': 'section13b_test_mock', 'user_name': 'synthetic_writer',
            'server_address': '127.0.0.1', 'server_port': 55449, 'major_version': 17, 'database_oid': 42000,
            'statement_timeout_ms': 5000, 'lock_timeout_ms': 1000}
        self.role = dict(rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolreplication=False,
            rolbypassrls=False, owns_database=False, owns_schema=False, schema_usage=True,
            database_create=False, schema_create=False, broad_write_or_server_access=False)
        self.tables = [dict(table_name=name, owned=False, can_select=True, can_insert=True,
            can_update=name != 'scrape_events', can_delete=False, can_truncate=False, can_trigger=False)
            for name in safety.TABLES]
        self.columns = [dict(table_name=table, column_name=column,
            type_name='jsonb' if safety.JSONB_COLUMNS.get(table) == column else 'text')
            for table, names in safety.REQUIRED_COLUMNS.items() for column in names.split()]
        self.constraints = [dict(table_name=table, kind=kind, columns=list(columns),
            referenced_table=None, referenced_schema=None, referenced_columns=[])
            for table, kind, columns in safety.KEYS]
        self.constraints += [dict(table_name=table, kind='f', columns=list(columns),
            referenced_table=target, referenced_schema='school_scraping', referenced_columns=list(target_columns))
            for table, columns, target, target_columns in safety.FOREIGN_KEYS]
        self.sequences, self.ssl = [], False

    def query(self, connection, statement, parameters=()):
        if 'current_database() AS database_name' in statement:
            return [self.identity]
        if 'FROM pg_roles' in statement:
            return [self.role]
        if 'AS can_select' in statement:
            return self.tables
        if 'FROM pg_attribute' in statement:
            return self.columns
        if 'FROM pg_constraint' in statement:
            return self.constraints
        if 'has_sequence_privilege' in statement:
            return self.sequences
        if 'FROM pg_stat_ssl' in statement:
            return [{'ssl': self.ssl}]
        raise AssertionError('Unexpected catalog query')

    def run_preflight(self, source='saras', env=None):
        with (patch.dict(os.environ, environment(self.profile) if env is None else env, clear=True),
              patch.object(safety, '_rows', side_effect=self.query)):
            return safety.production_preflight(self.args, source, self.profile,
                connect=self.factory, readiness_checker=self.readiness)

    def test_approved_bounded_job_passes_with_readonly_catalog_checks(self):
        result = self.run_preflight()
        self.assertEqual(result['status'], 'PASS', result)
        self.assertEqual(result['checks']['schema_contract_v1'], 'PASS')
        self.assertTrue(result['checks']['deployed_monitoring'])
        self.assertEqual(result['checks']['readiness_mode'], 'synthetic')
        self.connection.execute.assert_called_once_with('SET TRANSACTION READ ONLY')
        self.connection.commit.assert_not_called()
        self.connection.rollback.assert_called_once()
        self.connection.close.assert_called_once()
        self.assertNotIn('synthetic-secret-value', json.dumps(result))
        settings = self.factory.call_args.kwargs
        self.assertEqual(settings['connect_timeout'], 5)
        self.assertEqual(settings['sslmode'], 'disable')
        self.assertIn('default_transaction_read_only=on', settings['options'])
        self.assertIn('statement_timeout=5000', settings['options'])
        self.assertIn('lock_timeout=1000', settings['options'])

    def test_unapproved_or_incomplete_configuration_blocks_before_connect(self):
        mutations = [
            lambda a,p:p.update(approved=False),
            lambda a,p:p.update(approval_reference=''),
            lambda a,p:p.update(schema_version=2),
            lambda a,p:p.update(schema_version=True),
            lambda a,p:p['scheduler'].update(approved=False),
            lambda a,p:p['scheduler'].update(hard_timeout_seconds=None),
            lambda a,p:setattr(a,'scheduler_enabled',False),
            lambda a,p:setattr(a,'storage_mode','files'),
            lambda a,p:setattr(a,'database',False),
            lambda a,p:setattr(a,'all_states',True),
            lambda a,p:setattr(a,'unlimited',True),
            lambda a,p:setattr(a,'sync_pending',True),
            lambda a,p:setattr(a,'limit',None),
            lambda a,p:setattr(a,'new_only',False),
            lambda a,p:setattr(a,'refresh',True),
            lambda a,p:setattr(a,'state',['Haryana']),
            lambda a,p:setattr(a,'pincode',['134001']),
            lambda a,p:p['database'].update(database_oid=None),
            lambda a,p:p['database'].update(port=5432),
            lambda a,p:p['database'].update(host='unrelated-hostname',port=55439),
            lambda a,p:p['database'].update(name='postgres'),
            lambda a,p:p['database'].update(name='database_only_test_original'),
            lambda a,p:p['database'].update(host='127.0.0.1,example.invalid'),
            lambda a,p:p['database'].update(schema_version=2),
            lambda a,p:p['database'].update(schema_version=True),
            lambda a,p:p['database']['timeouts'].update(statement_ms=0),
            lambda a,p:p['database']['timeouts'].update(lock_ms=6000),
            lambda a,p:p['database']['tls'].update(mode='prefer'),
            lambda a,p:p['database']['tls'].update(approved_local_exception=False),
            lambda a,p:p['database']['tls'].update(reason=''),
            lambda a,p:p['database'].update(host='example.invalid'),
            lambda a,p:p.update(protected_targets=[]),
            lambda a,p:p['protected_targets'].append({'host':'127.0.0.1','port':55449,'name':'section13b_test_mock'}),
        ]
        for index, mutation in enumerate(mutations):
            with self.subTest(case=index):
                self.args,self.profile=options(),profile()
                mutation(self.args,self.profile)
                result=self.run_preflight()
                self.assertEqual(result['status'],'BLOCKED')
                self.factory.assert_not_called()
        self.readiness.assert_not_called()

    def test_udise_requires_exact_explicit_year(self):
        self.assertEqual(self.run_preflight('udise')['status'],'PASS')
        for year in (None,'2024-25','invalid'):
            with self.subTest(year=year):
                self.args.academic_year=year
                self.factory.reset_mock()
                self.assertEqual(self.run_preflight('udise')['status'],'BLOCKED')
                self.factory.assert_not_called()

    def test_missing_or_mismatched_environment_never_uses_dotenv_fallback(self):
        for key in ('DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_PASSWORD'):
            with self.subTest(key=key):
                env=environment(self.profile);env.pop(key)
                self.assertEqual(self.run_preflight(env=env)['status'],'BLOCKED')
                self.factory.assert_not_called()
        env=environment(self.profile);env['DB_NAME']='wrong_target'
        self.assertEqual(self.run_preflight(env=env)['status'],'BLOCKED')

    def test_readiness_failure_and_exception_do_not_connect_or_leak_error(self):
        for failure in ({'status':'BLOCKED'},RuntimeError('password=synthetic-secret-value')):
            self.readiness.side_effect=failure if isinstance(failure,Exception) else None
            self.readiness.return_value=failure
            result=self.run_preflight()
            self.assertEqual(result['status'],'BLOCKED')
            self.assertNotIn('synthetic-secret-value',json.dumps(result))
            self.factory.assert_not_called()

    def test_connection_error_is_sanitized(self):
        self.factory.side_effect=RuntimeError('password=synthetic-secret-value')
        result=self.run_preflight()
        self.assertEqual(result['errors'],['DATABASE_PREFLIGHT_FAILED'])
        self.assertNotIn('synthetic-secret-value',json.dumps(result))

    def test_prototype_readiness_is_blocked_except_explicit_disposable_test_exception(self):
        self.readiness.return_value={'status':'PASS','mode':'prototype','deployed_monitoring':False}
        self.assertIn('DEPLOYED_MONITORING_NOT_VERIFIED',self.run_preflight()['errors'])
        self.factory.assert_not_called()
        self.profile['testing']={'allow_prototype_readiness':True}
        result=self.run_preflight()
        self.assertEqual(result['status'],'PASS')
        self.assertTrue(result['checks']['prototype_test_exception'])
        self.assertFalse(result['checks']['deployed_monitoring'])
        self.profile['database']['name']='approved_future_deployment'
        self.assertIn('DEPLOYED_MONITORING_NOT_VERIFIED',self.run_preflight()['errors'])

    def test_identity_and_timeout_mismatch_blocks(self):
        for key,value in [('database_name','another'),('server_port',55439),('database_oid',99999),
                ('user_name','admin'),('major_version',16),('statement_timeout_ms',0),('lock_timeout_ms',0)]:
            with self.subTest(key=key):
                original=self.identity.copy();self.identity[key]=value
                self.assertEqual(self.run_preflight()['status'],'BLOCKED')
                self.identity=original

    def test_unsafe_roles_and_missing_table_privileges_block(self):
        for flag in ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls','owns_database','owns_schema',
                'database_create','schema_create','broad_write_or_server_access'):
            with self.subTest(flag=flag):
                self.role[flag]=True
                self.assertIn('DATABASE_ROLE_NOT_LEAST_PRIVILEGED',self.run_preflight()['errors'])
                self.role[flag]=False
        self.tables[0]['can_update']=False
        self.assertIn('DATABASE_TABLE_PRIVILEGES_UNSUITABLE',self.run_preflight()['errors'])

    def test_optional_cluster_metadata_mismatch_is_blocked_without_broad_role_grants(self):
        original_query=self.query
        for key,code in (('data_directory','DATABASE_DATA_DIRECTORY_MISMATCH'),
                         ('system_identifier','DATABASE_SYSTEM_IDENTIFIER_MISMATCH')):
            with self.subTest(key=key):
                self.profile=profile();self.profile['database'][key]='approved-observation'
                def rows(connection,statement,parameters=()):
                    if statement in ("SELECT current_setting('data_directory') AS value",
                                     'SELECT (pg_control_system()).system_identifier::text AS value'):
                        return [{'value':'unexpected-observation'}]
                    return original_query(connection,statement,parameters)
                with patch.object(self,'query',side_effect=rows):
                    result=self.run_preflight()
                self.assertIn(code,result['errors'])
                self.assertNotIn('database_identity',result['checks'])

    def test_destructive_table_grants_and_mutable_event_grants_are_rejected(self):
        for flag in ('can_delete','can_truncate','can_trigger'):
            with self.subTest(privilege=flag):
                self.tables[0][flag]=True
                self.assertIn('DATABASE_TABLE_PRIVILEGES_UNSUITABLE',self.run_preflight()['errors'])
                self.tables[0][flag]=False
        event=next(row for row in self.tables if row['table_name']=='scrape_events')
        event['can_update']=True
        self.assertIn('DATABASE_TABLE_PRIVILEGES_UNSUITABLE',self.run_preflight()['errors'])

    def test_catalog_contract_requires_all_tables_columns_and_validated_keys(self):
        self.tables.pop()
        self.assertIn('REQUIRED_TABLES_MISSING',self.run_preflight()['errors'])
        self.tables.append(dict(table_name=safety.TABLES[-1],owned=False,can_select=True,can_insert=True,
            can_update=True,can_delete=False,can_truncate=False,can_trigger=False))
        self.columns=[row for row in self.columns if (row['table_name'],row['column_name'])!=('school_source_records','phone')]
        self.assertIn('SCHEMA_REQUIRED_COLUMNS_MISSING',self.run_preflight()['errors'])
        self.constraints.pop()
        self.assertIn('SCHEMA_KEY_CONSTRAINTS_MISSING',self.run_preflight()['errors'])

    def test_verify_full_requires_ca_and_actual_tls(self):
        self.profile['database']['tls']={'mode':'verify-full','root_certificate':'synthetic-ca.crt'}
        with patch.object(Path,'is_file',return_value=False):
            self.assertEqual(self.run_preflight()['status'],'BLOCKED')
            self.factory.assert_not_called()
        with patch.object(Path,'is_file',return_value=True):
            self.assertIn('VERIFIED_TLS_CONNECTION_REQUIRED',self.run_preflight()['errors'])
            self.ssl=True
            self.assertEqual(self.run_preflight()['status'],'PASS')
        self.assertEqual(self.factory.call_args.kwargs['sslmode'],'verify-full')
        self.assertEqual(self.factory.call_args.kwargs['sslrootcert'],'synthetic-ca.crt')

    def test_cleanup_closes_connection_even_when_rollback_fails(self):
        self.connection.rollback.side_effect=OSError('Unavailable')
        self.assertIn('DATABASE_CONNECTION_CLEANUP_FAILED',self.run_preflight()['errors'])
        self.connection.close.assert_called_once()

    def test_runtime_environment_is_whitelisted_nonsecret_and_requires_approval(self):
        values=safety.runtime_environment(self.profile)
        self.assertEqual(values['DB_STATEMENT_TIMEOUT_MS'],'5000')
        self.assertEqual(values['PGSSLMODE'],'disable')
        self.assertEqual(values['PYTHON_DOTENV_DISABLED'],'1')
        self.assertNotIn('DB_PASSWORD',values)
        self.assertNotIn('DB_NAME',values)
        self.profile['approved']=False
        with self.assertRaises(ValueError):safety.runtime_environment(self.profile)

    def test_database_store_optional_timeouts_and_tls_reach_libpq(self):
        env=environment(self.profile)
        env.update(DB_CONNECT_TIMEOUT_SECONDS='7',DB_STATEMENT_TIMEOUT_MS='8000',DB_LOCK_TIMEOUT_MS='2000',
            DB_SSLMODE='verify-full',DB_SSLROOTCERT='synthetic-ca.crt')
        with patch.dict(os.environ,env,clear=True),patch('dotenv.load_dotenv'),patch('psycopg.connect') as connect:
            _environment_connection()
        settings=connect.call_args.kwargs
        self.assertEqual(settings['connect_timeout'],7)
        self.assertEqual(settings['options'],'-c statement_timeout=8000 -c lock_timeout=2000')
        self.assertEqual(settings['sslmode'],'verify-full')
        self.assertEqual(settings['sslrootcert'],'synthetic-ca.crt')

    def test_invalid_optional_timeout_blocks_before_connect(self):
        for invalid in ('0','-1','not-a-number'):
            env=environment(self.profile);env['DB_STATEMENT_TIMEOUT_MS']=invalid
            with patch.dict(os.environ,env,clear=True),patch('dotenv.load_dotenv'),patch('psycopg.connect') as connect:
                with self.assertRaises(DatabaseOnlyError):_environment_connection()
                connect.assert_not_called()


@unittest.skipUnless(os.getenv('SCHOOL_SCRAPING_PRODUCTION_SAFETY_TEST_DSN'), 'Dedicated Section13B disposable DSN not supplied')
class ProductionPreflightPostgresTests(unittest.TestCase):
    def test_real_readonly_catalog_check_rejects_administrator_role(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        dsn=os.environ['SCHOOL_SCRAPING_PRODUCTION_SAFETY_TEST_DSN']
        settings=conninfo_to_dict(dsn)
        if (settings.get('host')!='127.0.0.1' or settings.get('port')!='55449'
                or not settings.get('dbname','').startswith('section13b_test_')):
            self.skipTest('Not an explicitly approved disposable Section13B database')
        with psycopg.connect(dsn,options='-c default_transaction_read_only=on') as observer:
            identity=observer.execute("SELECT current_database(),current_user,inet_server_port(),"
                "current_setting('data_directory'),current_setting('server_version_num')::int/10000,"
                "(SELECT oid FROM pg_database WHERE datname=current_database())").fetchone()
            expected=Path(__file__).resolve().parents[1]/'.refactor_validation/section10a_backup_restore_test/restore_cluster/pgdata'
            if identity[0]!=settings['dbname'] or identity[2]!=55449 or Path(identity[3]).resolve()!=expected or identity[4]!=17:
                self.skipTest('Disposable restore-cluster identity differs')
        value=profile();value['database'].update(name=identity[0],user=identity[1],database_oid=identity[5])
        value['testing']={'allow_prototype_readiness':True}
        def factory(**kwargs):
            return psycopg.connect(dsn,options=kwargs['options'],sslmode=kwargs['sslmode'],row_factory=kwargs['row_factory'])
        with patch.dict(os.environ,environment(value),clear=False):
            result=safety.production_preflight(options(),'saras',value,connect=factory,
                readiness_checker=lambda _: {'status':'PASS','mode':'prototype','deployed_monitoring':False})
        self.assertEqual(result['status'],'BLOCKED',result)
        self.assertIn('DATABASE_ROLE_NOT_LEAST_PRIVILEGED',result['errors'])
        self.assertNotIn('DATABASE_PREFLIGHT_FAILED',result['errors'])
