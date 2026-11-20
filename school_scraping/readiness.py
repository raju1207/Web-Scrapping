"""Fail-closed local readiness evidence checks; this is not a deployed monitor.

Evidence hashes establish integrity against an operator-pinned digest. They do
not prove that its producer, alert channel or recovery service is deployed.
No database connections, uploads, keys, or filesystem writes occur here.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
from datetime import datetime, timezone


_MAX_EVIDENCE_BYTES = 1024 * 1024
_AGE_KEYS = ('max_evidence_age_seconds', 'max_heartbeat_age_seconds',
             'max_success_age_seconds', 'max_database_age_seconds', 'max_backup_age_seconds',
             'max_recovery_test_age_seconds', 'max_alert_test_age_seconds')
_COUNT_KEYS = ('failed_imports', 'missing_acknowledgements', 'alert_delivery_failures')


def _number(value, *, integer=False, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        if not math.isfinite(value):
            return False
    except OverflowError:
        return False
    return (not integer or isinstance(value, int)) and (value > 0 if positive else value >= 0)


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Missing timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('Timezone required')
    return parsed.astimezone(timezone.utc)


def _normal_path(path):
    """Check every existing ancestor before resolving any redirected path."""
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('Redirected evidence path')
        if item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
            raise ValueError('Redirected evidence path')
    return path


def _relative_file(root, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Invalid relative evidence path')
    relative = Path(name)
    if relative.is_absolute() or any(piece in ('..', '.') for piece in name.split('/')):
        raise ValueError('Evidence path escapes its root')
    path = _normal_path(root / relative)
    if not path.is_file() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError('Evidence must be a regular file')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Evidence path escapes its root')
    return path


def _digest(path):
    checksum = hashlib.sha256()
    before = path.stat()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('Evidence changed during verification')
    return checksum.hexdigest()


def check_readiness(settings, *, now=None, disk_usage=shutil.disk_usage):
    """Validate explicit thresholds and pinned operator-generated evidence.

    All timestamps require a timezone and must be no later than ``now``. Evidence
    and backup paths must be ordinary files beneath one explicit evidence root.
    Production approval and live database identity are separate preflight checks.
    """
    report = {'status': 'BLOCKED', 'mode': 'prototype', 'deployed_monitoring': False,
              'errors': [], 'checks': {}}
    def check(name, passed):
        report['checks'][name] = 'PASS' if passed else 'BLOCKED'
        if not passed:
            report['errors'].append(name)
        return passed
    if not isinstance(settings, dict) or settings.get('enabled') is not True:
        check('readiness_not_enabled', False)
        return report
    approval = settings.get('approval_reference')
    if not check('approval_reference', isinstance(approval, str) and bool(approval.strip())):
        return report
    thresholds = all(_number(settings.get(key), positive=True) for key in _AGE_KEYS)
    thresholds = thresholds and _number(settings.get('min_disk_free_bytes'), integer=True, positive=True)
    ratio = settings.get('min_disk_free_ratio')
    thresholds = thresholds and _number(ratio, positive=True) and ratio < 1
    thresholds = thresholds and all(_number(settings.get('max_' + key), integer=True) for key in _COUNT_KEYS)
    thresholds = thresholds and all(settings.get(key, True) is True for key in
        ('require_encrypted_backup', 'require_off_machine_verified', 'require_alert_delivery_tested'))
    if not check('explicit_thresholds', thresholds):
        return report
    current = now() if callable(now) else now
    current = current or datetime.now(timezone.utc)
    if not isinstance(current, datetime) or current.tzinfo is None or current.utcoffset() is None:
        check('clock_invalid', False)
        return report
    current = current.astimezone(timezone.utc)
    try:
        raw_root = settings.get('evidence_root')
        if not isinstance(raw_root, str) or not Path(raw_root).is_absolute():
            raise ValueError('Explicit absolute root required')
        root = _normal_path(Path(os.path.abspath(raw_root)))
        if not root.is_dir():
            raise ValueError('Evidence root missing')
        path = _relative_file(root, settings.get('evidence_path'))
        expected = settings.get('evidence_sha256')
        if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Pinned evidence checksum required')
        if path.stat().st_size > _MAX_EVIDENCE_BYTES:
            raise ValueError('Evidence too large')
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('Evidence checksum mismatch')
        evidence = json.loads(content)
        if not isinstance(evidence, dict) or type(evidence.get('version')) is not int or evidence['version'] != 1:
            raise ValueError('Unsupported evidence format')
        check('pinned_evidence', True)
    except (OSError, ValueError, TypeError):
        check('pinned_evidence', False)
        return report
    def fresh(name, value, age):
        try:
            elapsed = (current - _timestamp(value)).total_seconds()
            return check(name, 0 <= elapsed <= settings[age])
        except (ValueError, TypeError, OverflowError):
            return check(name, False)
    fresh('evidence_fresh', evidence.get('observed_at'), 'max_evidence_age_seconds')
    fresh('scheduler_heartbeat', evidence.get('heartbeat_at'), 'max_heartbeat_age_seconds')
    fresh('last_successful_execution', evidence.get('last_success_at'), 'max_success_age_seconds')
    database = evidence.get('database')
    database = database if isinstance(database, dict) else {}
    check('database_connectivity', database.get('status') == 'connected')
    fresh('database_evidence_fresh', database.get('checked_at'), 'max_database_age_seconds')
    for key in _COUNT_KEYS:
        count = evidence.get(key)
        check(key, _number(count, integer=True) and count <= settings['max_' + key])
    try:
        capacity = disk_usage(root)
        check('disk_capacity', _number(capacity.total, positive=True) and _number(capacity.free)
              and capacity.free <= capacity.total and capacity.free >= settings['min_disk_free_bytes']
              and capacity.free / capacity.total >= ratio)
    except (OSError, ValueError, TypeError, AttributeError):
        check('disk_capacity', False)
    backup = evidence.get('backup')
    backup = backup if isinstance(backup, dict) else {}
    fresh('backup_fresh', backup.get('completed_at'), 'max_backup_age_seconds')
    try:
        backup_path = _relative_file(root, backup.get('path'))
        checksum = backup.get('sha256')
        size = backup.get('size_bytes')
        if (not isinstance(checksum, str) or not re.fullmatch('[0-9a-f]{64}', checksum)
                or not _number(size, integer=True, positive=True) or size != backup_path.stat().st_size
                or _digest(backup_path) != checksum):
            raise ValueError('Backup verification failed')
        check('backup_artifact', True)
    except (OSError, ValueError, TypeError):
        check('backup_artifact', False)
    for policy, field, name in (
        ('require_encrypted_backup', 'encrypted', 'encrypted_backup_attestation'),
        ('require_off_machine_verified', 'off_machine_verified', 'off_machine_recovery_attestation')):
        check(name, not settings.get(policy, True) or backup.get(field) is True)
    fresh('encryption_attestation_fresh', backup.get('encryption_verified_at'), 'max_backup_age_seconds')
    fresh('off_machine_attestation_fresh', backup.get('off_machine_verified_at'), 'max_recovery_test_age_seconds')
    check('alert_delivery_test_attestation', not settings.get('require_alert_delivery_tested', True)
          or evidence.get('alert_delivery_tested') is True)
    fresh('alert_test_attestation_fresh', evidence.get('alert_delivery_tested_at'), 'max_alert_test_age_seconds')
    if not report['errors']:
        report['status'] = 'PASS'
    return report
