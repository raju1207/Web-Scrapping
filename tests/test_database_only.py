"""Database-only pipeline contracts; real writes require a dedicated disposable DSN."""
import copy
import hashlib
import importlib.util
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

from database import scrape_pipeline as pipeline
from school_scraping.database_only import DatabaseOnlyError, DatabaseOnlyStore, validate_identifier

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('db_only_fixture_builders', ROOT / 'tests/test_scrape_pipeline.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class FakeTransaction:
    def __init__(self, connection):
        self.connection = connection
    def __enter__(self):
        self.number = self.connection.transactions = self.connection.transactions + 1
        return self
    def __exit__(self, kind, value, traceback):
        if kind is None:
            if self.number == self.connection.fail_commit:
                raise OSError('synthetic commit failure')
            self.connection.commits += 1
        return False


class FakeConnection:
    def __init__(self, fail_commit=None):
        self.transactions = self.commits = 0
        self.fail_commit = fail_commit
        self.execute = Mock()
        self.rollback = Mock()
        self.close = Mock()
    def transaction(self):
        return FakeTransaction(self)


class FailCommitConnection:
    """Run real transactions, rolling back the selected outer commit attempt."""
    def __init__(self, connection, fail_outer_commit):
        self.connection = connection
        self.fail_outer_commit = fail_outer_commit
        self.depth = self.outer_transactions = 0

    def __getattr__(self, name):
        return getattr(self.connection, name)

    @property
    def autocommit(self):
        return self.connection.autocommit

    @autocommit.setter
    def autocommit(self, value):
        self.connection.autocommit = value

    def transaction(self):
        owner = self
        underlying = self.connection.transaction()

        class Transaction:
            def __enter__(self):
                self.outer = owner.depth == 0
                if self.outer:
                    owner.outer_transactions += 1
                self.number = owner.outer_transactions
                result = underlying.__enter__()
                owner.depth += 1
                return result

            def __exit__(self, kind, value, traceback):
                try:
                    if kind is None and self.outer and self.number == owner.fail_outer_commit:
                        failure = OSError('Injected outer commit failure')
                        underlying.__exit__(OSError, failure, None)
                        raise failure
                    return underlying.__exit__(kind, value, traceback)
                finally:
                    owner.depth -= 1

        return Transaction()


class DatabaseOnlyPureTests(unittest.TestCase):
    def test_in_memory_input_never_reads_a_physical_file(self):
        payload = {'source': 'saras', 'school_identifier': '2730847', 'region': 'Delhi',
                   'raw_record': fixtures.saras_raw('2730847', 'In Memory School')}
        with (patch.object(Path, 'read_bytes', side_effect=AssertionError('No file reads')),
              patch.object(Path, 'is_file', side_effect=AssertionError('No file lookups'))):
            raw, content, checksum, logical, stamp = pipeline.prepared_input(payload)
        self.assertEqual(raw, payload['raw_record'])
        self.assertEqual(content, pipeline.json_bytes(raw))
        self.assertEqual(len(checksum), 64)
        self.assertEqual(logical, 'data/CBSE_SARAS/delhi/schools/2730847.json')
        self.assertIsNotNone(stamp.tzinfo)

    def test_inline_checksum_rejection_precedes_database_access(self):
        with self.assertRaisesRegex(pipeline.PipelineValidationError, 'checksum'):
            pipeline.import_scraped_record(None, {'source': 'saras', 'school_identifier': '2730847',
                'region': 'Delhi', 'raw_record': {}, 'raw_sha256': '0' * 64})

    def test_virtual_path_traversal_is_rejected(self):
        with self.assertRaises(pipeline.PipelineValidationError):
            pipeline.prepared_input({'source': 'saras', 'school_identifier': '2730847', 'raw_record': {},
                'logical_raw_path': 'data/../outside.json'})

    def test_invalid_listing_identifier_never_connects(self):
        for source, values in [('udise', ['123', 'a'*11, True, None, '٠'*11]),
                               ('saras', ['', '-1', '1.1', True, None])]:
            factory = Mock(side_effect=AssertionError('No connection'))
            store = DatabaseOnlyStore(source, '2025-26', connect=factory)
            for value in values:
                with self.subTest(source=source, value=repr(value)):
                    with self.assertRaises(ValueError):store.lookup_committed([value])
            factory.assert_not_called()
        self.assertEqual(validate_identifier('udise', '04010800107'), '04010800107')

    def test_explicit_port_is_required_without_defaulting_to_normal_postgres(self):
        environment = {'DB_HOST': '127.0.0.1', 'DB_NAME': 'unused_test',
                       'DB_USER': 'unused_test', 'DB_PASSWORD': 'synthetic-test-value'}
        with (patch.dict(os.environ, environment, clear=True), patch('dotenv.load_dotenv'),
              patch('psycopg.connect') as connect):
            store = DatabaseOnlyStore('saras')
            with self.assertRaises(DatabaseOnlyError):
                store.lookup_committed(['2730847'])
            connect.assert_not_called()
            self.assertTrue(store.failed)

    def test_lookup_uses_bounded_batches_and_one_connection(self):
        connection = FakeConnection(); factory = Mock(return_value=connection)
        store = DatabaseOnlyStore('saras', connect=factory)
        with patch.object(store, '_lookup', return_value=set()) as lookup:
            self.assertEqual(store.lookup_committed([str(x) for x in range(1,1002)]), set())
            self.assertEqual([len(call.args[1]) for call in lookup.call_args_list], [500,500,1])
            store.lookup_committed(['2000'])
        factory.assert_called_once()
        self.assertEqual(store.stats['identifiers_checked'], 1002)
        self.assertEqual(connection.commits, 2)

    def test_lookup_failure_stops_without_reconnection_or_fallback(self):
        connection = FakeConnection();factory = Mock(return_value=connection)
        store = DatabaseOnlyStore('saras', connect=factory)
        with patch.object(store, '_lookup', side_effect=OSError('synthetic unavailable')):
            with self.assertRaises(DatabaseOnlyError):store.lookup_committed(['2730847'])
        with self.assertRaises(DatabaseOnlyError):store.lookup_committed(['2730847'])
        self.assertFalse(store.event('run_finished', {})['synced'])
        factory.assert_called_once()

    def prepared_store(self, fail_commit=None):
        connection = FakeConnection(fail_commit);store = DatabaseOnlyStore('saras', connect=lambda:connection)
        events = []
        def apply(conn, event):
            events.append((event['kind'], conn.commits, copy.deepcopy(event['payload'])))
            if event['kind']=='import_requested':
                store._memory.import_handler(conn,event['payload'])
            return True
        store._memory._apply_db = Mock(side_effect=apply)
        return store, connection, events

    def test_acknowledgement_is_written_only_after_data_commit(self):
        store,connection,events=self.prepared_store()
        with patch.object(pipeline,'import_scraped_record',return_value={'status':'imported','source_record_pk':1,'entity_id':2}):
            result=store.import_record('2730847',fixtures.saras_raw('2730847','Memory School'),'Delhi')
        self.assertTrue(result['acknowledged'])
        self.assertEqual([x[:2] for x in events], [('import_requested',0),('import_completed',0),('task_completed',0),('import_acknowledged',1)])
        self.assertEqual(connection.commits,2)
        self.assertTrue(all('raw_record' not in x[2] and 'raw_path' not in x[2] and 'snapshot_path' not in x[2] for x in events))

    def test_commit_failure_never_acknowledges(self):
        store,connection,events=self.prepared_store(fail_commit=1)
        with patch.object(pipeline,'import_scraped_record',return_value={'status':'imported'}):
            with self.assertRaises(DatabaseOnlyError) as caught:
                store.import_record('2730847',fixtures.saras_raw('2730847','Memory School'),'Delhi')
        self.assertFalse(caught.exception.data_committed)
        self.assertEqual(store.stats['imports_committed'],0)
        self.assertEqual(store.stats['import_acknowledgements'],0)
        self.assertNotIn('import_acknowledged',[x[0] for x in events])
        self.assertTrue(store.failed)

    def test_ack_failure_distinguishes_already_committed_data(self):
        store,connection,events=self.prepared_store(fail_commit=2)
        with patch.object(pipeline,'import_scraped_record',return_value={'status':'imported'}):
            with self.assertRaises(DatabaseOnlyError) as caught:
                store.import_record('2730847',fixtures.saras_raw('2730847','Memory School'),'Delhi')
        self.assertTrue(caught.exception.data_committed)
        self.assertEqual(store.stats['imports_committed'],1)
        self.assertEqual(store.stats['import_acknowledgements'],0)
        self.assertTrue(store.failed)

    def test_concurrent_new_only_recheck_skips_before_pipeline(self):
        store,connection,events=self.prepared_store()
        with patch.object(store,'_lookup',return_value={'2730847'}),patch.object(pipeline,'import_scraped_record') as importer:
            result=store.import_record('2730847',fixtures.saras_raw('2730847','Memory School'),'Delhi',new_only=True)
        self.assertEqual(result['status'],'already_committed')
        self.assertFalse(result['acknowledged']);importer.assert_not_called()
        self.assertEqual(events,[])


@unittest.skipUnless(os.getenv('SCHOOL_SCRAPING_DATABASE_ONLY_TEST_DSN'), 'Dedicated disposable database-only DSN not supplied')
class DatabaseOnlyPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn=os.environ['SCHOOL_SCRAPING_DATABASE_ONLY_TEST_DSN']
        settings=conninfo_to_dict(cls.dsn)
        if (settings.get('host')!='127.0.0.1' or settings.get('port')!='55449'
                or not settings.get('dbname','').startswith('database_only_test_')):
            raise unittest.SkipTest('Not an explicitly named disposable database-only test DB')
        with psycopg.connect(cls.dsn,autocommit=True) as connection:
            d=connection.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),current_setting('data_directory'),current_setting('server_version')").fetchone()
            expected=ROOT/'.refactor_validation/section10a_backup_restore_test/restore_cluster/pgdata'
            if d[0]!=settings['dbname'] or d[1]!='127.0.0.1' or d[2]!=55449 or Path(d[3]).resolve()!=expected or not d[4].startswith('17.'):
                raise unittest.SkipTest('Disposable cluster identity is not the approved test environment')

    def setUp(self):
        self.observer=psycopg.connect(self.dsn,autocommit=True)
        self.addCleanup(self.observer.close)
        self.stores=[];self.addCleanup(lambda:[x.close() for x in self.stores])

    def store(self,source='saras',year='2025-26'):
        store=DatabaseOnlyStore(source,year,connect=lambda:psycopg.connect(self.dsn,autocommit=True))
        self.stores.append(store);return store

    def identifier(self,source='saras'):
        width=7 if source=='saras' else 11
        return str(uuid.uuid4().int%(9*10**(width-1))+10**(width-1))

    def raw(self,source,identifier,year='2025-26',suffix=''):
        name='Database Only Test '+identifier+suffix
        return fixtures.saras_raw(identifier,name) if source=='saras' else fixtures.udise_raw(identifier,year,name)

    def test_saras_raw_canonical_completion_and_postcommit_ack_without_files(self):
        identifier=self.identifier();store=self.store();raw=self.raw('saras',identifier)
        with patch.object(Path,'open',side_effect=AssertionError('Database import must not access files')):
            result=store.import_record(identifier,raw,'Punjab',new_only=True)
        self.assertTrue(result['acknowledged']);self.assertEqual(store.lookup_committed([identifier]),{identifier})
        archived=self.observer.execute('SELECT payload FROM school_scraping.json_file_archive WHERE file_id=%s',(result['raw_archive_file_id'],)).fetchone()[0]
        canonical=self.observer.execute('SELECT payload FROM school_scraping.json_file_archive WHERE file_id=%s',(result['canonical_archive_file_id'],)).fetchone()[0]
        self.assertEqual(archived,raw);self.assertEqual(canonical['metadata']['schema_version'],'4.0')
        self.assertEqual(canonical['identity_location']['pincode'],'141001')
        kinds=self.observer.execute('SELECT kind FROM school_scraping.scrape_events WHERE run_id=%s',(store.run_id,)).fetchall()
        self.assertIn(('import_requested',),kinds);self.assertIn(('import_completed',),kinds);self.assertIn(('import_acknowledged',),kinds)
        cp=store.checkpoint(identifier)
        self.assertTrue(cp['imported']);self.assertEqual(cp['checkpoint_status'],'completed')
        self.assertNotIn('raw_record',cp);self.assertIsNone(cp.get('raw_path'))

    def test_udise_lookup_requires_requested_year_snapshot(self):
        identifier=self.identifier('udise');first=self.store('udise','2025-26')
        first.import_record(identifier,self.raw('udise',identifier),'Punjab',new_only=True)
        second=self.store('udise','2026-27')
        self.assertEqual(second.lookup_committed([identifier]),set())
        second.import_record(identifier,self.raw('udise',identifier,'2026-27'),'Punjab',new_only=True)
        self.assertEqual(first.lookup_committed([identifier]),{identifier})
        self.assertEqual(second.lookup_committed([identifier]),{identifier})
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s',(identifier,)).fetchone()[0],1)
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_yearly_snapshots y JOIN school_scraping.school_source_records s USING(source_record_pk) WHERE s.udise_code=%s',(identifier,)).fetchone()[0],2)

    def test_validation_failure_rolls_back_school_events_and_checkpoint(self):
        identifier=self.identifier();store=self.store();raw=self.raw('saras',identifier)
        raw['school_details']['Affiliation Number']=self.identifier()
        with self.assertRaises(pipeline.PipelineValidationError):store.import_record(identifier,raw,'Punjab')
        self.assertEqual(store.lookup_committed([identifier]),set())
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.scrape_events WHERE run_id=%s',(store.run_id,)).fetchone()[0],0)
        self.assertIsNone(store.checkpoint(identifier))

    def test_school_commit_failure_leaves_no_school_or_completion_evidence(self):
        identifier = self.identifier()
        connection = FailCommitConnection(psycopg.connect(self.dsn, autocommit=True), 1)
        store = DatabaseOnlyStore('saras', connect=lambda: connection)
        self.stores.append(store)
        with self.assertRaises(DatabaseOnlyError) as caught:
            store.import_record(identifier, self.raw('saras', identifier), 'Punjab')
        self.assertFalse(caught.exception.data_committed)
        self.assertTrue(caught.exception.commit_outcome_unknown)
        self.assertEqual(store.stats['imports_committed'], 0)
        self.assertEqual(store.stats['import_acknowledgements'], 0)
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_source_records WHERE cbse_affiliation_number=%s', (identifier,)).fetchone()[0], 0)
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.scrape_events WHERE run_id=%s', (store.run_id,)).fetchone()[0], 0)
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.scrape_checkpoints WHERE school_identifier=%s', (identifier,)).fetchone()[0], 0)

    def test_ack_commit_failure_keeps_committed_school_without_false_ack(self):
        identifier = self.identifier()
        connection = FailCommitConnection(psycopg.connect(self.dsn, autocommit=True), 2)
        store = DatabaseOnlyStore('saras', connect=lambda: connection)
        self.stores.append(store)
        with self.assertRaises(DatabaseOnlyError) as caught:
            store.import_record(identifier, self.raw('saras', identifier), 'Punjab')
        self.assertTrue(caught.exception.data_committed)
        self.assertTrue(store.failed)
        self.assertEqual(store.stats['imports_committed'], 1)
        self.assertEqual(store.stats['canonical_validated'], 1)
        self.assertEqual(store.stats['import_acknowledgements'], 0)
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_source_records WHERE cbse_affiliation_number=%s', (identifier,)).fetchone()[0], 1)
        kinds = self.observer.execute('SELECT kind FROM school_scraping.scrape_events WHERE run_id=%s', (store.run_id,)).fetchall()
        self.assertEqual(kinds.count(('import_requested',)), 1)
        self.assertEqual(kinds.count(('import_completed',)), 1)
        self.assertNotIn(('import_acknowledged',), kinds)
        checkpoint = self.observer.execute('SELECT imported,checkpoint_status FROM school_scraping.scrape_checkpoints WHERE source=%s AND school_identifier=%s', ('saras', identifier)).fetchone()
        self.assertEqual(checkpoint, (True, 'completed'))
        self.assertTrue(store.event('run_finished', {})['stopped'])
        with self.assertRaises(DatabaseOnlyError):
            store.lookup_committed([identifier])

    def test_refresh_preserves_old_archived_versions(self):
        identifier=self.identifier();store=self.store();raw=self.raw('saras',identifier)
        observed=datetime.now(timezone.utc)
        first=store.import_record(identifier,raw,'Punjab',observed_at=observed)
        changed=self.raw('saras',identifier,suffix=' Updated')
        second=store.import_record(identifier,changed,'Punjab',observed_at=observed+timedelta(seconds=1))
        self.assertEqual(first['source_record_pk'],second['source_record_pk'])
        self.assertEqual(first['entity_id'],second['entity_id'])
        preserved=self.observer.execute('SELECT payload FROM school_scraping.json_file_archive WHERE content_sha256=%s',(first['raw_sha256'],)).fetchall()
        self.assertTrue(any(row[0]==raw for row in preserved))
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_source_records WHERE cbse_affiliation_number=%s',(identifier,)).fetchone()[0],1)

    def test_new_only_repeat_does_not_create_another_import(self):
        identifier=self.identifier();store=self.store();raw=self.raw('saras',identifier)
        store.import_record(identifier,raw,'Punjab',new_only=True)
        count=self.observer.execute('SELECT count(*) FROM school_scraping.scrape_events WHERE run_id=%s',(store.run_id,)).fetchone()[0]
        again=store.import_record(identifier,raw,'Punjab',new_only=True)
        self.assertEqual(again['status'],'already_committed')
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.scrape_events WHERE run_id=%s',(store.run_id,)).fetchone()[0],count)

    def test_legacy_pending_file_import_rolls_back_then_replays_without_fetch(self):
        from school_scraping.incremental import IncrementalRuntime
        from school_scraping.memory import MemoryStore
        spec = importlib.util.spec_from_file_location('disposable_legacy_runtime_fixtures', ROOT / 'tests/test_incremental.py')
        helpers = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helpers)
        with tempfile.TemporaryDirectory() as temporary:
            args = helpers.options(temporary, database=True)
            clock = helpers.Clock()
            identifier = self.identifier('udise')
            raw = helpers.raw_record('udise', identifier)
            path = helpers.save_record(args, 'udise', identifier, raw)
            raw_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            attempts = []

            def handler(connection, payload):
                result = pipeline.import_scraped_record(connection, payload)
                attempts.append(payload['raw_sha256'])
                if len(attempts) == 1:
                    raise RuntimeError('Injected legacy failure after real school writes')
                return result

            memory = MemoryStore(args.state_dir, database_enabled=True,
                connect=lambda: psycopg.connect(self.dsn), import_handler=handler, retry_seconds=0)
            runtime = IncrementalRuntime(args, 'udise', memory, clock)
            runtime.start()
            with self.assertLogs('school_scraping', level='WARNING'):
                self.assertEqual(runtime.persist(identifier, raw, path), 'pending_import')
            self.assertFalse(runtime.checkpoint(identifier)['imported'])
            self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s', (identifier,)).fetchone()[0], 0)
            requested = [event for event in memory._events() if event['kind'] == 'import_requested']
            self.assertEqual(len(requested), 1)
            snapshot = Path(requested[0]['payload']['snapshot_path'])
            snapshot_before = snapshot.read_bytes()
            journal_before = memory.journal.read_bytes()
            self.assertEqual(hashlib.sha256(snapshot_before).hexdigest(), raw_sha256)
            clock.advance(days=1)
            recovered = IncrementalRuntime(args, 'udise', memory, clock)
            # Only replaying the saved immutable event can recover this record;
            # a detail request or rewriting the original raw file is forbidden.
            with patch('requests.Session.get', side_effect=AssertionError('No detail refetch')):
                recovered.start()
                self.assertTrue(recovered.checkpoint(identifier)['imported'])
                self.assertFalse(recovered.detail_due(identifier, raw, path))
                self.assertEqual(recovered.persist(identifier, raw, path, fetched=False), 'already_imported')
            self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s', (identifier,)).fetchone()[0], 1)
            self.assertEqual(attempts, [raw_sha256, raw_sha256])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), raw_sha256)
            self.assertEqual(snapshot.read_bytes(), snapshot_before)
            self.assertTrue(memory.journal.read_bytes().startswith(journal_before))
            self.assertEqual(len([event for event in memory._events() if event['kind'] == 'import_requested']), 1)

    def test_concurrent_new_only_imports_keep_one_school_identity(self):
        identifier=self.identifier();raw=self.raw('saras',identifier);barrier=threading.Barrier(2)
        def worker():
            store=DatabaseOnlyStore('saras',connect=lambda:psycopg.connect(self.dsn,autocommit=True))
            try:
                self.assertEqual(store.lookup_committed([identifier]),set());barrier.wait(timeout=10)
                return store.import_record(identifier,raw,'Punjab',new_only=True)
            finally:store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:worker(),range(2)))
        self.assertEqual(sum(x.get('acknowledged',False) for x in results),1)
        self.assertEqual(sum(x['status']=='already_committed' for x in results),1)
        self.assertEqual(self.observer.execute('SELECT count(*) FROM school_scraping.school_source_records WHERE cbse_affiliation_number=%s',(identifier,)).fetchone()[0],1)
