"""Per-school integration with the project's existing archive and school tables.

The caller owns the PostgreSQL transaction. This module never connects, commits,
merges entities, or rewrites source files. Immutable snapshot bytes are the event
authority; archive paths use the original logical data/ layout even in a sandbox.
"""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from school_scraping import canonical_v4_mapper as udise_mapper
from school_scraping import saras_canonical_v4_mapper as saras_mapper
from school_scraping import standardize_school_data as standardizer
from school_scraping import validate_canonical_v4 as udise_validator
from school_scraping import validate_saras_canonical_v4 as saras_validator
from database import load_school_tables as loader
from database.import_json_to_postgres import UPSERT_SQL, prepare_json_record
from school_scraping.datasets import academic_year


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_FIELDS = (
    "relative_path", "dataset_layer", "source_system", "region", "academic_year",
    "file_name", "content_sha256", "file_size_bytes", "json_root_type", "raw_json",
    "payload", "file_modified_at",
)


class PipelineValidationError(ValueError):
    """A source record could not pass the established mapping/validation rules."""


class TupleConnection:
    """Keep the existing loaders independent of the caller's row factory."""
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return self.connection.cursor(row_factory=tuple_row)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def history_path(source, relative_path, checksum):
    path_key = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:24]
    return f"data/pipeline_history/{source}/{path_key}/{checksum}.json"


