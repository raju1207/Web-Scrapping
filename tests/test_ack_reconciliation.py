"""Read-only reconciliation; optional writes are isolated test fixtures only."""
import copy
from contextlib import contextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import Mock

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from school_scraping.ack_reconciliation import reconcile_imports


class FakeConnection:
    def __init__(self, completed=(), checkpoints=(), *, busy=False, read_only='on'):
        self.info = Mock(transaction_status=TransactionStatus.INTRANS if busy else TransactionStatus.IDLE)
        self.calls = []
        self.completed = completed
        self.checkpoints = checkpoints
        self.read_only = read_only
        self.commit = Mock(side_effect=AssertionError('No explicit commit'))
        self.rollback = Mock(side_effect=AssertionError('Do not modify caller transaction'))

    @contextmanager
    def transaction(self):
        yield

    def execute(self, query, parameters=None):
        self.calls.append((query, parameters))
        if 'current_setting' in query:
            return Mock(fetchone=lambda: (self.read_only,))
        values = self.completed if "e.kind='import_completed'" in query else self.checkpoints
        return Mock(fetchall=lambda: [(row,) for row in values])


def completed(**changes):
    return {'completion_event_id': 'completion-1', 'source': 'saras', 'identifier': '2730847',
            'academic_year': '', 'committed': True, 'ack_count': 1,
            'latest_observation': True, 'checkpoint_consistent': True, **changes}


