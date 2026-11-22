"""Explicit Section14A test-target extension; never provisions or mutates a DB.

Without its opt-in manifest, callers retain their original historical guards.
With it, malformed configuration or identity failure raises instead of skipping.
No passwords, DSNs or server exception messages are exposed by this helper.
"""
import json
import os
from pathlib import Path
import re

from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUARD_ENV = 'SCHOOL_SCRAPING_SECTION14A_GUARD'
ADMIN_ENV = 'SCHOOL_SCRAPING_SECTION14A_ADMIN_DSN'


def _require(condition):
    if not condition:
        raise ValueError('Guard rejected')


def _normal(path):
    for item in (path, *path.parents):
        _require(not item.is_symlink() and not getattr(item, 'is_junction', lambda: False)())
        if item.exists():
            _require(not getattr(item.lstat(), 'st_file_attributes', 0) & 0x400)
    return path.resolve()


def _manifest():
    supplied = Path(os.environ[GUARD_ENV])
    _require(supplied.is_absolute())
    path = _normal(supplied)
    _require(path.is_file() and path.stat().st_size <= 65536)
    value = json.loads(path.read_text(encoding='utf-8-sig'))
    _require(isinstance(value, dict) and type(value.get('version')) is int and value['version'] == 1)
    root_path = Path(value['root'])
    data_path = Path(value['data_directory'])
    _require(root_path.is_absolute() and data_path.is_absolute())
    root, data = _normal(root_path), _normal(data_path)
    _require(root.is_dir() and data.is_dir() and path.parent == root)
    _require(root.parent == (PROJECT_ROOT / '.refactor_validation').resolve())
    match = re.fullmatch(r'section14a_pg17_rehearsal_([0-9a-f]{12})', root.name)
    _require(match is not None and data == root / 'pgdata')
    nonce = match.group(1)
    _require(value.get('host') == '127.0.0.1' and type(value.get('port')) is int
        and 1 <= value['port'] <= 65535 and value['port'] not in (5432, 55439, 55449))
    _require(type(value.get('major_version')) is int and value['major_version'] == 17)
    _require(isinstance(value.get('system_identifier'), str)
        and re.fullmatch(r'[1-9][0-9]{0,19}', value['system_identifier']) is not None
        and int(value['system_identifier']) <= 2 ** 64 - 1)
    databases = value.get('databases')
    _require(isinstance(databases, dict) and bool(databases))
    for name, database in databases.items():
        _require(re.fullmatch(r'section14a_[a-z0-9_]+_' + nonce, name) is not None
            and isinstance(database, dict) and type(database.get('oid')) is int and database['oid'] > 0)
        users = database.get('users')
        _require(isinstance(users, list) and bool(users)
            and all(isinstance(user, str) and re.fullmatch(r'section14a_[a-z0-9_]+_' + nonce, user) for user in users))
    return value, root, data


def _settings(dsn, manifest):
    settings = conninfo_to_dict(dsn)
    _require(settings.get('host') == '127.0.0.1'
        and settings.get('hostaddr', '127.0.0.1') == '127.0.0.1'
        and settings.get('port') == str(manifest['port'])
        and not settings.get('service') and not settings.get('replication')
        and not os.getenv('PGSERVICE') and os.getenv('PGHOSTADDR', '127.0.0.1') == '127.0.0.1')
    database = manifest['databases'].get(settings.get('dbname'))
    _require(database is not None and settings.get('user') in database['users'])
    return settings, database


def _identity(dsn, settings, connect, *, privileged):
    connection = None
    try:
        connection = connect(dsn, hostaddr='127.0.0.1', connect_timeout=5, autocommit=True,
            row_factory=dict_row, options='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000')
        extra = (",current_setting('data_directory') AS data_directory,"
                 "(pg_control_system()).system_identifier::text AS system_identifier") if privileged else ''
        result = connection.execute("SELECT current_database() AS database_name,current_user AS user_name,"
            "host(inet_server_addr()) AS server_address,inet_server_port() AS server_port,"
            "current_setting('server_version_num')::integer/10000 AS major_version,"
            "(SELECT oid FROM pg_database WHERE datname=current_database()) AS database_oid,"
            "current_setting('transaction_read_only') AS read_only" + extra).fetchone()
        _require(result is not None and result['database_name'] == settings['dbname']
            and result['user_name'] == settings['user'] and result['read_only'] == 'on')
        return result
    finally:
        if connection is not None:
            connection.close()


def section14a_target(dsn, *, connect=None):
    """Return verified metadata, or None only when no Section14A guard is set."""
    if GUARD_ENV not in os.environ:
        return None
    try:
        manifest, root, data = _manifest()
        requested, database = _settings(dsn, manifest)
        admin_dsn = os.environ[ADMIN_ENV]
        admin_settings, admin_database = _settings(admin_dsn, manifest)
        if connect is None:
            import psycopg
            connect = psycopg.connect
        admin = _identity(admin_dsn, admin_settings, connect, privileged=True)
        _require(admin['server_address'] == manifest['host'] and admin['server_port'] == manifest['port']
            and admin['major_version'] == manifest['major_version'] and admin['database_oid'] == admin_database['oid']
            and _normal(Path(admin['data_directory'])) == data
            and admin['system_identifier'] == manifest['system_identifier'])
        actual = _identity(dsn, requested, connect, privileged=False)
        _require(actual['server_address'] == manifest['host'] and actual['server_port'] == manifest['port']
            and actual['major_version'] == manifest['major_version'] and actual['database_oid'] == database['oid'])
        return {'host': manifest['host'], 'port': manifest['port'], 'database': actual['database_name'],
            'user': actual['user_name'], 'database_oid': actual['database_oid'], 'major_version': actual['major_version'],
            'data_directory': str(data), 'system_identifier': manifest['system_identifier'], 'root': str(root)}
    except Exception:
        raise RuntimeError('Section14A test target verification failed; no test writes are authorized') from None
