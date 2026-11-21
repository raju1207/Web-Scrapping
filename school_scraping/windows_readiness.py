"""Read-only Windows preparation audit; never connects to a database or enables jobs.

The report cannot authorize activation. Actual SQL identity/role/TLS checks remain
the responsibility of the existing production preflight, after separate approval.
Importing this module performs no probes and does not load .env.
"""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys


PROJECT_DIR = Path(__file__).resolve().parents[1]
APPROVALS = ('power', 'firewall', 'ntfs', 'task_design', 'source_access',
    'backup_retention', 'recovery_objectives', 'key_custody',
    'off_machine_destination', 'log_rotation', 'alert_delivery')

# Fixed commands only. Paths travel through one environment variable, never
# interpolated into executable PowerShell. Task arguments/service command lines
# are inspected for matching but never returned. .env contents are never read.
_WINDOWS_PROBE = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$auditRoot = $env:WINDOWS_AUDIT_PROJECT
$facts = @{}
$probeErrors = [System.Collections.Generic.List[string]]::new()
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $facts.os = @{caption=$os.Caption;version=$os.Version;build=$os.BuildNumber;architecture=$os.OSArchitecture}
} catch { $probeErrors.Add('WINDOWS_OS_PROBE_FAILED') }
try {
    $facts.timezone = (Get-TimeZone).Id
    $facts.local_time = (Get-Date -Format o)
} catch { $probeErrors.Add('WINDOWS_TIME_PROBE_FAILED') }
try {
    $pg = @(Get-Service -Name '*postgres*' -ErrorAction SilentlyContinue | ForEach-Object {
        @{name=$_.Name;status=[string]$_.Status;start_type=[string]$_.StartType}
    })
    $schedule = Get-Service -Name Schedule
    $facts.services = @{status='OK';postgresql=$pg;scheduler_running=($schedule.Status -eq 'Running')}
} catch { $facts.services=@{status='UNKNOWN'}; $probeErrors.Add('WINDOWS_SERVICES_PROBE_FAILED') }
try {
    $profiles = @(Get-NetFirewallProfile | ForEach-Object {
        @{name=$_.Name;enabled=([string]$_.Enabled -eq 'True' -or [string]$_.Enabled -eq '1');
          default_inbound=[string]$_.DefaultInboundAction;default_outbound=[string]$_.DefaultOutboundAction}
    })
    $facts.firewall = @{status='OK';profiles=$profiles}
} catch { $facts.firewall=@{status='UNKNOWN'}; $probeErrors.Add('WINDOWS_FIREWALL_PROBE_FAILED') }
try {
    $matches = @(Get-ScheduledTask | ForEach-Object {
        $task = $_
        $isProject = $false
        foreach ($action in $task.Actions) {
            $text = $action.Execute + ' ' + $action.Arguments + ' ' + $action.WorkingDirectory
            if ($text.IndexOf($auditRoot,[StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                $text -match '(?i)daily_scheduler\.py|saras_dynamic_scraper\.py|udise_dynamic_scraper\.py') { $isProject=$true }
        }
        if ($isProject) { @{state=[string]$task.State;triggers=@($task.Triggers | ForEach-Object {
            @{start_boundary=$_.StartBoundary;enabled=$_.Enabled}
        })} }
    })
    $facts.tasks = @{status='OK';project_tasks=$matches}
} catch { $facts.tasks=@{status='UNKNOWN'}; $probeErrors.Add('WINDOWS_TASKS_PROBE_FAILED') }
try {
    function Read-PowerIndices($setting) {
        $lines = & powercfg.exe /QUERY SCHEME_CURRENT SUB_SLEEP $setting 2>$null
        if ($LASTEXITCODE -ne 0) { throw 'Unavailable' }
        $values = [regex]::Matches(($lines -join "`n"),'0x([0-9a-fA-F]+)')
        if ($values.Count -lt 2) { throw 'Unavailable' }
        return @([Convert]::ToInt32($values[$values.Count-2].Groups[1].Value,16),
                 [Convert]::ToInt32($values[$values.Count-1].Groups[1].Value,16))
    }
    $sleep = Read-PowerIndices 'STANDBYIDLE'
    $wake = Read-PowerIndices 'RTCWAKE'
    $hibernate = Read-PowerIndices 'HIBERNATEIDLE'
    $facts.power = @{status='OK';ac_sleep_seconds=$sleep[0];dc_sleep_seconds=$sleep[1];
        ac_wake=$wake[0];dc_wake=$wake[1];ac_hibernate_seconds=$hibernate[0];dc_hibernate_seconds=$hibernate[1]}
} catch { $facts.power=@{status='UNKNOWN'}; $probeErrors.Add('WINDOWS_POWER_PROBE_FAILED') }
try {
    $broad = @('S-1-1-0','S-1-5-11','S-1-5-32-545')
    $secretBroad = $false
    $projectBroad = $false
    $secretExists = Test-Path -LiteralPath (Join-Path $auditRoot '.env') -PathType Leaf
    foreach ($suffix in @('', '.env', 'venv', 'logs')) {
        $target = if ($suffix) { Join-Path $auditRoot $suffix } else { $auditRoot }
        if (-not (Test-Path -LiteralPath $target)) { continue }
        $acl = Get-Acl -LiteralPath $target
        foreach ($rule in $acl.Access) {
            $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
            if ($rule.AccessControlType -eq 'Allow' -and $sid -in $broad) {
                if ($suffix -eq '.env' -and [long]$rule.FileSystemRights -ne 0) { $secretBroad=$true }
                if ([long]$rule.FileSystemRights -band 0x500d0116) { $projectBroad=$true }
            }
        }
    }
    $facts.acl = @{status='OK';env_exists=$secretExists;broad_secret_access=$secretBroad;broad_project_modify=$projectBroad}
} catch { $facts.acl=@{status='UNKNOWN'}; $probeErrors.Add('WINDOWS_ACL_PROBE_FAILED') }
$facts.errors = @($probeErrors.ToArray())
$facts | ConvertTo-Json -Depth 8 -Compress
"""


def _normal(path):
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            return False
        if item.exists() and getattr(item.lstat(), 'st_file_attributes', 0) & 0x400:
            return False
    return True


def _positive(value, *, integer=False):
    try:
        return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0 and (not integer or isinstance(value, int)))
    except OverflowError:
        return False


def _dependencies(root):
    """Read installed metadata only; no package import, install or network access."""
    rows = []
    for line in (root / 'requirements.txt').read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?((?:[<>=!~].*)?)', line)
        if not match:
            raise ValueError('Unsupported dependency declaration')
        name, constraints = match.groups()
        try:
            version = importlib.metadata.version(name)
            valid = True
            for constraint in filter(None, constraints.split(',')):
                if constraint.startswith('=='):
                    valid = valid and version == constraint[2:]
                    continue
                spec = re.fullmatch(r'(==|>=|<=|<|>)(\d+(?:\.\d+)*)', constraint)
                if not spec or not re.fullmatch(r'\d+(?:\.\d+)*', version):
                    valid = False
                    break
                op, target = spec.groups()
                actual_parts, wanted_parts = tuple(map(int, version.split('.'))), tuple(map(int, target.split('.')))
                width = max(len(actual_parts), len(wanted_parts))
                a = actual_parts + (0,) * (width - len(actual_parts))
                b = wanted_parts + (0,) * (width - len(wanted_parts))
                valid = valid and {'==': a == b, '>=': a >= b, '<=': a <= b, '>': a > b, '<': a < b}[op]
        except importlib.metadata.PackageNotFoundError:
            version, valid = None, False
        rows.append({'name': name, 'version': version, 'matches_requirement': valid})
    return rows


def collect_windows_facts(project_root=PROJECT_DIR, *, runner=None):
    """Bounded, read-only OS/client probes. No SQL, browser launch or .env load."""
    root = Path(project_root).absolute()
    runner = runner or subprocess.run
    facts = {'platform': sys.platform, 'errors': [], 'paths': {
        'project_normal': root.is_dir() and _normal(root),
        'venv_python': (root / 'venv/Scripts/python.exe').is_file() and _normal(root / 'venv'),
        'scheduler': (root / 'daily_scheduler.py').is_file(),
        'requirements': (root / 'requirements.txt').is_file(),
        'log_directory': (root / 'logs').is_dir() and _normal(root / 'logs')}}
    facts['python'] = {'version': '.'.join(map(str, sys.version_info[:3])), 'bits': struct.calcsize('P') * 8,
        'venv_matches': Path(sys.prefix).resolve() == (root / 'venv').resolve()
            and Path(sys.prefix).resolve() != Path(sys.base_prefix).resolve(),
        'dependencies_ok': False, 'browser_executable': False}
    try:
        rows = _dependencies(root)
        facts['python']['dependencies'] = rows
        facts['python']['dependencies_ok'] = bool(rows) and all(row['matches_requirement'] for row in rows)
        # Inspect browser files without starting Playwright's driver or a browser.
        browser_root = os.getenv('PLAYWRIGHT_BROWSERS_PATH')
        if browser_root == '0':
            browsers = root / 'venv/Lib/site-packages/playwright/driver/package/.local-browsers'
        elif browser_root:
            browsers = Path(browser_root)
        else:
            browsers = Path(os.getenv('LOCALAPPDATA', '')) / 'ms-playwright'
        facts['python']['browser_executable'] = any(path.is_file() and _normal(path)
            for path in browsers.glob('chromium-*/chrome-win*/chrome.exe'))
    except (OSError, ValueError, TypeError):
        facts['errors'].append('PYTHON_DEPENDENCY_PROBE_FAILED')
    try:
        disk = shutil.disk_usage(root)
        facts['disk'] = {'total_bytes': disk.total, 'free_bytes': disk.free}
    except OSError:
        facts['errors'].append('DISK_PROBE_FAILED')
    if sys.platform != 'win32':
        facts['errors'].append('WINDOWS_REQUIRED')
        return facts
    powershell = Path(os.getenv('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    try:
        env = os.environ.copy()
        env['WINDOWS_AUDIT_PROJECT'] = str(root)
        result = runner([str(powershell), '-NoProfile', '-NonInteractive', '-Command', _WINDOWS_PROBE],
            capture_output=True, text=True, encoding='utf-8-sig', errors='replace', timeout=45, env=env, check=True)
        windows = json.loads(result.stdout)
        if not isinstance(windows, dict):
            raise ValueError('Invalid probe result')
        facts['errors'].extend(windows.pop('errors', []))
        facts.update(windows)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        facts['errors'].append('WINDOWS_INSPECTION_UNAVAILABLE')
    pg_bin = Path(os.getenv('ProgramFiles', 'C:/Program Files')) / 'PostgreSQL/17/bin'
    tools = []
    for name in ('psql', 'pg_dump', 'pg_restore', 'pg_isready'):
        try:
            result = runner([str(pg_bin / (name + '.exe')), '--version'], capture_output=True,
                text=True, timeout=5, check=True)
            match = re.search(r'\(PostgreSQL\) (\d+(?:\.\d+)+)', result.stdout)
            version = match.group(1) if match else None
        except (OSError, subprocess.SubprocessError):
            version = None
        tools.append({'name': name, 'version': version})
    facts['postgresql'] = {'major': 17, 'tools_ok': all(row['version'] and row['version'].startswith('17.') for row in tools), 'tools': tools}
    return facts


def check_windows_readiness(*, project_root=PROJECT_DIR, facts=None, plan=None,
                            profile=None, config=None, readiness_checker=None):
    """Evaluate preparation evidence, never live SQL identity or permission to run."""
    facts = collect_windows_facts(project_root) if facts is None else facts
    facts, plan, profile, config = (value if isinstance(value, dict) else {} for value in (facts, plan, profile, config))
    report = {'status': 'BLOCKED', 'preparation_status': 'BLOCKED', 'activation_allowed': False,
        'read_only': True, 'database_connections': 0, 'database_identity_verified': False,
        'checks': {}, 'blockers': [], 'environment': {}}
    def check(name, passed):
        report['checks'][name] = 'PASS' if passed else 'BLOCKED'
        if not passed:
            report['blockers'].append(name)
        return passed
    def section(container, key):
        value = container.get(key)
        return value if isinstance(value, dict) else {}
    paths, python = section(facts, 'paths'), section(facts, 'python')
    services, power = section(facts, 'services'), section(facts, 'power')
    firewall, tasks, acl = (section(facts, key) for key in ('firewall', 'tasks', 'acl'))
    os_info = section(facts, 'os')
    check('WINDOWS_REQUIRED', facts.get('platform') == 'win32'
        and all(isinstance(os_info.get(key), str) and bool(os_info[key].strip()) for key in ('caption', 'version', 'build', 'architecture'))
        and os_info.get('architecture') == '64-bit')
    check('PROJECT_RUNTIME_PATHS_REQUIRED', all(paths.get(key) is True for key in
        ('project_normal', 'venv_python', 'scheduler', 'requirements', 'log_directory')))
    check('VENV_DEPENDENCIES_REQUIRED', isinstance(python.get('version'), str)
        and re.fullmatch(r'3\.14\.\d+', python['version']) is not None
        and python.get('bits') == 64 and python.get('venv_matches') is True
        and python.get('dependencies_ok') is True and python.get('browser_executable') is True)
    check('POSTGRESQL_17_TOOLS_REQUIRED', section(facts, 'postgresql').get('tools_ok') is True
        and section(facts, 'postgresql').get('major') == 17)
    check('WINDOWS_TIMEZONE_REVIEW_REQUIRED', facts.get('timezone') == 'India Standard Time')
    check('WINDOWS_TASK_SCHEDULER_REQUIRED', services.get('status') == 'OK' and services.get('scheduler_running') is True)
    check('EXISTING_PROJECT_TASK_REVIEW_REQUIRED', tasks.get('status') == 'OK'
        and isinstance(tasks.get('project_tasks'), list) and not tasks['project_tasks'])
    profiles = firewall.get('profiles')
    check('FIREWALL_REVIEW_REQUIRED', firewall.get('status') == 'OK' and isinstance(profiles, list)
        and len(profiles) == 3 and all(isinstance(row, dict) and isinstance(row.get('name'), str)
            and row.get('enabled') is True for row in profiles)
        and {row.get('name') for row in profiles} == {'Domain', 'Private', 'Public'})
    check('POWER_SLEEP_REMEDIATION_REQUIRED', power.get('status') == 'OK'
        and type(power.get('ac_sleep_seconds')) is int and power['ac_sleep_seconds'] == 0
        and type(power.get('ac_hibernate_seconds')) is int and power['ac_hibernate_seconds'] == 0)
    check('NTFS_REMEDIATION_REQUIRED', acl.get('status') == 'OK'
        and acl.get('broad_secret_access') is False and acl.get('broad_project_modify') is False)
    check('ENVIRONMENT_INSPECTION_COMPLETE', facts.get('errors') == [])
    check('DEPLOYMENT_APPROVAL_REQUIRED', type(plan.get('schema_version')) is int and plan['schema_version'] == 1
        and plan.get('approved') is True and isinstance(plan.get('approval_reference'), str) and bool(plan['approval_reference'].strip()))
    check('DEDICATED_WINDOWS_IDENTITY_REQUIRED', isinstance(plan.get('runtime_identity'), str) and bool(plan['runtime_identity'].strip()))
    approvals = section(plan, 'approvals')
    for name in APPROVALS:
        check(name.upper() + '_APPROVAL_REQUIRED', approvals.get(name) is True)
    expected_service = plan.get('database_service')
    pg_services = services.get('postgresql')
    check('APPROVED_DATABASE_SERVICE_REQUIRED', isinstance(expected_service, str) and bool(expected_service.strip())
        and isinstance(pg_services, list) and any(isinstance(row, dict) and row.get('name') == expected_service
            and row.get('status') == 'Running' for row in pg_services))
    schedule, defaults = section(config, 'scheduler'), section(config, 'database')
    check('ORIGINAL_ACTIVATION_FLAGS_MUST_STAY_DISABLED', schedule.get('enabled') is False and defaults.get('enabled') is False)
    check('APPROVED_2300_IST_SCHEDULE_REQUIRED', schedule.get('time') == '23:00' and schedule.get('timezone') == 'Asia/Kolkata')
    scheduler, db = section(profile, 'scheduler'), section(profile, 'database')
    check('PRODUCTION_PROFILE_APPROVAL_REQUIRED', profile.get('schema_version') == 1 and type(profile.get('schema_version')) is int
        and profile.get('approved') is True and isinstance(profile.get('approval_reference'), str) and bool(profile['approval_reference'].strip()))
    check('APPROVED_HARD_TIMEOUT_REQUIRED', scheduler.get('approved') is True and _positive(scheduler.get('hard_timeout_seconds')))
    check('FINITE_CANDIDATE_LIMIT_REQUIRED', _positive(plan.get('candidate_limit'), integer=True))
    jobs = profile.get('allowed_jobs')
    from school_scraping.datasets import academic_year
    def scoped(job):
        if not isinstance(job, dict) or job.get('source') not in ('saras', 'udise') or job.get('policy') not in ('new-only', 'refresh'):
            return False
        if not isinstance(job.get('states'), list) or not job['states'] or not all(isinstance(v, str) and v.strip() for v in job['states']):
            return False
        if not all(isinstance(job.get(key, []), list) and all(isinstance(v, str) for v in job.get(key, [])) for key in ('districts', 'pincodes')):
            return False
        try:
            if job['source'] == 'udise':
                academic_year(job.get('academic_year'))
        except (ValueError, TypeError):
            return False
        return True
    check('EXPLICIT_SOURCE_SCOPE_YEAR_REQUIRED', isinstance(jobs, list) and bool(jobs) and all(scoped(job) for job in jobs))
    port, name, user = db.get('port'), db.get('name'), db.get('user')
    protected = profile.get('protected_targets')
    dedicated = (db.get('host') in ('127.0.0.1', '::1') and _positive(port, integer=True) and port <= 65535
        and port not in (5432, 55439, 55449) and isinstance(name, str) and bool(name.strip())
        and name.casefold() not in ('postgres', 'template0', 'template1')
        and not name.casefold().startswith(('section', 'database_only_test', 'memory_'))
        and isinstance(user, str) and bool(user.strip()) and user.casefold() not in ('postgres', 'memory_test_admin')
        and isinstance(protected, list) and bool(protected) and all(isinstance(row, dict) for row in protected))
    if dedicated:
        dedicated = not any(row.get('host') in ('*', db['host']) and row.get('port') == port
            and (not row.get('name') or row['name'] == name) for row in protected)
    check('DEDICATED_LOOPBACK_DATABASE_REQUIRED', dedicated)
    check('DATABASE_IDENTITY_SETTINGS_REQUIRED', db.get('major_version') == 17
        and _positive(db.get('database_oid'), integer=True) and type(db.get('schema_version')) is int
        and db['schema_version'] == 1 and db.get('canonical_schema_version') == '4.0')
    timeouts = section(db, 'timeouts')
    check('DATABASE_TIMEOUTS_REQUIRED', all(_positive(timeouts.get(key), integer=True)
        for key in ('connect_seconds', 'statement_ms', 'lock_ms'))
        and timeouts.get('lock_ms', math.inf) <= timeouts.get('statement_ms', -1))
    tls = section(db, 'tls')
    certificate_ok = False
    try:
        certificate = tls.get('root_certificate')
        certificate_ok = isinstance(certificate, str) and bool(certificate.strip()) and Path(certificate).is_file() and _normal(Path(certificate))
    except OSError:
        pass
    check('TLS_POLICY_APPROVAL_REQUIRED', (tls.get('mode') == 'verify-full' and certificate_ok)
        or (tls.get('mode') == 'approved-local' and db.get('host') in ('127.0.0.1', '::1')
        and tls.get('approved_local_exception') is True and isinstance(tls.get('reason'), str) and bool(tls['reason'].strip())))
    health_settings, disk = section(profile, 'readiness'), section(facts, 'disk')
    minimum, ratio = health_settings.get('min_disk_free_bytes'), health_settings.get('min_disk_free_ratio')
    free, total = disk.get('free_bytes'), disk.get('total_bytes')
    check('APPROVED_DISK_THRESHOLDS_REQUIRED', _positive(minimum, integer=True) and _positive(ratio) and ratio < 1
        and _positive(total, integer=True) and isinstance(free, int) and not isinstance(free, bool)
        and 0 <= free <= total and free >= minimum and free / total >= ratio)
    from school_scraping.readiness import check_readiness
    checker = readiness_checker or check_readiness
    try:
        health = checker(health_settings)
        check('BACKUP_MONITORING_READINESS_REQUIRED', isinstance(health, dict) and health.get('status') == 'PASS')
        check('DEPLOYED_MONITORING_REQUIRED', isinstance(health, dict) and health.get('deployed_monitoring') is True)
    except Exception:
        check('BACKUP_MONITORING_READINESS_REQUIRED', False)
    # Emit selected ordinary facts only, never profile, environment, task/service
    # arguments, ACL principals, dependency error strings or connection settings.
    for group, keys in (
        ('os', ('caption', 'version', 'build', 'architecture')),
        ('disk', ('total_bytes', 'free_bytes')),
        ('power', ('status', 'ac_sleep_seconds', 'dc_sleep_seconds', 'ac_wake', 'dc_wake', 'ac_hibernate_seconds', 'dc_hibernate_seconds')),
        ('postgresql', ('major', 'tools_ok'))):
        observed = section(facts, group)
        report['environment'][group] = {key: observed.get(key) for key in keys
            if observed.get(key) is None or isinstance(observed.get(key), (str, int, float, bool))}
    for key in ('timezone', 'local_time'):
        report['environment'][key] = facts.get(key) if isinstance(facts.get(key), str) else None
    tools = section(facts, 'postgresql').get('tools')
    report['environment']['postgresql']['tools'] = [{key: row.get(key) for key in ('name', 'version')}
        for row in (tools if isinstance(tools, list) else []) if isinstance(row, dict)
        and all(row.get(key) is None or isinstance(row.get(key), str) for key in ('name', 'version'))]
    report['environment']['firewall'] = {'status': firewall.get('status') if isinstance(firewall.get('status'), str) else None, 'profiles': [
        {key: row.get(key) for key in ('name', 'enabled', 'default_inbound', 'default_outbound')
            if row.get(key) is None or isinstance(row.get(key), (str, bool))}
        for row in (profiles if isinstance(profiles, list) else []) if isinstance(row, dict)]}
    report['environment']['acl'] = {key: acl.get(key) for key in
        ('status', 'env_exists', 'broad_secret_access', 'broad_project_modify')
        if acl.get(key) is None or isinstance(acl.get(key), (str, bool))}
    report['environment']['python'] = {key: python.get(key) for key in
        ('version', 'bits', 'venv_matches', 'dependencies_ok', 'browser_executable')
        if python.get(key) is None or isinstance(python.get(key), (str, int, float, bool))}
    report['environment']['postgresql_services'] = [{key: row.get(key) for key in ('name', 'status', 'start_type')}
        for row in (pg_services if isinstance(pg_services, list) else []) if isinstance(row, dict)
        and all(row.get(key) is None or isinstance(row.get(key), str) for key in ('name', 'status', 'start_type'))]
    report['environment']['project_task_count'] = len(tasks['project_tasks']) if isinstance(tasks.get('project_tasks'), list) else None
    if not report['blockers']:
        report['preparation_status'] = 'PASS'
    report['blockers'].append('LIVE_DATABASE_PREFLIGHT_REQUIRED')
    report['checks']['LIVE_DATABASE_PREFLIGHT_REQUIRED'] = 'BLOCKED'
    return report


def _read_object(path):
    path = Path(path)
    if not _normal(path) or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError('Invalid configuration file')
    result = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(result, dict):
        raise ValueError('Expected JSON object')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=PROJECT_DIR)
    parser.add_argument('--profile', type=Path, help='Approved production profile; default is the inactive example.')
    parser.add_argument('--plan', type=Path, help='Windows preparation approvals; default is the inactive example.')
    parser.add_argument('--config', type=Path, help='Original shared collection settings; read only.')
    args = parser.parse_args(argv)
    root = args.project_root.absolute()
    try:
        config = _read_object(args.config or root / 'config/states_pincodes.json')
        profile = _read_object(args.profile or root / 'config/production_safety.example.json')
        plan = _read_object(args.plan or root / 'config/windows_local_deployment.example.json')
    except (OSError, ValueError, TypeError):
        print(json.dumps({'status': 'BLOCKED', 'read_only': True, 'activation_allowed': False,
            'database_connections': 0, 'blockers': ['CONFIGURATION_UNREADABLE']}))
        return 2
    try:
        report = check_windows_readiness(project_root=root, config=config, profile=profile, plan=plan)
        print(json.dumps(report, indent=2, ensure_ascii=True))
    except Exception:
        print(json.dumps({'status': 'BLOCKED', 'read_only': True, 'activation_allowed': False,
            'database_connections': 0, 'blockers': ['READINESS_AUDIT_FAILED']}))
        return 2
    return 2  # Preparation never substitutes for the existing live admission gate.


if __name__ == '__main__':
    raise SystemExit(main())
