"""Bounded, read-only detection of committed imports that need operator review.

This module never opens a connection, replays an import, or repairs a checkpoint.
The caller supplies an idle connection to its independently approved database.
"""
import re

from psycopg.pq import TransactionStatus


_COMPLETIONS = """
SELECT jsonb_build_object(
    'completion_event_id', e.event_id, 'source', e.source,
    'identifier', e.payload->>'school_identifier',
    'academic_year', COALESCE(e.payload->>'academic_year',''),
    'ack_count', (SELECT count(*) FROM school_scraping.scrape_events ack
        WHERE requested.event_id IS NOT NULL AND ack.kind='import_acknowledged' AND ack.source=e.source
          AND ack.run_id IS NOT DISTINCT FROM e.run_id
          AND ack.payload->>'run_id' IS NOT DISTINCT FROM e.run_id
          AND ack.occurred_at>=e.occurred_at
          AND ack.payload->>'source'=e.source
          AND ack.payload->>'completion_event_id'=e.event_id
          AND ack.payload->>'import_event_id'=e.payload->>'import_event_id'
          AND ack.payload->>'school_identifier'=e.payload->>'school_identifier'
          AND COALESCE(ack.payload->>'academic_year','')=COALESCE(e.payload->>'academic_year','')
          AND ack.payload->'source_record_pk'=e.payload->'source_record_pk'
          AND ack.payload->'entity_id'=e.payload->'entity_id'
          AND ack.payload->'raw_archive_file_id'=e.payload->'raw_archive_file_id'
          AND ack.payload->'canonical_archive_file_id'=e.payload->'canonical_archive_file_id'
          AND ack.payload->'event_raw_archive_file_id'=e.payload->'event_raw_archive_file_id'
          AND ack.payload->'event_canonical_archive_file_id'=e.payload->'event_canonical_archive_file_id'
          AND ack.payload->>'raw_sha256'=e.payload->>'raw_sha256'
          AND ack.payload->>'content_hash'=e.payload->>'content_hash'),
    'committed', COALESCE(requested.event_id IS NOT NULL
        AND s.source_record_pk IS NOT NULL AND master.entity_id IS NOT NULL
        AND s.entity_id::text=e.payload->>'entity_id'
        AND current_canonical.file_id IS NOT NULL
        AND current_canonical.payload->'metadata'->>'schema_version'='4.0'
        AND current_canonical.payload->'metadata'->>'record_id'=s.source_record_id
        AND canonical.file_id IS NOT NULL AND canonical.dataset_layer IN ('canonical','history')
        AND canonical.payload->'metadata'->>'schema_version'='4.0'
        AND canonical.payload->'metadata'->>'record_id'=s.source_record_id
        AND raw.file_id IS NOT NULL AND raw.content_sha256=e.payload->>'raw_sha256'
        AND ((e.source='saras' AND s.cbse_affiliation_number=e.payload->>'school_identifier'
              AND canonical.payload->'board_affiliation'->>'affiliation_number'=s.cbse_affiliation_number)
          OR (e.source='udise' AND s.udise_code=e.payload->>'school_identifier'
              AND canonical.payload->'metadata'->>'udise_code'=s.udise_code
              AND canonical.payload->'provenance'->>'academic_year'=e.payload->>'academic_year'
              AND yearly.snapshot_id IS NOT NULL AND yearly.schema_version='4.0'
              AND yearly.snapshot_payload->>'schema_version'='4.0'
              AND yearly.snapshot_payload->>'record_id'=s.source_record_id
              AND yearly.snapshot_payload->>'udise_code'=s.udise_code
              AND yearly.snapshot_payload->>'academic_year'=e.payload->>'academic_year'
              AND yearly.source='UDISE+' AND yearly.snapshot_payload->>'source'=yearly.source
              AND yearly_archive.payload @> jsonb_build_array(yearly.snapshot_payload))), false),
    'latest_observation', NOT EXISTS (SELECT 1 FROM school_scraping.scrape_events newer
        WHERE newer.kind='import_completed' AND newer.source=e.source
          AND newer.payload->>'school_identifier'=e.payload->>'school_identifier'
          AND COALESCE(newer.payload->>'academic_year','')=COALESCE(e.payload->>'academic_year','')
          AND (newer.occurred_at,newer.event_id)>(e.occurred_at,e.event_id)),
    'checkpoint_consistent', COALESCE(cp.imported AND cp.checkpoint_status='completed'
        AND cp.last_known_content_hash=e.payload->>'content_hash',false))
FROM school_scraping.scrape_events e
LEFT JOIN school_scraping.scrape_events requested
  ON requested.event_id=e.payload->>'import_event_id' AND requested.kind='import_requested'
  AND requested.source=e.source AND requested.run_id IS NOT DISTINCT FROM e.run_id
  AND requested.payload->>'run_id' IS NOT DISTINCT FROM e.run_id
  AND e.payload->>'run_id' IS NOT DISTINCT FROM e.run_id
  AND requested.occurred_at<=e.occurred_at AND requested.payload->>'source'=e.source
  AND e.payload->>'source'=e.source
  AND requested.payload->>'school_identifier'=e.payload->>'school_identifier'
  AND COALESCE(requested.payload->>'academic_year','')=COALESCE(e.payload->>'academic_year','')
  AND requested.payload->>'raw_sha256'=e.payload->>'raw_sha256'
  AND requested.payload->>'content_hash'=e.payload->>'content_hash'
LEFT JOIN school_scraping.school_source_records s
  ON s.source_record_pk::text=e.payload->>'source_record_pk' AND s.source_system=e.source
LEFT JOIN school_scraping.school_entities master ON master.entity_id=s.entity_id
LEFT JOIN school_scraping.json_file_archive current_canonical ON current_canonical.file_id=s.archive_file_id
LEFT JOIN school_scraping.json_file_archive canonical
  ON canonical.file_id::text=COALESCE(e.payload->>'event_canonical_archive_file_id',e.payload->>'canonical_archive_file_id')
LEFT JOIN school_scraping.json_file_archive raw
  ON raw.file_id::text=COALESCE(e.payload->>'event_raw_archive_file_id',e.payload->>'raw_archive_file_id')
LEFT JOIN school_scraping.school_yearly_snapshots yearly
  ON yearly.source_record_pk=s.source_record_pk AND yearly.academic_year=e.payload->>'academic_year'
LEFT JOIN school_scraping.json_file_archive yearly_archive ON yearly_archive.file_id=yearly.source_archive_file_id
LEFT JOIN school_scraping.scrape_checkpoints cp
  ON cp.source=e.source AND cp.school_identifier=e.payload->>'school_identifier'
  AND cp.academic_year=COALESCE(e.payload->>'academic_year','') AND cp.scope_key=''
WHERE e.kind='import_completed' AND (%s::text IS NULL OR e.source=%s)
  AND (%s::text IS NULL OR e.run_id=%s)
ORDER BY e.occurred_at DESC,e.event_id DESC LIMIT %s
"""

