"""Windows preparation checks use synthetic facts; no system or DB changes."""
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from school_scraping import windows_readiness as windows


ROOT = Path(__file__).resolve().parents[1]


def facts():
    return {'platform': 'win32',
        'paths': {'project_normal': True, 'venv_python': True, 'scheduler': True,
                  'requirements': True, 'log_directory': True},
        'python': {'version': '3.14.6', 'bits': 64, 'venv_matches': True,
                   'dependencies_ok': True, 'browser_executable': True},
        'os': {'caption': 'Synthetic Windows', 'version': '10.0', 'build': '26100',
               'architecture': '64-bit'},
        'timezone': 'India Standard Time',
        'disk': {'total_bytes': 10000, 'free_bytes': 5000},
        'services': {'status': 'OK', 'postgresql': [{'name': 'synthetic-postgresql-17',
            'status': 'Running', 'start_type': 'Automatic'}], 'scheduler_running': True},
        'firewall': {'status': 'OK', 'profiles': [{'name': name, 'enabled': True}
                                               for name in ('Domain', 'Private', 'Public')]},
        'power': {'status': 'OK', 'ac_sleep_seconds': 0, 'dc_sleep_seconds': 0,
                  'ac_wake': True, 'dc_wake': True,
                  'ac_hibernate_seconds': 0, 'dc_hibernate_seconds': 0},
        'tasks': {'status': 'OK', 'project_tasks': []},
        'acl': {'status': 'OK', 'env_exists': False, 'broad_secret_access': False,
                'broad_project_modify': False},
        'postgresql': {'tools_ok': True, 'major': 17}, 'errors': []}


def plan():
    return {'schema_version': 1, 'approved': True,
        'approval_reference': 'SYNTHETIC WINDOWS PREPARATION TEST ONLY',
        'runtime_identity': 'SYNTHETIC-PC\\synthetic-runtime',
        'database_service': 'synthetic-postgresql-17', 'candidate_limit': 1,
        'approvals': {name: True for name in (
            'power', 'firewall', 'ntfs', 'task_design', 'source_access',
            'backup_retention', 'recovery_objectives', 'key_custody',
            'off_machine_destination', 'log_rotation', 'alert_delivery')}}


def profile():
    return {'schema_version': 1, 'approved': True,
        'approval_reference': 'SYNTHETIC LOCAL PROFILE ONLY',
        'scheduler': {'approved': True, 'hard_timeout_seconds': 180},
        'allowed_jobs': [{'source': source, 'states': ['CHANDIGARH'], 'districts': [],
                          'pincodes': [], 'policy': 'new-only', 'academic_year': '2025-26'}
                         for source in ('saras', 'udise')],
        'protected_targets': [{'host': '*', 'port': port} for port in (5432, 55439, 55449)],
        'database': {'host': '127.0.0.1', 'port': 55459, 'name': 'schools_local_synthetic',
            'user': 'synthetic_scraper_writer', 'major_version': 17, 'database_oid': 123456,
            'schema_version': 1, 'canonical_schema_version': '4.0',
            'timeouts': {'connect_seconds': 5, 'statement_ms': 5000, 'lock_ms': 1000},
            'tls': {'mode': 'approved-local', 'approved_local_exception': True,
                    'reason': 'Explicit synthetic loopback fixture; no real deployment'}},
        'readiness': {'enabled': True, 'min_disk_free_bytes': 1000, 'min_disk_free_ratio': 0.1}}


def configuration():
    return {'scheduler': {'enabled': False, 'time': '23:00', 'timezone': 'Asia/Kolkata'},
            'database': {'enabled': False}}


