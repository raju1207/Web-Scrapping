"""Offline source-contract checks: database decisions precede mock details."""
import contextlib
import copy
import importlib.util
import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import saras_dynamic_scraper as saras
import udise_dynamic_scraper as udise
from school_scraping.database_only import DatabaseOnlyError, validate_identifier
from school_scraping.incremental import DatabaseOnlyRuntime


ROOT = Path(__file__).resolve().parents[1]


def fixtures(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


browser_fixtures = fixtures('database_only_offline_browser', 'test_saras_dynamic.py')
raw_fixtures = fixtures('database_only_offline_raw', 'test_scrape_pipeline.py')


class FakeStore:
    def __init__(self, source, academic_year='', *, committed=None, lookup_error=False,
                 import_error=None):
        self.source, self.academic_year = source, academic_year
        self.committed = committed if committed is not None else set()
        self.lookup_error, self.import_error = lookup_error, import_error
        self.failed = self.closed = False
        self.lookups, self.imports, self.events = [], [], []
        self.checkpoints = {}
        self.stats = dict.fromkeys(('identifiers_checked', 'database_lookup_duration_seconds',
            'imports_committed', 'canonical_validated', 'import_acknowledgements',
            'concurrent_existing_skips'), 0)

    def lookup_committed(self, identifiers):
        values = [validate_identifier(self.source, x) for x in identifiers]
        self.lookups.append(values)
        if self.lookup_error:
            self.failed = True
            raise DatabaseOnlyError('Injected offline lookup failure')
        self.stats['identifiers_checked'] += len(values)
        return {x for x in values if (self.source, self.academic_year, x) in self.committed}

    def event(self, kind, payload):
        if self.failed:
            return {'synced': False, 'stopped': True}
        self.events.append((kind, copy.deepcopy(payload)))
        return {'synced': True, 'acknowledged': True}

    def tasks(self, scope_key=None):
        return []

    def checkpoint(self, identifier='', scope_key=''):
        return copy.deepcopy(self.checkpoints.get(identifier, {}))

    def import_record(self, identifier, raw_record, region, **kwargs):
        self.imports.append((identifier, copy.deepcopy(raw_record), region, kwargs))
        if self.import_error == 'commit':
            self.failed = True
            raise DatabaseOnlyError('Injected commit failure', commit_outcome_unknown=True)
        self.stats['imports_committed'] += 1
        self.stats['canonical_validated'] += 1
        if self.import_error == 'ack':
            self.failed = True
            raise DatabaseOnlyError('Injected post-commit ack failure', data_committed=True)
        self.stats['import_acknowledgements'] += 1
        self.committed.add((self.source, self.academic_year, identifier))
        return {'status': 'imported', 'acknowledged': True}

    def close(self):
        self.closed = True


class DatabaseOnlySourceOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.committed, self.stores = set(), []

    def factory(self, source, academic_year=''):
        store = FakeStore(source, academic_year, committed=self.committed,
            lookup_error=getattr(self, 'lookup_error', False),
            import_error=getattr(self, 'import_error', None))
        self.stores.append(store)
        return store

    def args(self, source, *extra):
        parser = saras.build_parser() if source == 'saras' else udise.parser()
        return parser.parse_args(['--state', 'Punjab', '--database', '--storage-mode', 'database',
            '--new-only', '--delay', '0', '--data-dir', str(self.root / 'data'),
            '--state-dir', str(self.root / 'state'), '--report-dir', str(self.root / 'reports'), *extra])

    @contextlib.contextmanager
    def guards(self, source):
        module = saras if source == 'saras' else udise
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('school_scraping.database_only.DatabaseOnlyStore', side_effect=self.factory))
            stack.enter_context(patch.object(module, 'RawStore', side_effect=AssertionError('No filesystem school store')))
            stack.enter_context(patch('school_scraping.memory.MemoryStore', side_effect=AssertionError('No local journal/replay store')))
            yield

    def no_school_files(self):
        for directory in (self.root / 'data', self.root / 'state'):
            self.assertEqual(list(directory.rglob('*.json')), [])
            self.assertEqual(list(directory.rglob('*.jsonl')), [])
        self.assertTrue(all(store.closed for store in self.stores))

    def run_saras(self, identifiers, *extra):
        browser = browser_fixtures.ExhaustiveBrowser({'PUNJAB': [browser_fixtures.listing(x) for x in identifiers]})
        with self.guards('saras'):
            report = saras.run(self.args('saras', *extra), browser)
        self.no_school_files()
        return report, browser

    def run_udise(self, identifiers, *extra):
        raws = {x: raw_fixtures.udise_raw(x) for x in identifiers}
        rows = [copy.deepcopy(raws[x]['school_summary']) for x in identifiers]
        client = Mock()
        client.verify_year.return_value = 12
        client.catalog.return_value = [{'stateName': 'Punjab', 'stateId': 3}]
        details = []
        sections = {endpoint: key for key, (_, endpoint) in udise.DETAILS.items()}
        def response(endpoint, parameters):
            code = parameters['udiseSchCode']; details.append((code, endpoint))
            return {'success': True, 'response': {'status': True,
                'data': copy.deepcopy(raws[code][sections[endpoint]])}}
        client.get_json.side_effect = response
        args = self.args('udise', *extra)
        args.school_list = [self.root / 'in-memory-list.json']
        report = {}
        def capture(directory, source, value):
            report.update(copy.deepcopy(value))
            return self.root / 'report-not-written.json'
        with self.guards('udise'), patch.object(udise, 'UdiseClient', return_value=client), \
                patch.object(udise, 'load_lists', return_value=rows), \
                patch.object(udise, 'write_report', side_effect=capture), \
                patch.dict('os.environ', {'UDISE_HEADERS_JSON': '{}'}), contextlib.redirect_stdout(io.StringIO()):
            code = udise.run_job(args)
        self.no_school_files()
        return code, report, details

    def test_saras_existing_skipped_before_detail_and_new_record_imported_in_memory(self):
        self.committed.add(('saras', '', '1630001'))
        report, browser = self.run_saras(['1630001', '1630002'])
        self.assertEqual(browser.detail_calls, ['1630002'])
        self.assertEqual(self.stores[0].lookups, [['1630001', '1630002']])
        self.assertEqual(report['counts']['new_imports_committed'], 1)
        self.assertEqual(report['counts']['import_acknowledgements'], 1)
        self.assertEqual(self.stores[0].imports[0][1]['school_details']['Pin Code'], '134001')
        self.assertFalse(report['coverage']['details_complete'])
        self.assertFalse(report['coverage']['scope_complete'])

    def test_saras_limit_counts_existing_candidate_and_avoids_later_lookup_and_detail(self):
        self.committed.add(('saras', '', '1630001'))
        report, browser = self.run_saras(['1630001', '1630002'], '--limit', '1')
        self.assertEqual(browser.detail_calls, [])
        self.assertEqual(self.stores[0].lookups, [['1630001']])
        self.assertEqual(report['counts']['listing_candidates_examined'], 1)
        self.assertEqual(report['counts']['detail_fetches_avoided'], 1)
        self.assertEqual(report['counts']['local_school_json_files_generated'], 0)

    def test_saras_invalid_batch_identifier_prevents_lookup_and_details(self):
        report, browser = self.run_saras(['invalid', '1630002'])
        self.assertEqual(report['status'], 'partial_failure')
        self.assertEqual(report['counts']['failed'], 1)
        self.assertEqual(browser.detail_calls, [])
        self.assertEqual(self.stores[0].lookups, [])

    def test_saras_lookup_failure_stops_before_any_detail(self):
        self.lookup_error = True
        report, browser = self.run_saras(['1630001', '1630002'])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(browser.detail_calls, [])
        self.assertEqual(report['counts']['import_acknowledgements'], 0)
        self.assertIn('database_failure', report)

    def test_saras_commit_failure_stops_later_candidate_and_does_not_ack(self):
        self.import_error = 'commit'
        report, browser = self.run_saras(['1630001', '1630002'])
        self.assertEqual(browser.detail_calls, ['1630001'])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['counts']['new_imports_committed'], 0)
        self.assertEqual(report['counts']['import_acknowledgements'], 0)
        self.assertTrue(report['database_failure']['commit_outcome_unknown'])

    def test_saras_post_commit_ack_failure_reports_committed_without_false_ack(self):
        self.import_error = 'ack'
        report, browser = self.run_saras(['1630001', '1630002'])
        self.assertEqual(browser.detail_calls, ['1630001'])
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['counts']['new_imports_committed'], 1)
        self.assertEqual(report['counts']['import_acknowledgements'], 0)
        self.assertEqual(report['counts']['pending_acknowledgements'], 1)
        self.assertTrue(report['database_failure']['data_commit_confirmed'])

    def test_udise_existing_year_skipped_missing_year_and_new_code_fetched(self):
        existing, missing_year, fresh = '03000000001', '03000000002', '03000000003'
        self.committed.update({('udise', '2025-26', existing), ('udise', '2024-25', missing_year)})
        code, report, details = self.run_udise([existing, missing_year, fresh])
        self.assertEqual(code, 0)
        self.assertEqual({x for x, _ in details}, {missing_year, fresh})
        self.assertEqual(len(details), 8)
        self.assertEqual(report['counts']['existing_records_skipped'], 1)
        self.assertEqual(report['counts']['new_imports_committed'], 2)
        self.assertEqual(report['counts']['import_acknowledgements'], 2)
        self.assertEqual(self.stores[0].academic_year, '2025-26')
        self.assertEqual([x[0] for x in self.stores[0].imports], [missing_year, fresh])

    def test_udise_limit_counts_existing_year_and_does_not_fetch_next_candidate(self):
        self.committed.add(('udise', '2025-26', '03000000001'))
        code, report, details = self.run_udise(['03000000001', '03000000002'], '--limit', '1')
        self.assertEqual(code, 0)
        self.assertEqual(details, [])
        self.assertEqual(self.stores[0].lookups, [['03000000001']])
        self.assertEqual(report['counts']['listing_candidates_examined'], 1)
        self.assertEqual(report['counts']['new_imports_committed'], 0)

    def test_udise_invalid_identifier_never_enters_database_lookup(self):
        code, report, details = self.run_udise(['invalid', '03000000002'], '--limit', '1')
        self.assertEqual(code, 1)
        self.assertEqual(self.stores[0].lookups, [])
        self.assertEqual(details, [])
        self.assertEqual(report['counts']['failed'], 1)

    def test_udise_lookup_failure_stops_before_all_detail_calls(self):
        self.lookup_error = True
        code, report, details = self.run_udise(['03000000001', '03000000002'])
        self.assertEqual(code, 1)
        self.assertEqual(details, [])
        self.assertEqual(report['counts']['import_acknowledgements'], 0)
        self.assertIn('database_failure', report)

    def test_udise_commit_failure_stops_later_school_and_never_acknowledges(self):
        self.import_error = 'commit'
        code, report, details = self.run_udise(['03000000001', '03000000002'])
        self.assertEqual(code, 1)
        self.assertEqual({x for x, _ in details}, {'03000000001'})
        self.assertEqual(report['counts']['new_imports_committed'], 0)
        self.assertEqual(report['counts']['import_acknowledgements'], 0)

    def policy_runtime(self, source, checkpoint, *, committed=True, refresh=False, now=None):
        identifier = '1630001' if source == 'saras' else '03000000001'
        args = self.args(source)
        args.new_only = False
        args.refresh = refresh
        args.refresh_days = 30
        clock = now or datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        store = FakeStore(source, '2025-26' if source == 'udise' else '')
        if committed:
            store.committed.add((source, store.academic_year, identifier))
        store.checkpoints[identifier] = copy.deepcopy(checkpoint)
        runtime = DatabaseOnlyRuntime(args, source, store=store, clock=lambda: clock)
        self.addCleanup(runtime.close)
        return runtime, store, identifier

    def test_incremental_recent_committed_completed_checkpoint_avoids_detail(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        checkpoint = {'checkpoint_status': 'completed', 'imported': True,
                      'last_detail_check': (now - timedelta(days=1)).isoformat()}
        for source in ['saras', 'udise']:
            with self.subTest(source=source):
                runtime, store, identifier = self.policy_runtime(source, checkpoint, now=now)
                self.assertTrue(runtime.skip_existing(identifier))
                self.assertEqual(runtime.metrics['detail_fetches_avoided'], 1)
                self.assertEqual(store.stats['identifiers_checked'], 1)
                self.assertEqual(store.imports, [])
                self.assertEqual(store.checkpoints[identifier], checkpoint)

    def test_incremental_stale_and_boundary_checkpoints_require_refresh(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        for source in ['saras', 'udise']:
            for age in [30, 31]:
                with self.subTest(source=source, age=age):
                    checkpoint = {'checkpoint_status': 'completed', 'imported': True,
                                  'last_detail_check': (now - timedelta(days=age)).isoformat()}
                    runtime, store, identifier = self.policy_runtime(source, checkpoint, now=now)
                    self.assertFalse(runtime.skip_existing(identifier))
                    self.assertEqual(runtime.metrics['detail_fetches_avoided'], 0)
                    self.assertEqual(store.events, [])

    def test_incremental_explicit_refresh_bypasses_recent_checkpoint(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        checkpoint = {'checkpoint_status': 'completed', 'imported': True,
                      'last_detail_check': now.isoformat()}
        for source in ['saras', 'udise']:
            with self.subTest(source=source):
                runtime, store, identifier = self.policy_runtime(source, checkpoint, refresh=True, now=now)
                self.assertFalse(runtime.skip_existing(identifier))
                self.assertEqual(store.stats['identifiers_checked'], 1)
                self.assertEqual(store.events, [])

    def test_incremental_pending_or_unimported_checkpoint_never_gets_ttl_skip(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        for source in ['saras', 'udise']:
            for status, imported in [('pending_import', False), ('completed', False), ('pending_import', True)]:
                with self.subTest(source=source, status=status, imported=imported):
                    checkpoint = {'checkpoint_status': status, 'imported': imported,
                                  'last_detail_check': now.isoformat()}
                    runtime, store, identifier = self.policy_runtime(source, checkpoint, now=now)
                    self.assertFalse(runtime.skip_existing(identifier))
                    self.assertEqual(store.events, [])
                    self.assertEqual(runtime.metrics['existing_records_skipped'], 0)

    def test_incremental_checkpoint_cannot_substitute_for_committed_school(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        for source in ['saras', 'udise']:
            for status, imported in [('pending_import', False), ('completed', True)]:
                with self.subTest(source=source, status=status):
                    checkpoint = {'checkpoint_status': status, 'imported': imported,
                                  'last_detail_check': now.isoformat()}
                    runtime, store, identifier = self.policy_runtime(source, checkpoint, committed=False, now=now)
                    self.assertFalse(runtime.skip_existing(identifier))
                    self.assertEqual(store.events, [])
                    self.assertEqual(store.stats['identifiers_checked'], 1)

    def test_incremental_repeated_recent_skips_do_not_reset_detail_freshness(self):
        now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
        checkpoint = {'checkpoint_status': 'completed', 'imported': True,
                      'last_detail_check': (now - timedelta(days=1)).isoformat()}
        for source in ['saras', 'udise']:
            for elapsed in [0, 1]:
                with self.subTest(source=source, elapsed=elapsed):
                    runtime, store, identifier = self.policy_runtime(source, checkpoint,
                        now=now + timedelta(days=elapsed))
                    self.assertTrue(runtime.skip_existing(identifier))
                    self.assertEqual(store.checkpoints[identifier], checkpoint)
                    self.assertEqual(runtime.checkpoint(identifier)['last_detail_check'], checkpoint['last_detail_check'])
                    self.assertEqual([kind for kind, _ in store.events], ['task_completed'])
                    self.assertNotIn('last_detail_check', store.events[0][1])


if __name__ == '__main__':
    unittest.main()