_CHECKPOINTS = """
SELECT jsonb_build_object('source',cp.source,'identifier',cp.school_identifier,
    'academic_year',cp.academic_year,
    'consistent', COALESCE(cp.imported AND cp.checkpoint_status='completed'
        AND s.source_record_pk IS NOT NULL AND master.entity_id IS NOT NULL
        AND canonical.payload->'metadata'->>'schema_version'='4.0'
        AND canonical.payload->'metadata'->>'record_id'=s.source_record_id
        AND ((cp.source='saras' AND canonical.payload->'board_affiliation'->>'affiliation_number'=cp.school_identifier)
          OR (cp.source='udise' AND canonical.payload->'metadata'->>'udise_code'=cp.school_identifier
              AND yearly.snapshot_id IS NOT NULL AND yearly.schema_version='4.0'
              AND yearly.snapshot_payload->>'schema_version'='4.0'
              AND yearly.snapshot_payload->>'record_id'=s.source_record_id
              AND yearly.snapshot_payload->>'udise_code'=s.udise_code
              AND yearly.snapshot_payload->>'academic_year'=cp.academic_year
              AND yearly.source='UDISE+' AND yearly.snapshot_payload->>'source'=yearly.source
              AND yearly_archive.payload @> jsonb_build_array(yearly.snapshot_payload))),false))
FROM school_scraping.scrape_checkpoints cp
LEFT JOIN school_scraping.school_source_records s ON s.source_system=cp.source
  AND ((cp.source='saras' AND s.cbse_affiliation_number=cp.school_identifier)
    OR (cp.source='udise' AND s.udise_code=cp.school_identifier))
LEFT JOIN school_scraping.school_entities master ON master.entity_id=s.entity_id
LEFT JOIN school_scraping.json_file_archive canonical ON canonical.file_id=s.archive_file_id
LEFT JOIN school_scraping.school_yearly_snapshots yearly
  ON yearly.source_record_pk=s.source_record_pk AND yearly.academic_year=cp.academic_year
LEFT JOIN school_scraping.json_file_archive yearly_archive ON yearly_archive.file_id=yearly.source_archive_file_id
WHERE cp.scope_key='' AND cp.school_identifier<>'' AND (cp.imported OR cp.checkpoint_status='completed')
  AND (%s::text IS NULL OR cp.source=%s)
  AND (%s::text IS NULL OR cp.payload->>'run_id'=%s)
ORDER BY cp.source,cp.academic_year,cp.school_identifier LIMIT %s
"""