class AckReconciliationTests(unittest.TestCase):
    def test_verified_committed_and_acknowledged_import_passes_without_writes(self):
        connection = FakeConnection([completed()])
        result = reconcile_imports(connection)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['counts']['committed_imports_verified'], 1)
        self.assertEqual(result['mutations'], 0)
        self.assertIn('READ ONLY', connection.calls[0][0])
        self.assertTrue(all(q.startswith(('SET TRANSACTION', 'SELECT', '\nSELECT')) for q, _ in connection.calls))
        connection.commit.assert_not_called()
        connection.rollback.assert_not_called()

    def test_missing_postcommit_acknowledgement_is_reported_without_replay(self):
        result = reconcile_imports(FakeConnection([completed(ack_count=0)]))
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertEqual(result['counts']['missing_acknowledgements'], 1)
        self.assertEqual(result['counts']['committed_imports_verified'], 1)
        self.assertEqual(result['findings'][0]['code'], 'missing_acknowledgement')

    def test_duplicate_ack_and_broken_commit_evidence_are_distinguished(self):
        result = reconcile_imports(FakeConnection([completed(ack_count=2, committed=False)]))
        self.assertEqual(result['counts']['duplicate_acknowledgements'], 1)
        self.assertEqual(result['counts']['inconsistent_imports'], 1)

    def test_inconsistent_checkpoint_detected_and_deduplicated(self):
        checkpoint = {'source': 'saras', 'identifier': '2730847', 'academic_year': '', 'consistent': False}
        result = reconcile_imports(FakeConnection([completed(checkpoint_consistent=False)], [checkpoint]))
        self.assertEqual(result['counts']['inconsistent_checkpoints'], 1)

    def test_older_observation_does_not_invalidate_a_newer_checkpoint(self):
        result = reconcile_imports(FakeConnection([completed(latest_observation=False, checkpoint_consistent=False)]))
        self.assertEqual(result['status'], 'PASS')

    def test_active_caller_transaction_is_never_committed_or_rolled_back(self):
        connection = FakeConnection(busy=True)
        result = reconcile_imports(connection)
        self.assertEqual(result['errors'], ['idle_connection_required'])
        self.assertEqual(connection.calls, [])

    def test_read_only_failure_stops_before_scans_and_hides_server_error(self):
        connection = FakeConnection(read_only='off')
        result = reconcile_imports(connection)
        self.assertEqual(result['errors'], ['database_read_failed'])
        self.assertEqual(len(connection.calls), 2)
        connection = FakeConnection()
        connection.execute = Mock(side_effect=OSError('password=secret'))
        self.assertNotIn('secret', json.dumps(reconcile_imports(connection)))

    def test_filter_values_are_parameters_not_interpolated_sql(self):
        connection = FakeConnection()
        value = "run'; DROP TABLE ignored; --"
        result = reconcile_imports(connection, source='udise', run_id=value, limit=4)
        self.assertEqual(result['status'], 'PASS')
        for query, parameters in connection.calls[2:]:
            self.assertNotIn(value, query)
            self.assertEqual(parameters, ('udise', 'udise', value, value, 5))

    def test_invalid_filter_and_unbounded_limits_fail_before_database_access(self):
        connection = FakeConnection()
        for values in ({'source': 'other'}, {'limit': 0}, {'limit': 10001}, {'limit': True}, {'run_id': ''}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                reconcile_imports(connection, **values)
        self.assertEqual(connection.calls, [])

    def test_truncation_never_claims_a_complete_healthy_audit(self):
        for connection in (FakeConnection([completed(), completed()]),
                           FakeConnection(checkpoints=[{'consistent': True}, {'consistent': True}])):
            with self.subTest(connection=connection):
                result = reconcile_imports(connection, limit=1)
                self.assertEqual(result['status'], 'BLOCKED')
                self.assertTrue(result['truncated'])
                self.assertIn('scan_limit_reached', result['errors'])

    def test_payloads_and_malformed_identifiers_are_not_in_report(self):
        row = completed(identifier='private-password', committed=False)
        row['payload'] = {'password': 'secret'}
        result = reconcile_imports(FakeConnection([row]))
        self.assertEqual(result['findings'][0]['school_identifier'], 'invalid')
        self.assertNotIn('secret', json.dumps(result))


@unittest.skipUnless(os.getenv('SCHOOL_SCRAPING_SECTION13B_TEST_DSN'), 'Dedicated Section13B disposable DSN not supplied')
class AckReconciliationPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['SCHOOL_SCRAPING_SECTION13B_TEST_DSN']
        settings = conninfo_to_dict(cls.dsn)
        if (settings.get('host') != '127.0.0.1' or settings.get('port') != '55449'
                or not settings.get('dbname', '').startswith('section13b_test_')):
            raise unittest.SkipTest('Only explicitly named Section13B disposable targets are permitted')
        with psycopg.connect(cls.dsn, autocommit=True, options='-c default_transaction_read_only=on') as connection:
            row = connection.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),"
                "current_setting('data_directory'),current_setting('server_version_num')::integer,"
                "(SELECT system_identifier::text FROM pg_control_system())").fetchone()
            root = Path(__file__).resolve().parents[1]
            expected = root / '.refactor_validation/section10a_backup_restore_test/restore_cluster/pgdata'
            if (row[0] != settings['dbname'] or row[1] != '127.0.0.1' or row[2] != 55449
                    or Path(row[3]).resolve() != expected or not 170000 <= row[4] < 180000
                    or row[5] != '7708345824376186472'):
                raise unittest.SkipTest('Section13B cluster identity failed')

    def builders(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('ack_school_builders',
            Path(__file__).resolve().parent / 'test_scrape_pipeline.py')
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)
        return fixtures

    def test_real_pipeline_missing_ack_and_checkpoint_detection_is_read_only(self):
        from school_scraping.database_only import DatabaseOnlyStore, DatabaseOnlyError
        # These fixture builders are imported only after the disposable guard.
        import importlib.util
        fixtures = self.builders()
        spec = importlib.util.spec_from_file_location('ack_commit_failure_helpers',
            Path(__file__).resolve().parent / 'test_database_only.py')
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        identifier = str(uuid.uuid4().int % 9000000 + 1000000)
        store = DatabaseOnlyStore('saras', connect=lambda: helpers.FailCommitConnection(
            psycopg.connect(self.dsn, autocommit=True), 2))
        self.addCleanup(store.close)
        with self.assertRaises(DatabaseOnlyError):
            store.import_record(identifier, fixtures.saras_raw(identifier, 'Section13B synthetic'), 'Punjab')
        with psycopg.connect(self.dsn, autocommit=True) as observer:
            before = observer.execute('SELECT count(*) FROM school_scraping.scrape_events').fetchone()[0]
            result = reconcile_imports(observer, source='saras', run_id=store.run_id)
            self.assertEqual(result['counts']['missing_acknowledgements'], 1)
            self.assertEqual(result['counts']['committed_imports_verified'], 1)
            self.assertEqual(result['counts']['inconsistent_checkpoints'], 0)
            self.assertEqual(before, observer.execute('SELECT count(*) FROM school_scraping.scrape_events').fetchone()[0])
            with observer.transaction():
                observer.execute("UPDATE school_scraping.scrape_checkpoints SET imported=false "
                                 "WHERE source='saras' AND school_identifier=%s", (identifier,))
            result = reconcile_imports(observer, source='saras', run_id=store.run_id)
            self.assertEqual(result['counts']['inconsistent_checkpoints'], 1)

    def test_udise_committed_year_snapshot_is_required_for_consistent_evidence(self):
        from school_scraping.database_only import DatabaseOnlyStore
        identifier = str(uuid.uuid4().int % 90000000000 + 10000000000)
        store = DatabaseOnlyStore('udise', '2025-26',
                                  connect=lambda: psycopg.connect(self.dsn, autocommit=True))
        self.addCleanup(store.close)
        store.import_record(identifier, self.builders().udise_raw(identifier, '2025-26',
                            'Section13B synthetic yearly evidence'), 'Punjab')
        with psycopg.connect(self.dsn, autocommit=True) as observer:
            result = reconcile_imports(observer, source='udise', run_id=store.run_id)
            self.assertEqual(result['status'], 'PASS', result)
            snapshot_id, archive_id, original = observer.execute(
                'SELECT y.snapshot_id,y.source_archive_file_id,y.snapshot_payload '
                'FROM school_scraping.school_yearly_snapshots y '
                'JOIN school_scraping.school_source_records s USING(source_record_pk) '
                "WHERE s.source_system='udise' AND s.udise_code=%s AND y.academic_year='2025-26'",
                (identifier,)).fetchone()
            for key, invalid in (('academic_year', '2024-25'), ('udise_code', '99999999999'),
                                 ('record_id', 'different-synthetic-identity')):
                with self.subTest(corrupted_snapshot_field=key):
                    corrupted = {**original, key: invalid}
                    # Keep the synthetic archive consistent with the corrupt
                    # payload: the school/year/record identity checks must catch it.
                    with observer.transaction():
                        observer.execute('UPDATE school_scraping.school_yearly_snapshots '
                                         'SET snapshot_payload=%s WHERE snapshot_id=%s',
                                         (Jsonb(corrupted), snapshot_id))
                        observer.execute('UPDATE school_scraping.json_file_archive SET payload=%s WHERE file_id=%s',
                                         (Jsonb([corrupted]), archive_id))
                    result = reconcile_imports(observer, source='udise', run_id=store.run_id)
                    self.assertEqual(result['counts']['inconsistent_imports'], 1)
                    self.assertEqual(result['counts']['inconsistent_checkpoints'], 1)
            with observer.transaction():
                observer.execute("UPDATE school_scraping.school_yearly_snapshots SET schema_version='invalid', "
                    'snapshot_payload=%s WHERE snapshot_id=%s', (Jsonb(original), snapshot_id))
                observer.execute('UPDATE school_scraping.json_file_archive SET payload=%s WHERE file_id=%s',
                                 (Jsonb([original]), archive_id))
            result = reconcile_imports(observer, source='udise', run_id=store.run_id)
            self.assertEqual(result['counts']['inconsistent_imports'], 1)
            self.assertEqual(result['counts']['inconsistent_checkpoints'], 1)

    def test_corrupt_request_or_acknowledgement_never_claims_success(self):
        from school_scraping.database_only import DatabaseOnlyStore
        identifier = str(uuid.uuid4().int % 9000000 + 1000000)
        store = DatabaseOnlyStore('saras', connect=lambda: psycopg.connect(self.dsn, autocommit=True))
        self.addCleanup(store.close)
        store.import_record(identifier, self.builders().saras_raw(identifier, 'Section13B receipt fixture'), 'Punjab')
        with psycopg.connect(self.dsn, autocommit=True) as observer:
            events = {row[1]: {'event_id': row[0], 'kind': row[1], 'occurred_at': row[2], 'payload': row[3]}
                for row in observer.execute('SELECT event_id,kind,occurred_at,payload '
                    "FROM school_scraping.scrape_events WHERE source='saras' AND run_id=%s AND kind=ANY(%s)",
                    (store.run_id, ['import_requested', 'import_completed', 'import_acknowledged'])).fetchall()}
            self.assertEqual(reconcile_imports(observer, source='saras', run_id=store.run_id)['status'], 'PASS')
            requested = events['import_requested']
            completion = events['import_completed']
            ack = events['import_acknowledged']

            def replace(event, *, payload=None, kind=None, occurred_at=None):
                with observer.transaction():
                    observer.execute('UPDATE school_scraping.scrape_events '
                        'SET payload=%s,kind=%s,occurred_at=%s WHERE event_id=%s',
                        (Jsonb(event['payload'] if payload is None else payload),
                         event['kind'] if kind is None else kind,
                         event['occurred_at'] if occurred_at is None else occurred_at, event['event_id']))

            for key, value in (('raw_sha256', '0' * 64), ('content_hash', '0' * 64),
                               ('school_identifier', '9999999'), ('academic_year', '2024-25')):
                with self.subTest(corrupted_request_field=key):
                    replace(requested, payload={**requested['payload'], key: value})
                    result = reconcile_imports(observer, source='saras', run_id=store.run_id)
                    self.assertEqual(result['counts']['inconsistent_imports'], 1)
                    self.assertEqual(result['counts']['missing_acknowledgements'], 1)
                    replace(requested)
            with self.subTest(missing_referenced_request=True):
                replace(completion, payload={**completion['payload'], 'import_event_id': 'missing-synthetic-request'})
                result = reconcile_imports(observer, source='saras', run_id=store.run_id)
                self.assertEqual(result['counts']['inconsistent_imports'], 1)
                replace(completion)
            with self.subTest(wrong_request_kind=True):
                replace(requested, kind='synthetic_invalid_request_kind')
                self.assertEqual(reconcile_imports(observer, source='saras', run_id=store.run_id)
                                 ['counts']['inconsistent_imports'], 1)
                replace(requested)
            with self.subTest(request_after_completion=True):
                replace(requested, occurred_at=completion['occurred_at'] + timedelta(seconds=1))
                self.assertEqual(reconcile_imports(observer, source='saras', run_id=store.run_id)
                                 ['counts']['inconsistent_imports'], 1)
                replace(requested)
            for key in ('source_record_pk', 'entity_id', 'raw_archive_file_id', 'canonical_archive_file_id',
                        'event_raw_archive_file_id', 'event_canonical_archive_file_id', 'raw_sha256', 'content_hash'):
                with self.subTest(corrupted_ack_field=key):
                    replace(ack, payload={**ack['payload'], key: 'synthetic-wrong-result'})
                    result = reconcile_imports(observer, source='saras', run_id=store.run_id)
                    self.assertEqual(result['counts']['committed_imports_verified'], 1)
                    self.assertEqual(result['counts']['missing_acknowledgements'], 1)
                    self.assertEqual(result['status'], 'BLOCKED')
                    replace(ack)
            with self.subTest(ack_before_completion=True):
                replace(ack, occurred_at=completion['occurred_at'] - timedelta(seconds=1))
                self.assertEqual(reconcile_imports(observer, source='saras', run_id=store.run_id)
                                 ['counts']['missing_acknowledgements'], 1)
                replace(ack)
            self.assertEqual(reconcile_imports(observer, source='saras', run_id=store.run_id)['status'], 'PASS')


if __name__ == '__main__':
    unittest.main()
