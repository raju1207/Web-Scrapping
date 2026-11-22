"""No-network tests for the explicit new-cluster integration-test guard."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from tests.support import postgres_target as target


class PostgresTargetGuardTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.nonce = 'abcdef123456'
        self.root = self.project / '.refactor_validation' / ('section14a_pg17_rehearsal_' + self.nonce)
        self.data = self.root / 'pgdata'
        self.data.mkdir(parents=True)
        self.path = self.root / 'target_guard.json'
        self.app, self.catalog = ('section14a_' + kind + '_' + self.nonce for kind in ('app', 'catalog'))
        self.admin, self.writer = ('section14a_' + kind + '_' + self.nonce for kind in ('admin', 'scraper'))
        self.manifest = {'version': 1, 'root': str(self.root), 'data_directory': str(self.data),
            'host': '127.0.0.1', 'port': 55479, 'major_version': 17, 'system_identifier': '111222333444',
            'databases': {self.app: {'oid': 101, 'users': [self.writer, self.admin]},
                          self.catalog: {'oid': 102, 'users': [self.admin]}}}
        self.admin_dsn = f'host=127.0.0.1 port=55479 dbname={self.catalog} user={self.admin} password=synthetic-secret'
        self.writer_dsn = f'host=127.0.0.1 port=55479 dbname={self.app} user={self.writer} password=synthetic-secret'
        self.admin_identity = dict(database_name=self.catalog, user_name=self.admin,
            server_address='127.0.0.1', server_port=55479, major_version=17, database_oid=102,
            read_only='on', data_directory=str(self.data), system_identifier='111222333444')
        self.writer_identity = dict(database_name=self.app, user_name=self.writer,
            server_address='127.0.0.1', server_port=55479, major_version=17, database_oid=101, read_only='on')
        self.admin_connection, self.writer_connection = Mock(), Mock()
        self.admin_connection.execute.return_value.fetchone.side_effect = lambda: self.admin_identity
        self.writer_connection.execute.return_value.fetchone.side_effect = lambda: self.writer_identity
        self.connect = Mock(side_effect=[self.admin_connection, self.writer_connection])

    def run_guard(self, *, dsn=None, environment=None):
        self.path.write_text(json.dumps(self.manifest), encoding='utf-8')
        env = {target.GUARD_ENV: str(self.path), target.ADMIN_ENV: self.admin_dsn}
        env.update(environment or {})
        with patch.dict(os.environ, env, clear=True), patch.object(target, 'PROJECT_ROOT', self.project):
            return target.section14a_target(dsn or self.writer_dsn, connect=self.connect)

    def test_absent_opt_in_retains_existing_guards_without_any_io(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(target, '_manifest') as manifest:
            self.assertIsNone(target.section14a_target('not parsed without opt-in', connect=self.connect))
        manifest.assert_not_called()
        self.connect.assert_not_called()

    def test_complete_guard_checks_admin_cluster_then_restricted_user_without_broad_grants(self):
        result = self.run_guard()
        self.assertEqual(result['database'], self.app)
        self.assertEqual(result['user'], self.writer)
        self.assertEqual(result['database_oid'], 101)
        self.assertEqual(result['system_identifier'], '111222333444')
        self.assertEqual(self.connect.call_count, 2)
        self.assertIn('pg_control_system()', self.admin_connection.execute.call_args.args[0])
        self.assertNotIn('pg_control_system()', self.writer_connection.execute.call_args.args[0])
        self.assertNotIn('data_directory', self.writer_connection.execute.call_args.args[0])
        for call, connection in zip(self.connect.call_args_list, (self.admin_connection, self.writer_connection)):
            self.assertEqual(call.kwargs['hostaddr'], '127.0.0.1')
            self.assertEqual(call.kwargs['connect_timeout'], 5)
            self.assertIn('default_transaction_read_only=on', call.kwargs['options'])
            self.assertTrue(connection.execute.call_args.args[0].startswith('SELECT '))
            connection.commit.assert_not_called()
            connection.close.assert_called_once()
        self.assertNotIn('synthetic-secret', json.dumps(result))

    def test_forbidden_ports_or_nonunique_manifest_identity_block_before_connect(self):
        original = copy.deepcopy(self.manifest)
        for key, value in [('port', 5432), ('port', 55439), ('port', 55449), ('port', True),
                           ('host', 'example.invalid'), ('major_version', 16), ('version', True),
                           ('system_identifier', '0'), ('system_identifier', 'not-an-id')]:
            with self.subTest(key=key, value=value):
                self.manifest = copy.deepcopy(original); self.manifest[key] = value
                with self.assertRaises(RuntimeError):
                    self.run_guard()
                self.connect.assert_not_called()

    def test_unapproved_database_user_or_route_never_connects(self):
        invalid = [self.writer_dsn.replace(self.app, 'postgres'),
            self.writer_dsn.replace(self.writer, 'unapproved_user'),
            self.writer_dsn.replace('55479', '55439'),
            self.writer_dsn.replace('127.0.0.1', '192.0.2.1'),
            self.writer_dsn + ' hostaddr=192.0.2.2', self.writer_dsn + ' service=another']
        for dsn in invalid:
            with self.subTest(case=invalid.index(dsn)):
                with self.assertRaises(RuntimeError):self.run_guard(dsn=dsn)
                self.connect.assert_not_called()
        with self.assertRaises(RuntimeError):self.run_guard(environment={'PGHOSTADDR':'192.0.2.3'})
        with self.assertRaises(RuntimeError):self.run_guard(environment={'PGSERVICE':'another'})
        self.connect.assert_not_called()

    def test_admin_dsn_is_also_guarded_before_any_connection(self):
        for admin in (self.admin_dsn.replace('55479','55449'),self.admin_dsn.replace(self.catalog,'postgres')):
            with self.assertRaises(RuntimeError):self.run_guard(environment={target.ADMIN_ENV:admin})
            self.connect.assert_not_called()

    def test_manifest_cannot_reuse_historical_or_redirected_directories(self):
        original = copy.deepcopy(self.manifest)
        self.manifest['root'] = str(self.project / '.refactor_validation/memory_pg17')
        with self.assertRaises(RuntimeError):self.run_guard()
        self.manifest = original
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(RuntimeError):self.run_guard()
        self.connect.assert_not_called()

    def test_admin_cluster_mismatch_stops_before_user_connection_and_never_skips(self):
        original = self.admin_identity.copy()
        for key, value in [('system_identifier','99999'),('database_oid',999),('server_port',55449),
                           ('major_version',16),('data_directory',str(self.project)),('read_only','off')]:
            with self.subTest(key=key):
                self.admin_identity = {**original, key:value}
                self.connect.reset_mock(side_effect=True)
                self.connect.side_effect=[self.admin_connection,self.writer_connection]
                with self.assertRaises(RuntimeError):self.run_guard()
                self.assertEqual(self.connect.call_count,1)
        self.writer_connection.execute.assert_not_called()

    def test_wrong_database_oid_or_connected_user_is_not_accepted(self):
        self.writer_identity['database_oid'] = 999
        with self.assertRaises(RuntimeError):self.run_guard()
        self.admin_connection.close.assert_called_once()
        self.writer_connection.close.assert_called_once()

    def test_errors_do_not_expose_credentials_or_server_messages(self):
        self.connect.side_effect=RuntimeError('password=synthetic-secret server-message')
        with self.assertRaises(RuntimeError) as caught:self.run_guard()
        self.assertNotIn('synthetic-secret',str(caught.exception))
        self.assertNotIn('server-message',str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)


if __name__ == '__main__':
    unittest.main()
