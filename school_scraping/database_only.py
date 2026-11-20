"""Database-only access through the existing transactional archive/loader pipeline.

There is deliberately no local journal, payload spool or offline fallback here.
Each store owns one connection. Completed data and completion evidence commit
together; a separate acknowledgement is persisted only after that commit returns.
"""
import copy
import hashlib
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from database import scrape_pipeline as pipeline
from school_scraping.datasets import academic_year as validate_year
from school_scraping.memory import MemoryStore, sanitize


class DatabaseOnlyError(RuntimeError):
    """Fatal storage error: stop collection; do not create an offline spool."""
    def __init__(self, message, *, data_committed=False, commit_outcome_unknown=False):
        super().__init__(message)
        self.data_committed = data_committed
        self.commit_outcome_unknown = commit_outcome_unknown


def validate_identifier(source, identifier):
    if source not in {"saras", "udise"}:
        raise ValueError("Unknown school source")
    if isinstance(identifier, bool) or identifier is None:
        raise ValueError("Invalid listing identifier")
    value = str(identifier)
    pattern = r"[0-9]{11}" if source == "udise" else r"[0-9]+"
    if not re.fullmatch(pattern, value) or not any(ch != "0" for ch in value):
        raise ValueError("Invalid listing identifier")
    return value


def _environment_connection():
    import psycopg
    from dotenv import load_dotenv
    load_dotenv()
    required = ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    if any(not os.getenv(key) for key in required):
        raise DatabaseOnlyError("Database-only mode requires explicit DB_HOST/PORT/NAME/USER/PASSWORD")
    try:
        port = int(os.environ["DB_PORT"])
    except ValueError as error:
        raise DatabaseOnlyError("Database-only mode requires a valid explicit DB_PORT") from error
    if not 1 <= port <= 65535:
        raise DatabaseOnlyError("Database-only mode requires a valid explicit DB_PORT")
    settings = dict(host=os.environ["DB_HOST"], port=port, dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"], connect_timeout=5,
        autocommit=True, application_name="school_scraping_database_only")
    configured = {}
    for environment, setting in (("DB_CONNECT_TIMEOUT_SECONDS", "connect_timeout"),
            ("DB_STATEMENT_TIMEOUT_MS", "statement_timeout"), ("DB_LOCK_TIMEOUT_MS", "lock_timeout")):
        if environment in os.environ:
            try:
                value = int(os.environ[environment])
                if value <= 0:
                    raise ValueError()
            except ValueError:
                raise DatabaseOnlyError("Database timeout settings must be positive integers") from None
            configured[setting] = value
    settings["connect_timeout"] = configured.pop("connect_timeout", 5)
    if configured:
        settings["options"] = " ".join("-c " + name + "=" + str(value) for name, value in configured.items())
    if "DB_SSLMODE" in os.environ:
        if os.environ["DB_SSLMODE"] not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise DatabaseOnlyError("Invalid database TLS mode")
        settings["sslmode"] = os.environ["DB_SSLMODE"]
    if os.getenv("DB_SSLROOTCERT"):
        settings["sslrootcert"] = os.environ["DB_SSLROOTCERT"]
    return psycopg.connect(**settings)


