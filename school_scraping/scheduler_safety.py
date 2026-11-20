"""Production scheduler admission; evaluation never launches a child or repairs data."""
import copy
import json
import math
import os
from pathlib import Path


def required(args):
    return bool(getattr(args, 'production_profile', None) or getattr(args, 'preflight_only', False)
        or getattr(args, 'storage_mode', None) == 'database'
        or any(getattr(job, 'storage_mode', None) == 'database'
               for _, job in getattr(args, 'resolved_jobs', [])))


def evaluate(args, *, preflight=None):
    """Return (safe report, private child environment). Never serialize the latter.

    Database-only scheduled jobs require this gate even if the profile flag was
    omitted. Legacy file-mode jobs retain their existing explicit behavior.
    """
    report = {'status': 'BLOCKED', 'errors': [], 'jobs': [], 'read_only': True}
    if not required(args):
        report['status'] = 'NOT_REQUIRED'
        return report, None
    path = getattr(args, 'production_profile', None)
    if path is None:
        report['errors'].append('PRODUCTION_PROFILE_REQUIRED')
        return report, None
    try:
        path = Path(path)
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError()
        profile = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(profile, dict):
            raise ValueError()
    except (OSError, ValueError, TypeError):
        report['errors'].append('PRODUCTION_PROFILE_INVALID')
        return report, None
    if not isinstance(profile.get('scheduler'), dict):
        report['errors'].append('PRODUCTION_PROFILE_INVALID')
        return report, None
    approved_timeout = profile['scheduler'].get('hard_timeout_seconds')
    timeout = getattr(args, 'child_timeout', None)
    if timeout is None:
        timeout = approved_timeout
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout)
            or timeout <= 0 or isinstance(approved_timeout, bool)
            or not isinstance(approved_timeout, (int, float)) or not math.isfinite(approved_timeout)
            or not 0 < timeout <= approved_timeout):
        report['errors'].append('APPROVED_HARD_TIMEOUT_REQUIRED')
        return report, None
    jobs = getattr(args, 'resolved_jobs', [])
    if not jobs:
        report['errors'].append('EXPLICIT_APPROVED_JOBS_REQUIRED')
        return report, None
    from school_scraping.production_safety import production_preflight, runtime_environment
    checker = preflight or production_preflight
    for source, job in jobs:
        selected = copy.copy(job)
        selected.scheduler_enabled = bool(getattr(args, 'scheduler_enabled', False))
        selected.child_timeout = timeout
        try:
            result = checker(selected, source, profile)
        except Exception:
            result = {'status': 'BLOCKED', 'errors': ['PREFLIGHT_FAILED']}
        if not isinstance(result, dict):
            result = {'status': 'BLOCKED', 'errors': ['PREFLIGHT_FAILED']}
        report['jobs'].append({'source': source, 'preflight': result})
        if result.get('status') != 'PASS':
            report['errors'].append('JOB_PREFLIGHT_BLOCKED')
    if report['errors']:
        return report, None
    try:
        policy = runtime_environment(profile)
    except Exception:
        report['errors'].append('RUNTIME_POLICY_INVALID')
        return report, None
    child_environment = os.environ.copy()
    child_environment.update(policy)
    args.child_timeout = timeout
    report.update(status='PASS', child_timeout_seconds=timeout)
    return report, child_environment
