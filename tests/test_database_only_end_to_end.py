"""Mock source discovery/details with the real pipeline in a disposable database."""
import contextlib
import copy
import importlib.util
import io
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

import saras_dynamic_scraper as saras
import udise_dynamic_scraper as udise
from school_scraping.database_only import DatabaseOnlyStore


ROOT = Path(__file__).resolve().parents[1]


def fixture_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tests' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source_fixtures = fixture_module('database_only_saras_browser', 'test_saras_dynamic.py')
raw_fixtures = fixture_module('database_only_raw_builders', 'test_scrape_pipeline.py')


@unittest.skipUnless(os.getenv('SCHOOL_SCRAPING_DATABASE_ONLY_TEST_DSN'),
                     'Dedicated disposable database-only DSN not supplied')
class DatabaseOnlySourcePostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dsn = os.environ['SCHOOL_SCRAPING_DATABASE_ONLY_TEST_DSN']
        settings = conninfo_to_dict(cls.dsn)
        if (settings.get('host') != '127.0.0.1' or settings.get('port') != '55449'
                or not settings.get('dbname', '').startswith('database_only_test_')):
            raise unittest.SkipTest('Not an explicitly named disposable database-only test DB')
        with psycopg.connect(cls.dsn, autocommit=True) as connection:
            identity = connection.execute("SELECT current_database(),host(inet_server_addr()),"
                "inet_server_port(),current_setting('data_directory'),current_setting('server_version')").fetchone()
            expected = ROOT / '.refactor_validation/section10a_backup_restore_test/restore_cluster/pgdata'
            if (identity[0] != settings['dbname'] or identity[1] != '127.0.0.1' or identity[2] != 55449
                    or Path(identity[3]).resolve() != expected or not identity[4].startswith('17.')):
                raise unittest.SkipTest('Disposable cluster identity is not the approved test environment')

    def setUp(self):
        self.observer = psycopg.connect(self.dsn, autocommit=True)
        self.addCleanup(self.observer.close)
        self.stores = []
        self.addCleanup(lambda: [store.close() for store in self.stores])
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def store(self, source='saras', academic_year=''):
        store = DatabaseOnlyStore(source, academic_year,
            connect=lambda: psycopg.connect(self.dsn, autocommit=True))
        self.stores.append(store)
        return store

    def identifier(self, source='saras'):
        width = 7 if source == 'saras' else 11
        return str(uuid.uuid4().int % (9 * 10 ** (width - 1)) + 10 ** (width - 1))

    def source_args(self, source, *extra):
        parser = saras.build_parser() if source == 'saras' else udise.parser()
        args = parser.parse_args(['--state', 'Punjab', '--database', '--storage-mode', 'database',
            '--new-only', '--delay', '0', '--data-dir', str(self.directory / 'data'),
            '--state-dir', str(self.directory / 'state'), '--report-dir', str(self.directory / 'reports'), *extra])
        # Prevent unrelated synthetic failures from another test scope being resumed.
        args.scope_key = 'database-only-e2e-' + uuid.uuid4().hex
        return args

    def assert_no_school_files(self):
        for name in ('data', 'state'):
            directory = self.directory / name
            self.assertEqual(list(directory.rglob('*.json')), [])
            self.assertEqual(list(directory.rglob('*.jsonl')), [])

    def canonical_and_raw(self, source, identifier):
        column = 'cbse_affiliation_number' if source == 'saras' else 'udise_code'
        row = self.observer.execute('SELECT a.payload FROM school_scraping.school_source_records s '
            'JOIN school_scraping.json_file_archive a ON a.file_id=s.archive_file_id '
            f'WHERE s.source_system=%s AND s.{column}=%s', (source, identifier)).fetchone()
        canonical = row[0]
        path = ('data/CBSE_SARAS/punjab/schools/' + identifier + '.json' if source == 'saras'
                else 'data/Punjab/2025-26/' + identifier + '.json')
        raw = self.observer.execute('SELECT payload FROM school_scraping.json_file_archive '
            'WHERE relative_path=%s', (path,)).fetchone()[0]
        return canonical, raw

    def saras_browser(self, identifiers):
        return source_fixtures.ExhaustiveBrowser({'PUNJAB': [source_fixtures.listing(value)
                                                           for value in identifiers]})

    def run_saras(self, identifiers, *extra, lookup_failure=False):
        browser = self.saras_browser(identifiers)
        args = self.source_args('saras', *extra)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('school_scraping.database_only.DatabaseOnlyStore', side_effect=self.store))
            stack.enter_context(patch.object(saras, 'RawStore', side_effect=AssertionError('No local school store')))
            if lookup_failure:
                stack.enter_context(patch.object(DatabaseOnlyStore, '_lookup', side_effect=OSError('Injected lookup failure')))
            report = saras.run(args, browser)
        self.assert_no_school_files()
        return report, browser

    def test_saras_skips_committed_before_details_and_commits_new_school(self):
        existing, fresh = self.identifier(), self.identifier()
        self.store().import_record(existing, raw_fixtures.saras_raw(existing, 'Previously committed'), 'Punjab')
        report, browser = self.run_saras([existing, fresh])
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(browser.detail_calls, [fresh])
        counts = report['counts']
        self.assertEqual(counts['existing_records_skipped'], 1)
        self.assertEqual(counts['new_details_fetched'], 1)
        self.assertEqual(counts['new_imports_committed'], 1)
        self.assertEqual(counts['import_acknowledgements'], 1)
        canonical, raw = self.canonical_and_raw('saras', fresh)
        self.assertEqual(raw['school_details']['Pin Code'], '134001')
        self.assertEqual(canonical['identity_location']['pincode'], raw['school_details']['Pin Code'])
        self.assertEqual(canonical['metadata']['schema_version'], '4.0')
        self.assertFalse(report['coverage']['details_complete'])

    def test_saras_limit_counts_existing_candidate_without_fetching_next(self):
        existing, next_identifier = self.identifier(), self.identifier()
        self.store().import_record(existing, raw_fixtures.saras_raw(existing, 'Previously committed'), 'Punjab')
        report, browser = self.run_saras([existing, next_identifier], '--limit', '1')
        self.assertEqual(browser.detail_calls, [])
        self.assertEqual(report['counts']['existing_records_skipped'], 1)
        self.assertEqual(report['counts']['new_imports_committed'], 0)
        self.assertEqual(report['counts']['listing_candidates_examined'], 1)

    def test_saras_lookup_failure_stops_before_any_details(self):
        identifiers = [self.identifier(), self.identifier()]
        report, browser = self.run_saras(identifiers, lookup_failure=True)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(browser.detail_calls, [])
        self.assertEqual(report['counts']['new_imports_committed'], 0)
        self.assertEqual(report['counts']['import_acknowledgements'], 0)
        self.assertIn('database_failure', report)

    def run_udise(self, identifiers, year='2025-26', *extra, lookup_failure=False):
        raw_records = {identifier: raw_fixtures.udise_raw(identifier, year) for identifier in identifiers}
        rows = [copy.deepcopy(raw_records[identifier]['school_summary']) for identifier in identifiers]
        client = Mock()
        client.verify_year.return_value = 12
        client.catalog.return_value = [{'stateName': 'Punjab', 'stateId': 3}]
        detail_calls = []
        sections = {endpoint: section for section, (label, endpoint) in udise.DETAILS.items()}

        def get_json(endpoint, parameters):
            identifier = parameters['udiseSchCode']
            detail_calls.append((identifier, endpoint))
            return {'success': True, 'response': {'status': True,
                'data': copy.deepcopy(raw_records[identifier][sections[endpoint]])}}

        client.get_json.side_effect = get_json
        args = self.source_args('udise', '--academic-year', year, *extra)
        args.school_list = [self.directory / 'synthetic-in-memory-list.json']
        report = {}

        def capture_report(directory, source, value):
            report.update(copy.deepcopy(value))
            return self.directory / 'report-not-written.json'

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch('school_scraping.database_only.DatabaseOnlyStore', side_effect=self.store))
            stack.enter_context(patch.object(udise, 'RawStore', side_effect=AssertionError('No local school store')))
            stack.enter_context(patch.object(udise, 'UdiseClient', return_value=client))
            stack.enter_context(patch.object(udise, 'load_lists', return_value=rows))
            stack.enter_context(patch.object(udise, 'write_report', side_effect=capture_report))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(patch.dict(os.environ, {'UDISE_HEADERS_JSON': '{}'}))
            if lookup_failure:
                stack.enter_context(patch.object(DatabaseOnlyStore, '_lookup', side_effect=OSError('Injected lookup failure')))
            code = udise.run_job(args)
        self.assert_no_school_files()
        return code, report, detail_calls

    def test_udise_skips_existing_year_and_imports_missing_year_and_fresh_code(self):
        existing, missing_year, fresh = [self.identifier('udise') for _ in range(3)]
        self.store('udise', '2025-26').import_record(existing, raw_fixtures.udise_raw(existing), 'Punjab')
        self.store('udise', '2024-25').import_record(missing_year,
            raw_fixtures.udise_raw(missing_year, '2024-25'), 'Punjab')
        code, report, details = self.run_udise([existing, missing_year, fresh])
        self.assertEqual(code, 0, report)
        self.assertEqual({identifier for identifier, endpoint in details}, {missing_year, fresh})
        self.assertEqual(len(details), 8)
        self.assertEqual(report['counts']['existing_records_skipped'], 1)
        self.assertEqual(report['counts']['new_imports_committed'], 2)
        self.assertEqual(report['counts']['import_acknowledgements'], 2)
        canonical, raw = self.canonical_and_raw('udise', fresh)
        self.assertEqual(raw['school_summary']['pincode'], 141001)
        self.assertEqual(canonical['identity_location']['pincode'], raw['school_summary']['pincode'])
        self.assertEqual(self.store('udise', '2025-26').lookup_committed([existing, missing_year, fresh]),
                         {existing, missing_year, fresh})

    def test_udise_limit_and_lookup_failure_prevent_later_details(self):
        existing, fresh = self.identifier('udise'), self.identifier('udise')
        self.store('udise', '2025-26').import_record(existing, raw_fixtures.udise_raw(existing), 'Punjab')
        code, report, details = self.run_udise([existing, fresh], '2025-26', '--limit', '1')
        self.assertEqual(code, 0)
        self.assertEqual(details, [])
        self.assertEqual(report['counts']['listing_candidates_examined'], 1)
        self.assertEqual(report['counts']['new_imports_committed'], 0)
        code, report, details = self.run_udise([self.identifier('udise'), self.identifier('udise')],
                                              lookup_failure=True)
        self.assertEqual(code, 1)
        self.assertEqual(details, [])
        self.assertEqual(report['counts']['import_acknowledgements'], 0)
        self.assertIn('database_failure', report)