class DatabaseOnlyStore:
    BATCH_SIZE = 500

    def __init__(self, source, academic_year="", connect=None):
        if source not in {"saras", "udise"}:
            raise ValueError("Unknown school source")
        self.source = source
        self.academic_year = str(academic_year or "") if source == "udise" else ""
        if source == "udise":
            validate_year(self.academic_year)
        self.run_id = str(uuid.uuid4())
        self._factory = connect or _environment_connection
        self._connection = None
        self._mutex = threading.RLock()
        self._memory = MemoryStore(Path("."), database_enabled=False)
        self.failed = False
        self.closed = False
        self.stats = {"identifiers_checked": 0, "database_lookup_duration_seconds": 0.0,
            "imports_committed": 0, "canonical_validated": 0, "import_acknowledgements": 0,
            "concurrent_existing_skips": 0}

    def _conn(self):
        if self.closed:
            raise DatabaseOnlyError("Database-only store has been closed")
        if self.failed:
            raise DatabaseOnlyError("Database-only collection has stopped after a storage failure")
        if self._connection is None:
            try:
                connection = self._factory()
                # The service owns its connection and must never join a caller's
                # existing transaction, which could defer a claimed commit.
                connection.autocommit = True
                self._connection = connection
            except Exception as error:
                self.failed = True
                raise DatabaseOnlyError("Database-only connection failed; collection stopped") from error
        return self._connection

    def _fatal(self, operation, error, *, data_committed=False, uncertain=False):
        self.failed = True
        if self._connection is not None:
            try:
                self._connection.rollback()
            except Exception:
                pass
        return DatabaseOnlyError(f"Database-only {operation} failed; collection stopped ({type(error).__name__})",
            data_committed=data_committed, commit_outcome_unknown=uncertain)

    def _lookup(self, connection, identifiers):
        if self.source == "saras":
            statement = """SELECT s.cbse_affiliation_number
                FROM school_scraping.school_source_records s
                JOIN school_scraping.school_entities e ON e.entity_id=s.entity_id
                JOIN school_scraping.json_file_archive a ON a.file_id=s.archive_file_id
                WHERE s.source_system='saras' AND s.cbse_affiliation_number=ANY(%s)
                  AND a.payload->'metadata'->>'schema_version'='4.0'
                  AND a.payload->'metadata'->>'record_id'=s.source_record_id
                  AND a.payload->'board_affiliation'->>'affiliation_number'=s.cbse_affiliation_number"""
            parameters = (identifiers,)
        else:
            statement = """SELECT s.udise_code
                FROM school_scraping.school_source_records s
                JOIN school_scraping.school_entities e ON e.entity_id=s.entity_id
                JOIN school_scraping.json_file_archive c ON c.file_id=s.archive_file_id
                JOIN school_scraping.school_yearly_snapshots y ON y.source_record_pk=s.source_record_pk
                JOIN school_scraping.json_file_archive a ON a.file_id=y.source_archive_file_id
                WHERE s.source_system='udise' AND s.udise_code=ANY(%s) AND y.academic_year=%s
                  AND y.schema_version='4.0' AND c.payload->'metadata'->>'schema_version'='4.0'
                  AND c.payload->'metadata'->>'record_id'=s.source_record_id
                  AND c.payload->'metadata'->>'udise_code'=s.udise_code
                  AND y.snapshot_payload->>'academic_year'=y.academic_year
                  AND y.snapshot_payload->>'udise_code'=s.udise_code
                  AND a.payload @> jsonb_build_array(y.snapshot_payload)"""
            parameters = (identifiers, self.academic_year)
        with connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return {str(row[0]) for row in cursor.fetchall()}

    def lookup_committed(self, identifiers):
        values = list(dict.fromkeys(validate_identifier(self.source, value) for value in identifiers))
        if not values:
            return set()
        with self._mutex:
            started = time.monotonic()
            try:
                connection = self._conn()
                result = set()
                with connection.transaction():
                    connection.execute("SET TRANSACTION READ ONLY")
                    for offset in range(0, len(values), self.BATCH_SIZE):
                        result.update(self._lookup(connection, values[offset:offset + self.BATCH_SIZE]))
                self.stats["identifiers_checked"] += len(values)
                return result
            except DatabaseOnlyError:
                raise
            except Exception as error:
                raise self._fatal("lookup", error) from error
            finally:
                self.stats["database_lookup_duration_seconds"] += time.monotonic() - started

    def _metadata(self, payload):
        if "raw_record" in payload:
            raise ValueError("Raw school data belongs in archives, not operational events")
        result = copy.deepcopy(payload)
        if result.get("source", self.source) != self.source:
            raise ValueError("Event source disagrees with database-only store")
        result.update(source=self.source, academic_year=self.academic_year, storage_mode="database")
        result.setdefault("run_id", self.run_id)
        # Legacy filesystem references cannot accidentally enter this mode.
        for key in ("raw_path", "snapshot_path", "data_root"):
            result.pop(key, None)
        return sanitize(result)

    def _event(self, kind, payload, event_id=None):
        return {"event_id": str(event_id or uuid.uuid4()), "kind": kind,
            "occurred_at": datetime.now(timezone.utc).isoformat(), "payload": self._metadata(payload)}

    def event(self, kind, payload):
        if kind == "import_requested":
            raise ValueError("Use import_record for database-only school imports")
        with self._mutex:
            if self.failed:
                return {"synced": False, "acknowledged": False, "stopped": True}
            event = self._event(kind, payload)
            try:
                with self._conn().transaction():
                    self._memory._apply_db(self._connection, event)
                return {"event_id": event["event_id"], "synced": True, "acknowledged": True}
            except DatabaseOnlyError:
                raise
            except Exception as error:
                raise self._fatal("event commit", error, uncertain=True) from error

    def import_record(self, identifier, raw_record, region, *, scope_key="", run_id=None,
                      observed_at=None, new_only=False):
        # Lazy import keeps the collection-runtime adapter free to import this store.
        from school_scraping.incremental import content_hash, task_key
        identifier = validate_identifier(self.source, identifier)
        raw = copy.deepcopy(raw_record)
        content = pipeline.json_bytes(raw)
        checksum = hashlib.sha256(content).hexdigest()
        observed_at = observed_at or datetime.now(timezone.utc)
        if isinstance(observed_at, str):
            observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        observed_at = observed_at.replace(tzinfo=observed_at.tzinfo or timezone.utc).astimezone(timezone.utc)
        logical = pipeline.database_raw_path(self.source, identifier, region, self.academic_year or None)
        payload = {"run_id": run_id or self.run_id, "source": self.source, "school_identifier": identifier,
            "academic_year": self.academic_year, "scope_key": scope_key, "phase": "import",
            "task_id": task_key(self.source, identifier, self.academic_year, scope_key, "import"),
            "storage_mode": "database", "logical_raw_path": logical, "region": region,
            "raw_sha256": checksum, "content_hash": content_hash(self.source, raw),
            "last_detail_check": observed_at.isoformat()}
        event = self._event("import_requested", payload)
        results = {}

        def direct_import(connection, metadata):
            results.update(pipeline.import_scraped_record(connection, {**metadata, "raw_record": raw}))

        with self._mutex:
            committed = False
            try:
                connection = self._conn()
                with connection.transaction():
                    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (f"scrape-pipeline:{self.source}:{identifier}",))
                    if new_only and identifier in self._lookup(connection, [identifier]):
                        self.stats["concurrent_existing_skips"] += 1
                        return {"status": "already_committed", "skipped": True, "acknowledged": False,
                            "source": self.source, "school_identifier": identifier}
                    self._memory.import_handler = direct_import
                    self._memory._apply_db(connection, event)
                    completion = self._event("import_completed", {**payload, **results, "import_event_id": event["event_id"]})
                    self._memory._apply_db(connection, completion)
                    detail_completed = self._event("task_completed", {**payload,
                        "task_id": task_key(self.source, identifier, self.academic_year, scope_key),
                        "phase": "detail", "task_status": "completed", "completed_at": observed_at.isoformat()})
                    self._memory._apply_db(connection, detail_completed)
                committed = True
                self.stats["imports_committed"] += 1
                self.stats["canonical_validated"] += 1
                # This write is deliberately AFTER the data transaction commits.
                acknowledgement = self._event("import_acknowledged", {**payload, **results,
                    "import_event_id": event["event_id"], "completion_event_id": completion["event_id"],
                    "acknowledged_at": datetime.now(timezone.utc).isoformat()})
                with connection.transaction():
                    self._memory._apply_db(connection, acknowledgement)
                self.stats["import_acknowledgements"] += 1
                return {**results, "acknowledged": True, "import_event_id": event["event_id"],
                    "acknowledgement_event_id": acknowledgement["event_id"], "raw_sha256": checksum}
            except (pipeline.PipelineValidationError, ValueError, TypeError):
                if committed:
                    # Unexpected encoding/projection failure after commit is not
                    # a school-validation rollback and must stop collection.
                    error = DatabaseOnlyError("Data committed but acknowledgement failed", data_committed=True)
                    self.failed = True
                    raise error
                raise
            except DatabaseOnlyError:
                raise
            except Exception as error:
                raise self._fatal("import acknowledgement" if committed else "import transaction", error,
                    data_committed=committed, uncertain=True) from error
            finally:
                self._memory.import_handler = None

    def checkpoint(self, identifier="", scope_key=""):
        if identifier:
            identifier = validate_identifier(self.source, identifier)
        with self._mutex:
            try:
                with self._conn().transaction():
                    self._connection.execute("SET TRANSACTION READ ONLY")
                    with self._connection.cursor() as cursor:
                        cursor.execute("SELECT payload FROM school_scraping.scrape_checkpoints WHERE source=%s AND academic_year=%s "
                            "AND school_identifier=%s AND scope_key=%s", (self.source, self.academic_year, str(identifier), "" if identifier else scope_key))
                        row = cursor.fetchone()
                return copy.deepcopy(row[0]) if row else None
            except DatabaseOnlyError:
                raise
            except Exception as error:
                raise self._fatal("checkpoint read", error) from error

    def tasks(self, scope_key=None):
        with self._mutex:
            try:
                with self._conn().transaction():
                    self._connection.execute("SET TRANSACTION READ ONLY")
                    with self._connection.cursor() as cursor:
                        statement = "SELECT payload FROM school_scraping.scrape_tasks WHERE source=%s AND academic_year=%s"
                        parameters = [self.source, self.academic_year]
                        if scope_key is not None:
                            statement += " AND scope_key=%s"
                            parameters.append(scope_key)
                        cursor.execute(statement, parameters)
                        rows = cursor.fetchall()
                return [copy.deepcopy(row[0]) for row in rows]
            except DatabaseOnlyError:
                raise
            except Exception as error:
                raise self._fatal("task read", error) from error

    def close(self):
        with self._mutex:
            self.closed = True
            if self._connection is not None:
                self._connection.close()
                self._connection = None