def archive_record(connection, record, source, keep_newer=True):
    """Reuse the archive upsert, preserving the prior row before replacement."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT * FROM school_scraping.json_file_archive WHERE relative_path = %s FOR UPDATE",
                       (record["relative_path"],))
        previous = cursor.fetchone()
        if previous and previous["content_sha256"] != record["content_sha256"]:
            old_path = history_path(source, record["relative_path"], previous["content_sha256"])
            cursor.execute("""
                INSERT INTO school_scraping.json_file_archive
                    (relative_path, dataset_layer, source_system, region, academic_year,
                     file_name, content_sha256, file_size_bytes, json_root_type,
                     raw_json, payload, file_modified_at)
                SELECT %s, 'history', source_system, region, academic_year, %s,
                       content_sha256, file_size_bytes, json_root_type, raw_json, payload, file_modified_at
                FROM school_scraping.json_file_archive WHERE file_id = %s
                ON CONFLICT (relative_path) DO NOTHING
            """, (old_path, Path(old_path).name, previous["file_id"]))
            old_time, event_time = previous["file_modified_at"], record["file_modified_at"]
            if keep_newer and old_time and event_time and old_time > event_time:
                return previous["file_id"]
        values = tuple(Jsonb(record[name]) if name == "payload" else record[name]
                       for name in ARCHIVE_FIELDS)
        cursor.execute(UPSERT_SQL, values)
        cursor.execute("SELECT file_id FROM school_scraping.json_file_archive WHERE relative_path = %s",
                       (record["relative_path"],))
        return cursor.fetchone()["file_id"]


def prepare_archive(path, content, observed_at, source, region, year, layer=None):
    record = prepare_json_record(path, content, observed_at)
    record.update(source_system="UDISE" if source == "udise" else "CBSE_SARAS",
                  region=region, academic_year=year)
    if layer:
        record["dataset_layer"] = layer
    return record


def logical_raw_path(payload, raw_path):
    if payload.get("data_root"):
        try:
            relative = raw_path.relative_to(Path(payload["data_root"]).resolve())
            if relative.parts and not any(part in {".", ".."} for part in relative.parts):
                return (Path("data") / relative).as_posix()
        except ValueError:
            pass
    try:
        return raw_path.relative_to(ROOT).as_posix()
    except ValueError:
        region = standardizer.safe_name(payload.get("region") or "Unknown")
        if payload["source"] == "udise":
            return f"data/{region}/{payload['academic_year']}/{raw_path.name}"
        return f"data/CBSE_SARAS/{region.lower()}/schools/{raw_path.name}"


def map_and_validate(source, raw, raw_relative, region, identifier, observed_at):
    virtual_raw = ROOT / raw_relative
    if source == "udise":
        standardized = standardizer.standardize_udise(raw, virtual_raw, region)
        canonical = udise_mapper.map_udise_record(raw, virtual_raw, region)
        actual = canonical["metadata"].get("udise_code")
        issues = (udise_validator.validate_record_structure(canonical)
                  + udise_validator.validate_mapping(raw, canonical)
                  + udise_validator.validate_address(raw, canonical))
    else:
        standardized = standardizer.standardize_saras(raw, virtual_raw, region)
        canonical = saras_mapper.map_saras_record(raw, virtual_raw, region)
        actual = canonical["board_affiliation"].get("affiliation_number")
        canonical_path = ROOT / f"data/canonical_v4/saras/{region}/schools/{identifier}.json"
        issues = saras_validator.validate_record(raw, canonical, virtual_raw, canonical_path)
    if str(actual or "") != identifier:
        raise PipelineValidationError("Mapped source identifier does not match the immutable event")
    if issues:
        raise PipelineValidationError(f"Existing {source} validation rejected {len(issues)} field checks")
    canonical["metadata"]["last_verified_at"][source] = observed_at.isoformat()
    return standardized, canonical


def normalized_snapshot(snapshot, source_key, archive_id):
    result = {"source_key": source_key, "source_archive_file_id": archive_id,
              "academic_year": snapshot["academic_year"], "schema_version": snapshot["schema_version"],
              "source": snapshot["source"], "snapshot_payload": snapshot}
    for field in ("total_students", "total_teachers"):
        result[field] = loader.integer_value(snapshot.get(field), field, minimum=0)
    for field, precision, scale, maximum in (
        ("student_teacher_ratio", 12, 4, None), ("infra_score", 12, 4, None),
        ("pass_percentage_x", 7, 3, 100), ("pass_percentage_xii", 7, 3, 100),
    ):
        result[field] = loader.decimal_value(snapshot.get(field), field, precision, scale,
                                            minimum=0, maximum=maximum)
    return result


def import_scraped_record(connection, payload):
    """Validate/archive/upsert one saved event inside the caller's transaction.

    Required payload: source, school_identifier, academic_year (UDISE), region,
    raw_path, snapshot_path, raw_sha256. data_root optionally defines the physical
    raw root. Old-year events retain their yearly snapshot without replacing a
    newer master record. Ambiguous existing source identities require review.
    """
    source = payload["source"]
    if source not in {"udise", "saras"}:
        raise ValueError("Unknown source")
    identifier = str(payload["school_identifier"])
    if not identifier.isdigit() or (source == "udise" and len(identifier) != 11):
        raise PipelineValidationError("Invalid source identifier")
    raw_path, snapshot_path = Path(payload["raw_path"]).resolve(), Path(payload["snapshot_path"]).resolve()
    if not raw_path.is_file():
        raise PipelineValidationError("Latest raw file is missing; immutable event remains retryable")
    content = snapshot_path.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != payload["raw_sha256"]:
        raise PipelineValidationError("Immutable snapshot checksum mismatch")
    raw = json.loads(content.decode("utf-8-sig"))
    if not isinstance(raw, dict):
        raise PipelineValidationError("School JSON must be an object")
    checked_at = payload.get("last_detail_check")
    observed_at = (datetime.fromisoformat(str(checked_at).replace("Z", "+00:00")) if checked_at
                   else datetime.fromtimestamp(snapshot_path.stat().st_mtime, tz=timezone.utc))
    observed_at = observed_at.replace(tzinfo=observed_at.tzinfo or timezone.utc).astimezone(timezone.utc)
    year = str(payload.get("academic_year") or "") or None
    if source == "udise":
        academic_year(year)
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("academic_year") != year:
            raise PipelineValidationError("Saved academic year disagrees with the event")
    raw_relative = logical_raw_path(payload, raw_path)
    column = "udise_code" if source == "udise" else "cbse_affiliation_number"
    mapper_id = (udise_mapper.make_record_id(identifier) if source == "udise"
                 else saras_mapper.make_record_id(identifier))
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"scrape-pipeline:{source}:{identifier}",))
        cursor.execute(f"""
            SELECT s.*, a.relative_path AS archive_path, a.file_modified_at AS archive_modified,
                   a.payload AS archived_canonical
            FROM school_scraping.school_source_records s
            JOIN school_scraping.json_file_archive a ON a.file_id = s.archive_file_id
            WHERE s.source_system = %s AND (s.{column} = %s OR s.source_record_id = %s)
            FOR UPDATE OF s
        """, (source, identifier, mapper_id))
        matches = cursor.fetchall()
        if len(matches) > 1:
            raise PipelineValidationError("Multiple existing source identities require manual review")
        previous = matches[0] if matches else None
        if previous and previous["source_record_id"] != mapper_id:
            raise PipelineValidationError("Existing canonical identity differs; manual review required")
        if previous:
            region = previous["region"]
        else:
            parts = Path(raw_relative).parts
            region = parts[1] if source == "udise" else parts[2]
            region = standardizer.safe_name(region).lower()
        latest_year = None
        if previous and source == "udise":
            cursor.execute("""SELECT MAX(academic_year) AS year FROM school_scraping.school_yearly_snapshots
                              WHERE source_record_pk = %s""", (previous["source_record_pk"],))
            latest_year = cursor.fetchone()["year"]
            latest_year = latest_year or (previous["archived_canonical"].get("provenance") or {}).get("academic_year")
    standardized, canonical = map_and_validate(source, raw, raw_relative, region, identifier, observed_at)
    if previous:
        canonical["entity_resolution"] = previous["entity_resolution"]
    older_year = bool(latest_year and year and year < latest_year)
    older_observation = bool(previous and previous["archive_modified"]
                             and observed_at < previous["archive_modified"]
                             and (source == "saras" or year == latest_year))
    preserve_master = older_year or older_observation
    canonical_path = (previous["archive_path"] if previous else
                      f"data/canonical_v4/{source}/{region}/schools/{identifier}.json")
    normalized_path = f"data/standardized/{source}/{region}/schools/{identifier}.json"
    archive_paths = []

    def archive(path, value, layer, immutable=False, keep_newer=True):
        bytes_value = value if isinstance(value, bytes) else json_bytes(value)
        if immutable:
            path = history_path(source, path, hashlib.sha256(bytes_value).hexdigest())
        record = prepare_archive(path, bytes_value, observed_at, source, region, year, layer)
        archive_paths.append(path)
        return archive_record(connection, record, source, keep_newer=keep_newer and not immutable)

    # Archive immutable event data before advancing any logical latest views.
    archive(raw_relative, content, "history", immutable=True)
    raw_archive_id = archive(raw_relative, content, "raw")
    archive(normalized_path, standardized, "history", immutable=True)
    event_canonical_id = archive(canonical_path, canonical, "history", immutable=True)
    if not preserve_master:
        # Academic-year ordering above takes precedence over file observation time.
        archive(normalized_path, standardized, "standardized", keep_newer=False)
        canonical_id = archive(canonical_path, canonical, "canonical", keep_newer=False)
    else:
        canonical_id = previous["archive_file_id"]
    record = loader.parse_canonical_record(canonical_id, canonical_path, canonical)
    source_key = (source, region, mapper_id)
    tuple_connection = TupleConnection(connection)
    if preserve_master:
        source_map = {source_key: previous["source_record_pk"]}
    else:
        source_map = loader.load_source_records(tuple_connection, [record])

    snapshot_updated = False
    if source == "udise":
        snapshot = udise_mapper.build_yearly_snapshot(canonical)
        snapshots_path = f"data/canonical_v4/udise/{region}/_yearly_snapshots.json"
        # A versioned single-record array is the precise FK evidence for this event.
        snapshot_id = archive(snapshots_path + f"/{identifier}/{year}", [snapshot], "history", immutable=True)
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT a.file_modified_at FROM school_scraping.school_yearly_snapshots s
                JOIN school_scraping.json_file_archive a ON a.file_id = s.source_archive_file_id
                WHERE s.source_record_pk = %s AND s.academic_year = %s""",
                (source_map[source_key], year))
            saved_snapshot = cursor.fetchone()
        if not saved_snapshot or not saved_snapshot["file_modified_at"] or saved_snapshot["file_modified_at"] <= observed_at:
            loader.load_yearly_snapshots(tuple_connection,
                [normalized_snapshot(snapshot, source_key, snapshot_id)], source_map)
            snapshot_updated = True
        # Preserve the existing aggregate contract, retaining every already archived year.
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                           ("scrape-pipeline-snapshots:" + region,))
            cursor.execute("SELECT payload FROM school_scraping.json_file_archive WHERE relative_path = %s FOR UPDATE",
                           (snapshots_path,))
            saved_array = cursor.fetchone()
        combined = saved_array["payload"] if saved_array else []
        if not isinstance(combined, list):
            raise PipelineValidationError("Saved yearly snapshot aggregate is not an array")
        if snapshot_updated:
            combined = [item for item in combined if not (
                item.get("record_id") == mapper_id and item.get("academic_year") == year)] + [snapshot]
            archive(snapshots_path, combined, "canonical", keep_newer=False)

    candidates = []
    if not preserve_master:
        with connection.cursor(row_factory=dict_row) as cursor:
            cursor.execute("""SELECT * FROM school_scraping.school_source_records
                WHERE source_system <> %s AND region = %s AND normalized_school_name = %s""",
                (source, region, record["normalized_school_name"]))
            opposite = cursor.fetchall()
        candidates = loader.build_match_candidates([record, *opposite])
        for other in opposite:
            source_map[(other["source_system"], other["region"], other["source_record_id"])] = other["source_record_pk"]
        loader.load_match_candidates(tuple_connection, candidates, source_map)
    with connection.cursor(row_factory=tuple_row) as cursor:
        cursor.execute("SELECT entity_id FROM school_scraping.school_source_records WHERE source_record_pk = %s",
                       (source_map[source_key],))
        entity_id = cursor.fetchone()[0]
    return {"status": "snapshot_only" if preserve_master else "imported", "source": source,
            "school_identifier": identifier, "region": region, "academic_year": year,
            "source_record_pk": source_map[source_key], "entity_id": entity_id,
            "raw_archive_file_id": raw_archive_id, "canonical_archive_file_id": canonical_id,
            "event_canonical_archive_file_id": event_canonical_id,
            "snapshot_updated": snapshot_updated, "match_candidates": len(candidates),
            "preserved_newer_master": preserve_master, "archive_paths": archive_paths}