class WindowsReadinessTests(unittest.TestCase):
    def setUp(self):
        self.facts, self.plan, self.profile, self.config = facts(), plan(), profile(), configuration()
        self.monitoring = Mock(return_value={'status': 'PASS', 'mode': 'synthetic',
                                            'deployed_monitoring': True})
        self.no_database = patch('psycopg.connect', side_effect=AssertionError('Readiness must not connect'))
        self.no_database.start()
        self.addCleanup(self.no_database.stop)

    def check(self, **changes):
        options = {'project_root': ROOT, 'facts': self.facts, 'plan': self.plan,
                   'profile': self.profile, 'config': self.config,
                   'readiness_checker': self.monitoring}
        options.update(changes)
        return windows.check_windows_readiness(**options)

    def test_complete_synthetic_preparation_never_authorizes_activation_or_claims_database_identity(self):
        before = copy.deepcopy((self.facts, self.plan, self.profile, self.config))
        result = self.check()
        self.assertEqual(result['preparation_status'], 'PASS', result)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIs(result['activation_allowed'], False)
        self.assertEqual(result['database_connections'], 0)
        self.assertIs(result['read_only'], True)
        self.assertIn('LIVE_DATABASE_PREFLIGHT_REQUIRED', result['blockers'])
        self.assertEqual(before, (self.facts, self.plan, self.profile, self.config))

    def test_supplied_facts_make_the_checker_pure(self):
        with patch.object(Path, 'open', side_effect=AssertionError('No file reads')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('No file reads')), \
             patch('subprocess.run', side_effect=AssertionError('No system queries')):
            self.assertEqual(self.check()['preparation_status'], 'PASS')

    def test_missing_or_inactive_configuration_blocks(self):
        for values in ({'plan': None}, {'profile': None}, {'config': None},
                       {'plan': {}}, {'profile': {}}, {'config': {}}):
            with self.subTest(values=values):
                self.assertEqual(self.check(**values)['preparation_status'], 'BLOCKED')

    def test_every_required_manual_approval_is_explicit(self):
        for approval in self.plan['approvals']:
            with self.subTest(approval=approval):
                altered = copy.deepcopy(self.plan)
                altered['approvals'][approval] = False
                self.assertEqual(self.check(plan=altered)['preparation_status'], 'BLOCKED')

    def test_plan_identity_approval_and_candidate_budget_are_required(self):
        for key, value in (('approved', False), ('approval_reference', ''),
                           ('runtime_identity', None), ('database_service', ''),
                           ('candidate_limit', None), ('candidate_limit', True),
                           ('schema_version', True)):
            with self.subTest(key=key, value=value):
                altered = {**self.plan, key: value}
                self.assertEqual(self.check(plan=altered)['preparation_status'], 'BLOCKED')

    def test_non_windows_and_unknown_operating_system_block(self):
        for changes in ({'platform': 'linux'}, {'os': {}}, {'platform': None}):
            with self.subTest(changes=changes):
                self.assertEqual(self.check(facts={**self.facts, **changes})['preparation_status'], 'BLOCKED')

    def test_operating_system_identity_must_not_be_partially_unknown(self):
        for key in ('caption', 'version', 'build', 'architecture'):
            with self.subTest(key=key):
                altered = copy.deepcopy(self.facts)
                del altered['os'][key]
                self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')

    def test_project_venv_runtime_and_browser_prerequisites_are_required(self):
        for group, keys in (('paths', tuple(self.facts['paths'])),
                            ('python', ('venv_matches', 'dependencies_ok', 'browser_executable')),
                            ('postgresql', ('tools_ok',))):
            for key in keys:
                with self.subTest(group=group, key=key):
                    altered = copy.deepcopy(self.facts)
                    altered[group][key] = False
                    self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')

    def test_wrong_postgresql_major_and_unknown_runtime_versions_block(self):
        for group, key, value in (('postgresql', 'major', 16), ('python', 'version', ''),
                                  ('python', 'bits', 32)):
            with self.subTest(group=group, key=key):
                altered = copy.deepcopy(self.facts)
                altered[group][key] = value
                self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')

    def test_unknown_security_service_power_and_task_probes_block(self):
        for group in ('services', 'firewall', 'power', 'tasks', 'acl'):
            with self.subTest(group=group):
                altered = copy.deepcopy(self.facts)
                altered[group] = {'status': 'UNKNOWN'}
                self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')

    def test_selected_postgresql_and_scheduler_services_must_be_running(self):
        altered = copy.deepcopy(self.facts)
        altered['services']['postgresql'][0]['status'] = 'Stopped'
        self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')
        altered = copy.deepcopy(self.facts)
        altered['services']['scheduler_running'] = False
        self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')
        self.assertEqual(self.check(plan={**self.plan, 'database_service': 'unapproved-other-service'})
                         ['preparation_status'], 'BLOCKED')

    def test_disabled_firewall_sleep_and_broad_acl_access_block(self):
        changes = [('firewall', lambda value: value['profiles'][0].update(enabled=False)),
                   ('power', lambda value: value.update(ac_sleep_seconds=900)),
                   ('acl', lambda value: value.update(broad_secret_access=True)),
                   ('acl', lambda value: value.update(broad_project_modify=True))]
        for group, mutate in changes:
            with self.subTest(group=group):
                altered = copy.deepcopy(self.facts)
                mutate(altered[group])
                self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')

    def test_unknown_hibernation_and_duplicate_firewall_profiles_block(self):
        altered = copy.deepcopy(self.facts)
        del altered['power']['ac_hibernate_seconds']
        self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')
        altered = copy.deepcopy(self.facts)
        altered['firewall']['profiles'] = [{'name': 'Domain', 'enabled': True}] * 3
        self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')
        altered = copy.deepcopy(self.facts)
        altered['firewall']['profiles'][0]['name'] = {'unexpected': 'structured value'}
        self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')

    def test_existing_project_tasks_require_review(self):
        altered = copy.deepcopy(self.facts)
        altered['tasks']['project_tasks'] = [{'state': 'Ready', 'triggers': []}]
        self.assertEqual(self.check(facts=altered)['preparation_status'], 'BLOCKED')

    def test_timezone_and_confirmed_2300_schedule_are_required(self):
        self.assertEqual(self.check(facts={**self.facts, 'timezone': 'UTC'})['preparation_status'], 'BLOCKED')
        for key, value in (('time', '02:00'), ('timezone', 'UTC'), ('enabled', True)):
            with self.subTest(key=key):
                altered = copy.deepcopy(self.config)
                altered['scheduler'][key] = value
                self.assertEqual(self.check(config=altered)['preparation_status'], 'BLOCKED')
        altered = copy.deepcopy(self.config)
        altered['database']['enabled'] = True
        self.assertEqual(self.check(config=altered)['preparation_status'], 'BLOCKED')

    def test_insufficient_or_unknown_disk_capacity_blocks(self):
        for disk in ({}, {'total_bytes': 10000, 'free_bytes': 999},
                     {'total_bytes': 100000, 'free_bytes': 1000},
                     {'total_bytes': 0, 'free_bytes': 5000}):
            with self.subTest(disk=disk):
                self.assertEqual(self.check(facts={**self.facts, 'disk': disk})['preparation_status'], 'BLOCKED')

    def test_database_target_and_static_schema_contract_are_fail_closed(self):
        changes = [('host', 'example.invalid'), ('port', 5432), ('port', 55439), ('port', 55449),
                   ('name', 'postgres'), ('name', 'database_only_test_fixture'),
                   ('user', 'postgres'), ('user', 'memory_test_admin'), ('database_oid', None),
                   ('major_version', 16), ('schema_version', 2), ('canonical_schema_version', '3.0')]
        for key, value in changes:
            with self.subTest(key=key, value=value):
                altered = copy.deepcopy(self.profile)
                altered['database'][key] = value
                self.assertEqual(self.check(profile=altered)['preparation_status'], 'BLOCKED')

    def test_tls_and_timeout_policies_require_explicit_approval(self):
        mutations = [lambda p: p['database']['tls'].update(mode='prefer'),
                     lambda p: p['database']['tls'].update(approved_local_exception=False),
                     lambda p: p['database']['tls'].update(reason=''),
                     lambda p: p['database']['timeouts'].update(connect_seconds=0),
                     lambda p: p['database']['timeouts'].update(lock_ms=6000),
                     lambda p: p['scheduler'].update(approved=False),
                     lambda p: p['scheduler'].update(hard_timeout_seconds=None)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                altered = copy.deepcopy(self.profile)
                mutate(altered)
                self.assertEqual(self.check(profile=altered)['preparation_status'], 'BLOCKED')

    def test_verify_full_requires_an_existing_normal_ca_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            altered = copy.deepcopy(self.profile)
            altered['database']['tls'] = {'mode': 'verify-full',
                'root_certificate': str(Path(temporary) / 'missing-ca.pem')}
            self.assertEqual(self.check(profile=altered)['preparation_status'], 'BLOCKED')

    def test_source_scope_and_udise_year_must_be_approved(self):
        mutations = [lambda p: p.update(approved=False), lambda p: p.update(approval_reference=''),
                     lambda p: p.update(allowed_jobs=[]),
                     lambda p: p['allowed_jobs'][0].update(states=[]),
                     lambda p: p['allowed_jobs'][0].update(source='unknown'),
                     lambda p: p['allowed_jobs'][1].update(academic_year=None),
                     lambda p: p['allowed_jobs'][0].update(policy='unlimited')]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                altered = copy.deepcopy(self.profile)
                mutate(altered)
                self.assertEqual(self.check(profile=altered)['preparation_status'], 'BLOCKED')

    def test_backup_and_deployed_monitoring_are_independent_requirements(self):
        for returned in ({'status': 'BLOCKED', 'errors': ['backup_missing']},
                         {'status': 'PASS', 'mode': 'prototype', 'deployed_monitoring': False}, None):
            with self.subTest(returned=returned):
                checker = Mock(return_value=returned)
                self.assertEqual(self.check(readiness_checker=checker)['preparation_status'], 'BLOCKED')
        checker = Mock(side_effect=RuntimeError('password=private-fixture-marker'))
        result = self.check(readiness_checker=checker)
        self.assertEqual(result['preparation_status'], 'BLOCKED')
        self.assertNotIn('private-fixture-marker', json.dumps(result))

    def test_unexpected_fact_and_profile_secrets_are_not_serialized(self):
        self.facts['private_headers'] = {'Authorization': 'private-fact-marker'}
        self.profile['password'] = 'private-profile-marker'
        result = json.dumps(self.check())
        self.assertNotIn('private-fact-marker', result)
        self.assertNotIn('private-profile-marker', result)

    def test_unknown_nested_fact_fields_are_not_serialized(self):
        for key in ('os', 'disk', 'power', 'postgresql'):
            self.facts[key]['private_headers'] = {'Authorization': 'nested-private-marker'}
        self.assertNotIn('nested-private-marker', json.dumps(self.check()))

    def test_malformed_summary_values_do_not_serialize_nested_payloads(self):
        for group, key in (('python', 'version'), ('firewall', 'status')):
            with self.subTest(group=group, key=key):
                altered = copy.deepcopy(self.facts)
                altered[group][key] = {'Authorization': 'malformed-private-marker'}
                self.assertNotIn('malformed-private-marker', json.dumps(self.check(facts=altered)))

    def test_import_has_no_database_or_probe_side_effects(self):
        spec = importlib.util.spec_from_file_location('windows_readiness_import_test',
                                                    ROOT / 'school_scraping/windows_readiness.py')
        module = importlib.util.module_from_spec(spec)
        with patch('subprocess.run', side_effect=AssertionError('Import must not probe')), \
             patch.object(Path, 'read_text', side_effect=AssertionError('Import must not read settings')), \
             patch.object(Path, 'open', side_effect=AssertionError('Import must not read private files')):
            spec.loader.exec_module(module)

    def test_collection_probe_errors_are_safe_and_block(self):
        runner = Mock(side_effect=OSError('password=private-probe-marker'))
        observed = windows.collect_windows_facts(project_root=ROOT, runner=runner)
        self.assertIsInstance(observed, dict)
        self.assertNotIn('private-probe-marker', json.dumps(observed))
        self.assertEqual(self.check(facts=observed)['preparation_status'], 'BLOCKED')

    def test_collector_uses_fixed_readonly_commands_and_only_client_version_flags(self):
        probe = {key: copy.deepcopy(self.facts[key]) for key in
                 ('os', 'timezone', 'services', 'power', 'firewall', 'tasks', 'acl', 'errors')}
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[1:] == ['--version']:
                return SimpleNamespace(stdout='synthetic (PostgreSQL) 17.5')
            return SimpleNamespace(stdout=json.dumps(probe))
        with patch.object(windows, '_dependencies', return_value=[{'name': 'synthetic',
                'version': '1.0', 'matches_requirement': True}]), \
             patch.object(windows.shutil, 'disk_usage', return_value=SimpleNamespace(total=10000, free=5000)), \
             patch.object(windows.sys, 'platform', 'win32'):
            observed = windows.collect_windows_facts(project_root=ROOT, runner=runner)
        self.assertEqual(len(calls), 5)
        self.assertTrue(observed['postgresql']['tools_ok'])
        powershell, options = calls[0]
        self.assertIn('-NoProfile', powershell)
        self.assertIn('-NonInteractive', powershell)
        self.assertIn('WINDOWS_AUDIT_PROJECT', options['env'])
        self.assertEqual(options['timeout'], 45)
        self.assertNotIn(str(ROOT), powershell[-1])
        for argv, options in calls[1:]:
            self.assertEqual(argv[1:], ['--version'])
            self.assertEqual(options['timeout'], 5)
            self.assertNotIn('-h', argv)
            self.assertNotIn('-p', argv)

    def test_exact_dependency_pins_accept_matching_postrelease_versions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'requirements.txt').write_text('python-dateutil==2.9.0.post0\n', encoding='utf-8')
            with patch.object(windows.importlib.metadata, 'version', return_value='2.9.0.post0'):
                rows = windows._dependencies(root)
            self.assertEqual(rows, [{'name': 'python-dateutil', 'version': '2.9.0.post0',
                                     'matches_requirement': True}])
            with patch.object(windows.importlib.metadata, 'version', return_value='2.9.0'):
                self.assertFalse(windows._dependencies(root)[0]['matches_requirement'])


class WindowsReadinessCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.paths = {}
        for name, value in (('profile', profile()), ('plan', plan()), ('config', configuration())):
            path = self.root / (name + '.json')
            path.write_text(json.dumps(value), encoding='utf-8')
            self.paths[name] = path
        self.args = ['--project-root', str(ROOT)]
        for name, path in self.paths.items():
            self.args.extend(['--' + name, str(path)])

    def invoke(self, *extra):
        output = io.StringIO()
        with patch('psycopg.connect', side_effect=AssertionError('CLI must not connect')), \
             patch('sys.stdout', output):
            code = windows.main(self.args + list(extra))
        return code, json.loads(output.getvalue())

    def test_missing_profile_returns_nonzero_safe_json_without_probing(self):
        with patch.object(windows, 'collect_windows_facts') as collector:
            code, result = self.invoke('--profile', str(self.root / 'missing-profile.json'))
        self.assertNotEqual(code, 0)
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertIn('CONFIGURATION_UNREADABLE', result['blockers'])
        collector.assert_not_called()

    def test_malformed_configuration_is_not_printed(self):
        self.paths['profile'].write_text('private-configuration-marker broken json', encoding='utf-8')
        code, result = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertNotIn('private-configuration-marker', json.dumps(result))

    def test_internal_audit_failure_has_nonzero_generic_error(self):
        with patch.object(windows, 'collect_windows_facts', side_effect=RuntimeError('private-failure-marker')):
            code, result = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertIn('READINESS_AUDIT_FAILED', result['blockers'])
        self.assertNotIn('private-failure-marker', json.dumps(result))

    def test_completed_preparation_cli_still_exits_blocked_and_never_activates(self):
        reported = {'status': 'BLOCKED', 'preparation_status': 'PASS', 'activation_allowed': False,
                    'blockers': ['LIVE_DATABASE_PREFLIGHT_REQUIRED'], 'database_connections': 0,
                    'checks': {}, 'environment': {}, 'read_only': True}
        with patch.object(windows, 'collect_windows_facts', return_value=facts()), \
             patch.object(windows, 'check_windows_readiness', return_value=reported):
            code, result = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(result, reported)


if __name__ == '__main__':
    unittest.main()
