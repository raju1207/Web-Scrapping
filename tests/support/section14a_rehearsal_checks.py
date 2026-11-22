"""Synthetic restricted-login rehearsal; no connections or I/O on import."""
import hashlib
import importlib.util
import json
from pathlib import Path
import uuid
from unittest.mock import patch

from database import scrape_pipeline as pipeline
from school_scraping.database_only import DatabaseOnlyStore
from school_scraping.ack_reconciliation import reconcile_imports


ROOT = Path(__file__).resolve().parents[2]


class RehearsalCheckFailed(RuntimeError):
    pass


def _require(value, code):
    if not value:
        raise RehearsalCheckFailed(code)


def verify_restricted_pipeline(*, app_connection_factory, observer_connection_factory,
                               expected_identity, work_root):
    """Use opaque factories for this new rehearsal only; return metadata, not secrets.

    Root supplies the actual application environment connector and an independently
    guarded read-only observer. Existing integration tests cover mocked discovery,
    source detail skipping, privilege denials and additional failure boundaries.
    """
    result = {'status': 'FAILED', 'errors': [], 'sources': {}, 'imports_committed': 0,
              'local_school_payload_files': 0, 'live_source_requests': 0,
              'history_policy': 'pipeline_preservation_only', 'database_connections': 0}
    observer, stores, connections = None, [], []
    try:
        expected = expected_identity
        _require(isinstance(expected, dict) and expected.get('host') == '127.0.0.1'
            and type(expected.get('port')) is int and 1 <= expected['port'] <= 65535
            and expected['port'] not in (5432,55439,55449)
            and isinstance(expected.get('database'), str) and expected['database'].startswith('section14a_app_')
            and isinstance(expected.get('user'), str) and expected['user'].startswith('section14a_scraper_')
            and type(expected.get('oid')) is int and expected['oid'] > 0
            and expected.get('version') == '17.5', 'UNAPPROVED_REHEARSAL_IDENTITY')

        def verify(connection, application=False):
            _require(connection.autocommit, 'AUTOCOMMIT_CONNECTION_REQUIRED')
            with connection.transaction():
                connection.execute('SET TRANSACTION READ ONLY')
                row = connection.execute('SELECT host(inet_server_addr()),inet_server_port(),'
                    'current_database(),current_user,(SELECT oid FROM pg_database WHERE datname=current_database()),'
                    "current_setting('server_version')").fetchone()
            actual = dict(zip(('host','port','database','user','oid','version'),row))
            _require(all(actual[key] == expected[key] for key in ('host','port','database','oid','version')),
                     'DATABASE_IDENTITY_MISMATCH')
            _require(not application or actual['user'] == expected['user'], 'APPLICATION_LOGIN_MISMATCH')
            return actual

        observer = observer_connection_factory()
        result['database_connections'] += 1
        verify(observer)

        def read(sql, parameters=()):
            with observer.transaction():
                observer.execute('SET TRANSACTION READ ONLY')
                return observer.execute(sql,parameters).fetchall()

        def scalar(sql, parameters=()):
            return read(sql,parameters)[0][0]

        def application():
            connection = app_connection_factory()
            result['database_connections'] += 1
            try:
                result['application_identity'] = verify(connection,True)
            except BaseException:
                connection.close()
                raise
            connections.append(connection)
            return connection

        spec = importlib.util.spec_from_file_location('section14a_raw_builders',ROOT/'tests/test_scrape_pipeline.py')
        fixtures = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixtures)

        def store(source,year=''):
            value = DatabaseOnlyStore(source,year,connect=application)
            stores.append(value)
            original = value._memory._apply_db
            def observe(connection,event):
                if event['kind'] == 'import_acknowledged':
                    _require(scalar('SELECT count(*) FROM school_scraping.scrape_events '
                        "WHERE event_id=%s AND kind='import_completed'",
                        (event['payload']['completion_event_id'],)) == 1, 'ACK_BEFORE_COMPLETION_COMMIT')
                    result['post_commit_ack_observed'] = True
                applied = original(connection,event)
                if event['kind'] in ('import_completed','import_acknowledged'):
                    _require(scalar('SELECT count(*) FROM school_scraping.scrape_events WHERE event_id=%s',
                        (event['event_id'],)) == 0, 'UNCOMMITTED_RECEIPT_VISIBLE')
                return applied
            value._memory._apply_db = observe
            return value

        def import_one(value,source,identifier,raw,year=''):
            with patch.object(Path,'open',side_effect=RehearsalCheckFailed('LOCAL_PAYLOAD_FILE_ACCESS')):
                imported = value.import_record(identifier,raw,'Punjab',new_only=True)
            _require(imported.get('acknowledged') is True,'ACK_MISSING')
            result['imports_committed'] += 1
            row = read('SELECT s.entity_id,e.entity_id,s.source_system,a.payload '
                'FROM school_scraping.school_source_records s '
                'JOIN school_scraping.school_entities e ON e.entity_id=s.entity_id '
                'JOIN school_scraping.json_file_archive a ON a.file_id=s.archive_file_id '
                'WHERE s.source_record_pk=%s',(imported['source_record_pk'],))
            _require(len(row) == 1 and row[0][0] == row[0][1] == imported['entity_id']
                and row[0][2] == source,'SOURCE_ENTITY_RELATIONSHIP_MISMATCH')
            canonical = row[0][3]
            _require(canonical['metadata']['schema_version'] == '4.0','CANONICAL_SCHEMA_MISMATCH')
            _require(str(canonical['metadata']['udise_code'] if source=='udise' else
                canonical['board_affiliation']['affiliation_number']) == identifier,'CANONICAL_IDENTITY_MISMATCH')
            archive_ids = [imported[key] for key in ('raw_archive_file_id','canonical_archive_file_id',
                                                   'event_raw_archive_file_id','event_canonical_archive_file_id')]
            archives = read('SELECT file_id,dataset_layer,payload,raw_json,content_sha256 '
                'FROM school_scraping.json_file_archive WHERE file_id=ANY(%s)',(archive_ids,))
            by_id = {item[0]:item for item in archives}
            _require(len(by_id) == 4,'ARCHIVE_OBSERVATIONS_MISSING')
            for item in archives:
                _require(json.loads(item[3]) == item[2] and hashlib.sha256(item[3].encode()).hexdigest() == item[4],
                         'ARCHIVE_JSONB_CHECKSUM_MISMATCH')
            _require(by_id[imported['raw_archive_file_id']][2] == raw
                and by_id[imported['event_raw_archive_file_id']][2] == raw,'RAW_JSONB_MISMATCH')
            _require(by_id[imported['canonical_archive_file_id']][2] == canonical
                and by_id[imported['event_canonical_archive_file_id']][2] == canonical,'CANONICAL_JSONB_MISMATCH')
            _require(all(by_id[imported[key]][1] == 'history' for key in
                ('event_raw_archive_file_id','event_canonical_archive_file_id')),'HISTORY_LAYER_MISSING')
            if source == 'udise':
                snapshots = read('SELECT y.snapshot_payload,a.payload FROM school_scraping.school_yearly_snapshots y '
                    'JOIN school_scraping.json_file_archive a ON a.file_id=y.source_archive_file_id '
                    'WHERE y.source_record_pk=%s AND y.academic_year=%s',(imported['source_record_pk'],year))
                _require(len(snapshots) == 1 and snapshots[0][0] == pipeline.udise_mapper.build_yearly_snapshot(canonical)
                    and snapshots[0][0] in snapshots[0][1],'YEARLY_SNAPSHOT_MISMATCH')
            _require(reconcile_imports(observer,source=source,run_id=value.run_id)['status'] == 'PASS',
                     'RECEIPT_CHECKPOINT_RECONCILIATION_FAILED')
            return imported

        saras_id = str(uuid.uuid4().int%9000000+1000000)
        udise_id = str(uuid.uuid4().int%90000000000+10000000000)
        saras = store('saras')
        _require(saras.lookup_committed([saras_id]) == set(),'SYNTHETIC_ID_COLLISION')
        raw = fixtures.saras_raw(saras_id,'Section14A restricted synthetic SARAS')
        first = import_one(saras,'saras',saras_id,raw)
        history_ids = [first['event_raw_archive_file_id'],first['event_canonical_archive_file_id']]
        before_history = read('SELECT file_id,raw_json,content_sha256 FROM school_scraping.json_file_archive '
            'WHERE file_id=ANY(%s) ORDER BY file_id',(history_ids,))
        count = scalar('SELECT count(*) FROM school_scraping.scrape_events WHERE run_id=%s',(saras.run_id,))
        _require(saras.lookup_committed([saras_id]) == {saras_id},'SARAS_EXISTING_LOOKUP_FAILED')
        repeat = saras.import_record(saras_id,raw,'Punjab',new_only=True)
        _require(repeat.get('skipped') and repeat.get('status') == 'already_committed','SARAS_EXISTING_NOT_SKIPPED')
        _require(scalar('SELECT count(*) FROM school_scraping.scrape_events WHERE run_id=%s',(saras.run_id,)) == count,
                 'SKIP_CREATED_IMPORT_RECEIPTS')
        _require(before_history == read('SELECT file_id,raw_json,content_sha256 FROM school_scraping.json_file_archive '
            'WHERE file_id=ANY(%s) ORDER BY file_id',(history_ids,)),'HISTORY_CHANGED_ON_REPEAT')
        result['sources']['saras'] = {'new_import':True,'existing_skip':True,'raw_jsonb':True,
            'canonical_jsonb':True,'history_preserved':True,'identifier':saras_id}
        old = store('udise','2024-25')
        _require(old.lookup_committed([udise_id]) == set(),'SYNTHETIC_ID_COLLISION')
        previous = import_one(old,'udise',udise_id,fixtures.udise_raw(udise_id,'2024-25'),'2024-25')
        current = store('udise','2025-26')
        _require(current.lookup_committed([udise_id]) == set(),'MISSING_YEAR_INCORRECTLY_SKIPPED')
        raw = fixtures.udise_raw(udise_id,'2025-26')
        imported = import_one(current,'udise',udise_id,raw,'2025-26')
        _require(imported['source_record_pk'] == previous['source_record_pk']
            and imported['entity_id'] == previous['entity_id'],'YEAR_IMPORT_DUPLICATED_IDENTITY')
        _require(current.lookup_committed([udise_id]) == {udise_id},'UDISE_EXISTING_YEAR_LOOKUP_FAILED')
        _require(current.import_record(udise_id,raw,'Punjab',new_only=True).get('skipped'),'UDISE_EXISTING_YEAR_NOT_SKIPPED')
        _require(scalar('SELECT count(*) FROM school_scraping.school_yearly_snapshots WHERE source_record_pk=%s',
            (imported['source_record_pk'],)) == 2,'YEARLY_SNAPSHOT_DUPLICATED')
        result['sources']['udise'] = {'new_import':True,'existing_year_skip':True,'missing_year_eligible':True,
            'yearly_snapshots':2,'raw_jsonb':True,'canonical_jsonb':True,'identifier':udise_id}
        failed = store('saras')
        failed_id = str(uuid.uuid4().int%9000000+1000000)
        invalid = fixtures.saras_raw(failed_id,'Section14A rollback synthetic fixture')
        invalid['school_details']['Affiliation Number'] = str(int(failed_id)+1)
        rejected = False
        try:
            failed.import_record(failed_id,invalid,'Punjab')
        except pipeline.PipelineValidationError:
            rejected = True
        _require(rejected and failed.lookup_committed([failed_id]) == set(),'VALIDATION_DID_NOT_ROLL_BACK')
        _require(scalar('SELECT count(*) FROM school_scraping.scrape_events WHERE run_id=%s',(failed.run_id,)) == 0
            and scalar('SELECT count(*) FROM school_scraping.scrape_runs WHERE run_id=%s',(failed.run_id,)) == 0,
            'ROLLBACK_LEFT_RECEIPTS')
        result['atomic_validation_rollback'] = True
        work = Path(work_root)
        _require(not work.exists() or not any(work.rglob('*.json')) and not any(work.rglob('*.jsonl')),
                 'LOCAL_PAYLOAD_FILES_FOUND')
        result.update(status='PASSED',actual_application_connector=True,canonical_schema_version='4.0')
    except Exception as error:
        result['errors'].append(str(error) if isinstance(error,RehearsalCheckFailed) else 'RESTRICTED_PIPELINE_CHECK_FAILED')
        result['error_type'] = type(error).__name__
    finally:
        for store in stores:
            try:store.close()
            except Exception:pass
        for connection in connections:
            try:connection.close()
            except Exception:pass
        if observer is not None:
            try:observer.close()
            except Exception:pass
    return result
