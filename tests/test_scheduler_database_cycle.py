"""Offline one-cycle orchestration: real v4 validation, simulated DB commits.

No PostgreSQL connection, website request, real child or continuous scheduler is
started. Approval is mocked only inside these disposable fixture tests.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_scheduler as scheduler
import saras_dynamic_scraper as saras
import udise_dynamic_scraper as udise
from database import scrape_pipeline as pipeline
from school_scraping.database_only import DatabaseOnlyStore
from school_scraping.supervision import SupervisionResult


ROOT = Path(__file__).resolve().parents[1]


def load_fixture(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


browser_fixtures = load_fixture('cycle_browser_fixtures', 'test_saras_dynamic.py')
database_fixtures = load_fixture('cycle_database_fixtures', 'test_database_only.py')
raw_fixtures = database_fixtures.fixtures


class SchedulerDatabaseCycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        config = json.loads((ROOT / 'config/states_pincodes.json').read_text(encoding='utf-8'))
        config['scheduler'].update(enabled=True, time='23:00', timezone='Asia/Kolkata')
        for section in config['sources'].values():
            for location in section['locations']:
                location['enabled'] = False
        self.config = self.root / 'disposable-config.json'
        self.config.write_text(json.dumps(config), encoding='utf-8')
        self.profile = self.root / 'mock-approval.json'
        self.profile.write_text(json.dumps({'schema_version': 1, 'approved': True,
            'approval_reference': 'OFFLINE UNIT TEST ONLY',
            'scheduler': {'approved': True, 'hard_timeout_seconds': 2},
            'database': {'host': '127.0.0.1', 'port': 55449, 'name': 'section13b_test_mock_cycle',
                'timeouts': {'connect_seconds': 1, 'statement_ms': 1000, 'lock_ms': 500},
                'tls': {'mode': 'approved-local', 'approved_local_exception': True,
                        'reason': 'Mock connection only; no actual server access'}}}), encoding='utf-8')
        self.ids = {'saras': ('1630001', '1630002'), 'udise': ('03000000001', '03000000002')}
        self.ledger, self.stores, self.reports, self.validated, self.commands = [], {}, {}, {}, []
        self.failure = None

    def args(self, source='both'):
        argv = ['--config', str(self.config), '--source', source, '--state', 'Punjab',
            '--storage-mode', 'database', '--new-only', '--database', '--limit', '2',
            '--academic-year', '2025-26', '--year-id', '12', '--delay', '0',
            '--data-dir', str(self.root / 'data'), '--state-dir', str(self.root / 'state'),
            '--report-dir', str(self.root / 'reports'), '--udise-school-list', str(self.root / 'mock-list.json'),
            '--production-profile', str(self.profile), '--once']
        args = scheduler.build_parser().parse_args(argv)
        scheduler.prepare_args(args, argv)
        return args

    def store(self, source, academic_year=''):
        fixture = self

        class Transaction(database_fixtures.FakeTransaction):
            def __exit__(self, kind, value, traceback):
                stage = self.connection.import_stage
                if kind is None and stage == 'data' and fixture.failure == 'commit':
                    self.connection.import_stage = None
                    raise OSError('Simulated database commit failure')
                result = super().__exit__(kind, value, traceback)
                if kind is None and stage is not None:
                    fixture.ledger.append((source, 'SIMULATED_COMMIT_' + stage))
                    self.connection.import_stage = None
                return result

        class Connection(database_fixtures.FakeConnection):
            import_stage = None
            def transaction(self):
                return Transaction(self)

        connection = Connection()
        store = DatabaseOnlyStore(source, academic_year, connect=lambda: connection)
        self.stores[source] = store
        # SQL reads/projector writes are mocked; batch lookup, runtime policy,
        # import transaction boundaries and post-commit acknowledgement are real.
        def lookup(conn, identifiers):
            self.ledger.append((source, 'lookup', tuple(identifiers)))
            if self.failure == 'lookup':
                raise OSError('Simulated unavailable database')
            return {identifier for identifier in identifiers if identifier == self.ids[source][0]}
        store._lookup = lookup
        store.tasks = lambda scope_key=None: []
        store.checkpoint = lambda identifier='', scope_key='': None
        def apply(conn, event):
            self.ledger.append((source, event['kind']))
            if event['kind'] == 'import_requested':
                conn.import_stage = 'data'
                store._memory.import_handler(conn, event['payload'])
            elif event['kind'] == 'import_acknowledged':
                conn.import_stage = 'ack'
            return True
        store._memory._apply_db = apply
        return store

    def import_in_memory(self, connection, payload):
        raw, _, _, logical, observed = pipeline.prepared_input(payload)
        source, identifier = payload['source'], payload['school_identifier']
        _, canonical = pipeline.map_and_validate(source, raw, logical, 'punjab', identifier, observed)
        self.assertEqual(canonical['metadata']['schema_version'], '4.0')
        self.assertEqual(canonical['identity_location']['pincode'],
            raw['school_details']['Pin Code'] if source == 'saras' else raw['school_summary']['pincode'])
        self.validated[source] = copy.deepcopy(canonical)
        self.ledger.append((source, 'real_v4_validation', identifier))
        return {'status': 'imported', 'source_record_pk': 1, 'entity_id': 2,
            'school_identifier': identifier, 'source': source}

    def child(self, command, **kwargs):
        self.commands.append(command)
        source = 'saras' if Path(command[1]).name == 'saras_dynamic_scraper.py' else 'udise'
        self.assertEqual(kwargs['timeout'], 2)
        self.assertEqual(kwargs['env']['DB_STATEMENT_TIMEOUT_MS'], '1000')
        self.assertEqual(kwargs['env']['PYTHON_DOTENV_DISABLED'], '1')
        for flag in ('--storage-mode', '--database', '--new-only', '--limit'):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index('--storage-mode') + 1], 'database')
        self.assertEqual(command[command.index('--limit') + 1], '2')
        if source == 'saras':
            browser = browser_fixtures.ExhaustiveBrowser({'PUNJAB': [browser_fixtures.listing(value)
                for value in self.ids[source]]})
            original_detail = browser.detail
            def detail(school):
                self.ledger.append((source, 'detail', school['affiliation_number']))
                return original_detail(school)
            browser.detail = detail
            report = saras.run(saras.build_parser().parse_args(command[2:]), browser)
            self.reports[source] = report
            code = 0 if report['status'] == 'completed' else 1
        else:
            raws = {identifier: raw_fixtures.udise_raw(identifier) for identifier in self.ids[source]}
            rows = [copy.deepcopy(raws[identifier]['school_summary']) for identifier in self.ids[source]]
            from unittest.mock import Mock
            client = Mock()
            client.verify_year.return_value = 12
            client.catalog.return_value = [{'stateName': 'Punjab', 'stateId': 3}]
            sections = {endpoint: section for section, (_, endpoint) in udise.DETAILS.items()}
            def response(endpoint, parameters):
                identifier = parameters['udiseSchCode']
                self.ledger.append((source, 'detail', identifier))
                return {'success': True, 'response': {'status': True,
                    'data': copy.deepcopy(raws[identifier][sections[endpoint]])}}
            client.get_json.side_effect = response
            def capture(directory, name, report):
                self.reports[source] = copy.deepcopy(report)
                return self.root / 'report-not-written.json'
            with (patch.object(udise, 'UdiseClient', return_value=client),
                  patch.object(udise, 'load_lists', return_value=rows),
                  patch.object(udise, 'write_report', side_effect=capture),
                  contextlib.redirect_stdout(io.StringIO())):
                code = udise.run_job(udise.parser().parse_args(command[2:]))
        return SupervisionResult(code, False, False, 0.01, True, False)

    def cycle(self, *, source='both', approved=True):
        args = self.args(source)
        self.assertEqual((args.time, args.timezone), ('23:00', 'Asia/Kolkata'))
        self.assertEqual(len(scheduler.build_commands(args)), 2 if source == 'both' else 1)
        events = []
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'DB_PASSWORD': 'synthetic-private-value', 'UDISE_HEADERS_JSON': '{}'}, clear=True))
            checker = stack.enter_context(patch('school_scraping.production_safety.production_preflight',
                return_value={'status': 'PASS' if approved else 'BLOCKED', 'errors': []}))
            supervise = stack.enter_context(patch.object(scheduler, 'supervise', side_effect=self.child))
            stack.enter_context(patch.object(scheduler, '_memory', side_effect=AssertionError('No local recovery journal')))
            stack.enter_context(patch.object(scheduler.subprocess, 'Popen', side_effect=AssertionError('No real child')))
            stack.enter_context(patch('psycopg.connect', side_effect=AssertionError('No PostgreSQL connection')))
            stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('No live source')))
            stack.enter_context(patch('school_scraping.database_only.DatabaseOnlyStore', side_effect=self.store))
            stack.enter_context(patch.object(pipeline, 'import_scraped_record', side_effect=self.import_in_memory))
            stack.enter_context(patch.object(saras, 'RawStore', side_effect=AssertionError('No school file fallback')))
            stack.enter_context(patch.object(udise, 'RawStore', side_effect=AssertionError('No school file fallback')))
            for method in ('emit', '_append', 'replay'):
                stack.enter_context(patch('school_scraping.memory.MemoryStore.' + method,
                    side_effect=AssertionError('No legacy journal or replay')))
            results = scheduler.run_daily_job(args, threading.Event(),
                lambda kind, **fields: events.append((kind, fields)))
        self.assertNotIn('synthetic-private-value', json.dumps(events))
        for name in ('data', 'state'):
            self.assertEqual(list((self.root / name).rglob('*.json')), [])
            self.assertEqual(list((self.root / name).rglob('*.jsonl')), [])
        self.assertTrue(all(store.closed for store in self.stores.values()))
        return results, checker.call_count, supervise.call_count

    def test_one_cycle_both_sources_skip_existing_validate_new_and_commit_before_ack(self):
        results, checked, supervised = self.cycle()
        self.assertEqual([row['status'] for row in results], ['success', 'success'])
        self.assertEqual((checked, supervised), (2, 2))
        self.assertEqual(set(self.validated), {'saras', 'udise'})
        for source, (existing, fresh) in self.ids.items():
            with self.subTest(source=source):
                trace = [row for row in self.ledger if row[0] == source]
                details = [row[2] for row in trace if row[1] == 'detail']
                self.assertNotIn(existing, details)
                self.assertEqual(set(details), {fresh})
                self.assertEqual(len(details), 1 if source == 'saras' else 4)
                stages = [row[1] for row in trace]
                self.assertLess(stages.index('lookup'), stages.index('detail'))
                self.assertLess(stages.index('real_v4_validation'), stages.index('SIMULATED_COMMIT_data'))
                self.assertLess(stages.index('SIMULATED_COMMIT_data'), stages.index('import_acknowledged'))
                self.assertLess(stages.index('import_acknowledged'), stages.index('SIMULATED_COMMIT_ack'))
                self.assertEqual(self.reports[source]['counts']['existing_records_skipped'], 1)
                self.assertEqual(self.reports[source]['counts']['import_acknowledgements'], 1)

    def test_failed_preflight_launches_nothing(self):
        results, _, supervised = self.cycle(approved=False)
        self.assertTrue(all(row['status'] == 'failed' for row in results))
        self.assertEqual(supervised, 0)
        self.assertFalse(self.stores)

    def test_database_lookup_failure_stops_without_details_or_file_fallback(self):
        self.failure = 'lookup'
        results, _, _ = self.cycle(source='saras')
        self.assertEqual(results[0]['status'], 'failed')
        self.assertFalse(any(row[1] in ('detail', 'import_acknowledged') for row in self.ledger))
        self.assertEqual(self.reports['saras']['counts']['new_imports_committed'], 0)

    def test_simulated_commit_failure_never_acknowledges_or_falls_back(self):
        self.failure = 'commit'
        results, _, _ = self.cycle(source='saras')
        self.assertEqual(results[0]['status'], 'failed')
        self.assertIn('saras', self.validated)
        self.assertFalse(any(row[1] in ('SIMULATED_COMMIT_data', 'import_acknowledged') for row in self.ledger))
        self.assertEqual(self.reports['saras']['counts']['new_imports_committed'], 0)
        self.assertEqual(self.reports['saras']['counts']['import_acknowledgements'], 0)


if __name__ == '__main__':
    unittest.main()
