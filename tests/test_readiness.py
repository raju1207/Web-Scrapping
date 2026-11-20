"""Pure evidence checks: no database, service activation, keys or uploads."""
import copy
import hashlib
import json
import os
from pathlib import Path
from collections import namedtuple
from datetime import datetime, timedelta, timezone
import tempfile
import unittest
from unittest.mock import patch

from school_scraping.readiness import check_readiness


Capacity = namedtuple('Capacity', 'total used free')


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 11, 20, 6, tzinfo=timezone.utc)
        stamp = (self.now - timedelta(seconds=10)).isoformat()
        self.backup = self.root / 'backup.dump'
        self.backup.write_bytes(b'synthetic backup; not an encrypted deployment artifact')
        self.evidence = {'version': 1, 'observed_at': stamp, 'heartbeat_at': stamp,
            'last_success_at': stamp, 'database': {'checked_at': stamp, 'status': 'connected'},
            'failed_imports': 0, 'missing_acknowledgements': 0, 'alert_delivery_failures': 0,
            'alert_delivery_tested': True, 'alert_delivery_tested_at': stamp,
            'backup': {'path': 'backup.dump', 'sha256': hashlib.sha256(self.backup.read_bytes()).hexdigest(),
                       'size_bytes': self.backup.stat().st_size, 'completed_at': stamp,
                       'encrypted': True, 'off_machine_verified': True,
                       'encryption_verified_at': stamp, 'off_machine_verified_at': stamp}}
        self.settings = {'enabled': True, 'approval_reference': 'synthetic operator-approved fixture',
            'evidence_root': str(self.root), 'evidence_path': 'readiness.json',
            'max_evidence_age_seconds': 60, 'max_heartbeat_age_seconds': 60,
            'max_success_age_seconds': 300, 'max_database_age_seconds': 60,
            'max_backup_age_seconds': 600, 'max_recovery_test_age_seconds': 3600,
            'max_alert_test_age_seconds': 600, 'min_disk_free_bytes': 100,
            'min_disk_free_ratio': 0.1, 'max_failed_imports': 0,
            'max_missing_acknowledgements': 0, 'max_alert_delivery_failures': 0}
        self.publish()

    def publish(self):
        content = json.dumps(self.evidence).encode()
        (self.root / 'readiness.json').write_bytes(content)
        self.settings['evidence_sha256'] = hashlib.sha256(content).hexdigest()

    def check(self, **kwargs):
        return check_readiness(self.settings, now=self.now,
                               disk_usage=lambda _: Capacity(1000, 500, 500), **kwargs)

    def test_valid_pinned_evidence_passes_as_a_prototype_without_mutation(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        result = self.check()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['mode'], 'prototype')
        self.assertIs(result['deployed_monitoring'], False)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_missing_inactive_and_unapproved_profiles_block(self):
        for settings in (None, {}, {'enabled': False}, {'enabled': True}):
            with self.subTest(settings=settings):
                self.assertEqual(check_readiness(settings)['status'], 'BLOCKED')

    def test_explicit_positive_thresholds_and_security_policies_cannot_be_disabled(self):
        changes = [('min_disk_free_bytes', 0), ('max_backup_age_seconds', -1),
                   ('max_failed_imports', True), ('min_disk_free_ratio', float('nan')),
                   ('require_encrypted_backup', False), ('require_off_machine_verified', False),
                   ('require_alert_delivery_tested', False)]
        for key, value in changes:
            with self.subTest(key=key):
                settings = {**self.settings, key: value}
                self.assertIn('explicit_thresholds', check_readiness(settings, now=self.now)['errors'])

    def test_boolean_success_flags_without_files_do_not_pass(self):
        self.assertEqual(check_readiness({'enabled': True, 'approved': True,
            'backup_ready': True, 'monitoring_ready': True})['status'], 'BLOCKED')

    def test_missing_or_changed_pinned_digest_blocks(self):
        for digest in ('', '0' * 64, None):
            with self.subTest(digest=digest):
                self.settings['evidence_sha256'] = digest
                self.assertIn('pinned_evidence', self.check()['errors'])

    def test_evidence_must_be_a_confined_relative_regular_file(self):
        for path in ('../readiness.json', str(self.root / 'readiness.json'),
                     'nested/../../readiness.json', 'nested\\readiness.json', 'C:readiness.json'):
            with self.subTest(path=path):
                self.settings['evidence_path'] = path
                self.assertIn('pinned_evidence', self.check()['errors'])

    def test_symlink_evidence_is_rejected_before_reading(self):
        actual = Path.is_symlink
        with patch.object(Path, 'is_symlink', lambda p: p.name == 'readiness.json' or actual(p)):
            self.assertIn('pinned_evidence', self.check()['errors'])

    def test_stale_future_and_naive_timestamps_block(self):
        for field, value in (
            ('heartbeat_at', self.now - timedelta(seconds=61)),
            ('last_success_at', self.now - timedelta(seconds=301)),
            ('observed_at', self.now + timedelta(seconds=1)),
            ('heartbeat_at', self.now.replace(tzinfo=None))):
            with self.subTest(field=field, value=value):
                original = copy.deepcopy(self.evidence)
                self.evidence[field] = value.isoformat()
                self.publish()
                self.assertEqual(self.check()['status'], 'BLOCKED')
                self.evidence = original

    def test_naive_clock_is_rejected(self):
        self.assertIn('clock_invalid', check_readiness(self.settings,
            now=self.now.replace(tzinfo=None))['errors'])

    def test_database_connectivity_requires_recent_successful_evidence(self):
        self.evidence['database']['status'] = 'unavailable'
        self.evidence['database']['checked_at'] = (self.now - timedelta(seconds=61)).isoformat()
        self.publish()
        self.assertIn('database_connectivity', self.check()['errors'])
        self.assertIn('database_evidence_fresh', self.check()['errors'])

    def test_failed_import_missing_ack_and_alert_failures_block(self):
        for key in ('failed_imports', 'missing_acknowledgements', 'alert_delivery_failures'):
            with self.subTest(key=key):
                self.evidence[key] = 1
                self.publish()
                self.assertIn(key, self.check()['errors'])
                self.evidence[key] = 0

    def test_invalid_counters_cannot_claim_health(self):
        for count in (True, -1, '0', None):
            with self.subTest(count=count):
                self.evidence['missing_acknowledgements'] = count
                self.publish()
                self.assertIn('missing_acknowledgements', self.check()['errors'])

    def test_disk_checks_use_real_capacity_not_an_evidence_boolean(self):
        self.evidence['disk_ready'] = True
        self.publish()
        result = check_readiness(self.settings, now=self.now,
                                disk_usage=lambda _: Capacity(1000, 990, 10))
        self.assertIn('disk_capacity', result['errors'])
        with patch('shutil.disk_usage', side_effect=OSError('secret-location')):
            result = check_readiness(self.settings, now=self.now,
                                    disk_usage=lambda _: (_ for _ in ()).throw(OSError('secret')))
        self.assertNotIn('secret', json.dumps(result))

    def test_backup_actual_bytes_size_and_freshness_are_verified(self):
        self.backup.write_bytes(b'changed')
        self.evidence['backup']['completed_at'] = (self.now - timedelta(seconds=601)).isoformat()
        self.publish()
        errors = self.check()['errors']
        self.assertIn('backup_artifact', errors)
        self.assertIn('backup_fresh', errors)

    def test_backup_path_must_be_confined_and_exist(self):
        for path in ('../backup.dump', 'missing.dump'):
            with self.subTest(path=path):
                self.evidence['backup']['path'] = path
                self.publish()
                self.assertIn('backup_artifact', self.check()['errors'])

    def test_missing_security_and_alert_attestations_block(self):
        del self.evidence['backup']['encrypted']
        del self.evidence['backup']['off_machine_verified']
        del self.evidence['alert_delivery_tested']
        self.publish()
        result = self.check()
        for error in ('encrypted_backup_attestation', 'off_machine_recovery_attestation',
                      'alert_delivery_test_attestation'):
            self.assertIn(error, result['errors'])

    def test_fresh_metadata_cannot_renew_stale_security_test_attestations(self):
        self.evidence['backup']['encryption_verified_at'] = (self.now - timedelta(seconds=601)).isoformat()
        self.evidence['backup']['off_machine_verified_at'] = (self.now - timedelta(seconds=3601)).isoformat()
        self.evidence['alert_delivery_tested_at'] = (self.now - timedelta(seconds=601)).isoformat()
        self.publish()
        for error in ('encryption_attestation_fresh', 'off_machine_attestation_fresh', 'alert_test_attestation_fresh'):
            self.assertIn(error, self.check()['errors'])

    def test_malformed_and_oversized_evidence_cannot_expose_contents(self):
        for content in (b'private-token malformed json', b'x' * (1024 * 1024 + 1)):
            with self.subTest(length=len(content)):
                (self.root / 'readiness.json').write_bytes(content)
                self.settings['evidence_sha256'] = hashlib.sha256(content).hexdigest()
                result = self.check()
                self.assertIn('pinned_evidence', result['errors'])
                self.assertNotIn('private-token', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