def _identity(row):
    """Return identifiers only; school payloads and raw errors never leave SQL."""
    source = row.get('source')
    identifier = str(row.get('identifier') or '')
    year = str(row.get('academic_year') or '')
    return {'source': source if source in ('saras', 'udise') else 'invalid',
            'school_identifier': identifier if re.fullmatch(r'[0-9]{1,32}', identifier) else 'invalid',
            'academic_year': year if not year or re.fullmatch(r'[0-9]{4}-[0-9]{2}', year) else 'invalid'}


def reconcile_imports(connection, *, source=None, run_id=None, limit=1000):
    """Inspect bounded completion/ACK and checkpoint evidence without any repair.

    A PASS covers the selected completed events and imported/completed checkpoint
    claims, not every historical legacy import. Truncated scans always BLOCK.
    Active caller transactions are refused rather than committed or rolled back.
    """
    if source not in (None, 'saras', 'udise'):
        raise ValueError('Unknown reconciliation source')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
        raise ValueError('Reconciliation limit must be between 1 and 10000')
    if run_id is not None and (not isinstance(run_id, str) or not run_id or len(run_id) > 200):
        raise ValueError('Invalid reconciliation run identifier')
    report = {'status': 'BLOCKED', 'read_only': True, 'mutations': 0,
              'scope': 'completed_import_events_and_claimed_imported_checkpoints',
              'counts': {'completions_scanned': 0, 'committed_imports_verified': 0,
                         'missing_acknowledgements': 0, 'duplicate_acknowledgements': 0,
                         'inconsistent_imports': 0, 'inconsistent_checkpoints': 0,
                         'checkpoint_claims_scanned': 0},
              'findings': [], 'errors': [], 'truncated': False}
    if connection.info.transaction_status != TransactionStatus.IDLE:
        report['errors'].append('idle_connection_required')
        return report
    try:
        with connection.transaction():
            connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            if connection.execute("SELECT current_setting('transaction_read_only')").fetchone()[0] != 'on':
                raise RuntimeError('Read-only transaction unavailable')
            parameters = (source, source, run_id, run_id, limit + 1)
            completed = [row[0] for row in connection.execute(_COMPLETIONS, parameters).fetchall()]
            checkpoints = [row[0] for row in connection.execute(_CHECKPOINTS, parameters).fetchall()]
        report['truncated'] = len(completed) > limit or len(checkpoints) > limit
        inconsistent_checkpoints = set()
        for row in completed[:limit]:
            report['counts']['completions_scanned'] += 1
            identity = _identity(row)
            def finding(code):
                report['findings'].append({'code': code, **identity})
            if row.get('committed') is True:
                report['counts']['committed_imports_verified'] += 1
            else:
                report['counts']['inconsistent_imports'] += 1
                finding('committed_data_inconsistent')
            if row.get('ack_count') == 0:
                report['counts']['missing_acknowledgements'] += 1
                finding('missing_acknowledgement')
            elif row.get('ack_count') != 1:
                report['counts']['duplicate_acknowledgements'] += 1
                finding('acknowledgement_count_inconsistent')
            if row.get('latest_observation') is True and row.get('checkpoint_consistent') is not True:
                inconsistent_checkpoints.add(tuple(identity.values()))
        for row in checkpoints[:limit]:
            report['counts']['checkpoint_claims_scanned'] += 1
            if row.get('consistent') is not True:
                inconsistent_checkpoints.add(tuple(_identity(row).values()))
        for source_name, identifier, year in sorted(inconsistent_checkpoints):
            report['findings'].append({'code': 'checkpoint_inconsistent', 'source': source_name,
                                      'school_identifier': identifier, 'academic_year': year})
        report['counts']['inconsistent_checkpoints'] = len(inconsistent_checkpoints)
        if report['truncated']:
            report['errors'].append('scan_limit_reached')
        if not report['findings'] and not report['errors']:
            report['status'] = 'PASS'
    except Exception:
        # Do not include server exceptions, connection strings or school JSON.
        report['errors'].append('database_read_failed')
    return report
