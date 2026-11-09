"""Durable scraping journal with transactional, idempotent PostgreSQL replay.

Imports have no filesystem or database side effects. Journals are append-only;
acknowledgements are written only after PostgreSQL commits. Raw JSON remains in
the existing data folders and is never embedded in operational event payloads.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from school_scraping.common import SourceLock, atomic_json

LOGGER = logging.getLogger("school_scraping")
SECRET_KEY = re.compile(r"password|passwd|secret|token|authorization|cookie|api.?key|credential|dsn|connection.?string|captcha|headers", re.I)
TASK_KINDS = {"task_discovered", "task_started", "task_failed", "task_completed", "raw_saved", "import_requested", "detail_filtered"}


def _safe_url(match):
    try:
        parsed = urlsplit(match.group(0))
        authority = parsed.netloc.rsplit("@", 1)[-1]
        query = [(key, "[REDACTED]" if SECRET_KEY.search(key) or "captcha" in key.lower() else value)
                 for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
        return urlunsplit((parsed.scheme, authority, parsed.path, urlencode(query), parsed.fragment))
    except ValueError:
        return "[REDACTED URL]"


def _safe_text(value):
    text = str(value)
    text = re.sub(r"(?:https?|postgres(?:ql)?)://[^\s'\"<>]+", _safe_url, text, flags=re.I)
    text = re.sub(r"\b(Bearer|Basic)\s+[A-Za-z0-9+/_.=-]+", r"\1 [REDACTED]", text, flags=re.I)
    text = re.sub(r"(?i)(['\"]?\b(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)\b['\"]?\s*[=:]\s*)"
                  r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", r"\1[REDACTED]", text)
    return text


def sanitize(value):
    """Recursively redact credential fields and credentials embedded in URLs/errors."""
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if SECRET_KEY.search(str(key)) else sanitize(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item) for item in value]
    if isinstance(value, Path):
        return _safe_text(value)
    if isinstance(value, str):
        return _safe_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _safe_text(value)


def safe_error(error):
    return _safe_text(f"{type(error).__name__}: {error}")


def sanitize_error(error):
    return {"error_category": type(error).__name__, "error_message": safe_error(error),
            "sanitized_traceback": _safe_text("".join(traceback.format_exception(type(error), error, error.__traceback__)))}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _checkpoint_key(payload):
    identifier = str(payload.get("school_identifier") or "")
    return (str(payload.get("source") or "unknown"), str(payload.get("academic_year") or ""),
            identifier, "" if identifier else str(payload.get("scope_key") or ""))


def _task_id(payload):
    if payload.get("task_id"):
        return str(payload["task_id"])
    return hashlib.sha256(json.dumps(_checkpoint_key(payload), separators=(",", ":")).encode()).hexdigest()


def _newer(current, event):
    return not current or (event["occurred_at"], event["event_id"]) >= (
        current.get("last_event_at", ""), current.get("last_event_id", ""))


def _project(state, event, imported=False):
    payload, kind, stamp = event["payload"], event["kind"], event["occurred_at"]
    shared = {"last_event_at": stamp, "last_event_id": event["event_id"]}
    run_id = payload.get("run_id")
    if run_id and kind in {"run_started", "run_finished"}:
        previous = state["runs"].get(str(run_id), {})
        if _newer(previous, event):
            run = {**previous, **payload, **shared, "run_id": str(run_id)}
            run.setdefault("started_at", stamp)
            run["status"] = payload.get("status") or ("running" if kind == "run_started" else "completed")
            if kind == "run_finished":
                run["finished_at"] = stamp
            state["runs"][str(run_id)] = run
    if kind in TASK_KINDS:
        task_id = _task_id(payload)
        previous = state["tasks"].get(task_id, {})
        matching_completion = (kind == "import_requested" and imported
                               and previous.get("status") == "pending_import"
                               and (previous.get("content_hash") or previous.get("raw_sha256"))
                               == (payload.get("content_hash") or payload.get("raw_sha256")))
        if _newer(previous, event) or matching_completion:
            task = ({**previous, **payload, **shared, "task_id": task_id}
                    if _newer(previous, event) else {**previous})
            defaults = {"task_discovered": "pending", "task_started": "running", "task_failed": "failed",
                        "task_completed": "completed", "raw_saved": "pending_import", "import_requested": "pending_import", "detail_filtered": "filtered"}
            task["status"] = payload.get("task_status") or payload.get("status") or defaults[kind]
            if kind == "task_discovered" and previous:
                task["status"] = previous.get("task_status") or previous.get("status") or task["status"]
            previous_attempts = int(previous.get("attempts") or previous.get("attempt_count") or 0)
            explicit_attempts = payload.get("attempt_count", payload.get("attempts"))
            task["attempts"] = max(previous_attempts, int(explicit_attempts)) if explicit_attempts is not None else previous_attempts + int(kind == "task_started")
            if kind == "import_requested" and imported:
                task["status"] = "completed"
                task["completed_at"] = imported if isinstance(imported, str) else _now()
            elif kind == "task_started":
                task["last_attempt_at"] = payload.get("last_attempt_at") or stamp
            elif kind == "task_completed":
                task["completed_at"] = payload.get("completed_at") or stamp
            previous_checkpoint = state["checkpoints"].get(_checkpoint_key(payload), {})
            if (kind == "raw_saved" and previous_checkpoint.get("imported")
                    and previous_checkpoint.get("last_known_content_hash")
                    == (payload.get("content_hash") or payload.get("raw_sha256"))):
                task["status"] = "completed"
            task["task_status"], task["attempt_count"] = task["status"], task["attempts"]
            state["tasks"][task_id] = task
        if kind == "import_requested" and imported:
            for existing in state["tasks"].values():
                same_entity = _checkpoint_key(existing) == _checkpoint_key(payload)
                same_hash = (existing.get("content_hash") or existing.get("raw_sha256")) in (
                    None, payload.get("content_hash") or payload.get("raw_sha256"))
                if same_entity and same_hash and existing.get("status") == "pending_import":
                    existing["status"] = existing["task_status"] = "completed"
                    existing["completed_at"] = imported if isinstance(imported, str) else _now()
    if kind in {"raw_saved", "import_requested", "scope_checked", "detail_filtered"}:
        key = _checkpoint_key(payload)
        previous = state["checkpoints"].get(key, {})
        matching_completion = (kind == "import_requested" and imported and previous.get("pending_content_hash")
                               == (payload.get("content_hash") or payload.get("raw_sha256")))
        if _newer(previous, event) or matching_completion:
            checkpoint = ({**previous, **payload, **shared} if _newer(previous, event) else {**previous})
            checkpoint.update(zip(("source", "academic_year", "school_identifier", "scope_key"), key))
            if kind == "scope_checked":
                checkpoint["checkpoint_status"] = "completed" if payload.get("complete") else "incomplete"
                checkpoint["complete"] = bool(payload.get("complete"))
                if payload.get("complete"):
                    checkpoint["last_successful_check"] = payload.get("last_successful_check") or stamp
            elif kind == "detail_filtered":
                checkpoint["checkpoint_status"] = "filtered"
                checkpoint["last_detail_check"] = payload.get("last_detail_check") or stamp
                checkpoint["imported"] = bool(previous.get("imported") and previous.get("last_known_content_hash") == payload.get("content_hash"))
            else:
                current_hash = payload.get("content_hash") or payload.get("raw_sha256")
                already_imported = bool(previous.get("imported") and previous.get("last_known_content_hash") == current_hash)
                checkpoint["checkpoint_status"] = "completed" if kind == "raw_saved" and already_imported else "pending_import"
                checkpoint["imported"] = bool(kind == "raw_saved" and already_imported)
                if kind == "raw_saved":
                    checkpoint["last_detail_check"] = payload.get("last_detail_check") or stamp
                checkpoint["pending_content_hash"] = None if kind == "raw_saved" and already_imported else current_hash
                if kind == "import_requested" and imported:
                    checkpoint.update(checkpoint_status="completed", imported=True,
                                      last_successful_check=checkpoint.get("last_detail_check") or payload.get("last_detail_check") or stamp,
                                      last_successful_save=imported if isinstance(imported, str) else _now(),
                                      last_known_content_hash=current_hash, pending_content_hash=None)
            state["checkpoints"][key] = checkpoint


def _empty_state():
    return {"runs": {}, "tasks": {}, "checkpoints": {}}


class MemoryStore:
    def __init__(self, state_dir, database_enabled=False, connect=None, import_handler=None, retry_seconds=60):
        self.state_dir = Path(state_dir)
        self.database_enabled = bool(database_enabled)
        self.connect = connect
        self.import_handler = import_handler
        self.retry_seconds = max(0, float(retry_seconds))
        self.journal = self.state_dir / "events.jsonl"
        self.ack_file = self.state_dir / "acknowledged.json"
        self.retry_file = self.state_dir / "database_retry.json"
        self.warnings = []
        self._event_cache = []
        self._event_by_id = {}
        self._journal_identity = None
        self._journal_offset = 0
        self._journal_mtime = None
        self._ack_cache = {}
        self._ack_identity = None
        self._new_ack_ids = set()
        self._projection = _empty_state()
        self._projected_count = 0
        self._invalidate_projection = True
        self._remote_state = _empty_state()
        self._bootstrapped = set()
        self._bootstrap_retry = {}

    @contextmanager
    def _lock(self, name, wait=10):
        deadline = time.monotonic() + wait
        lock = SourceLock(self.state_dir, name)
        while True:
            try:
                lock.__enter__()
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        try:
            yield
        finally:
            lock.__exit__(None, None, None)

    def _events(self):
        if not self.journal.exists():
            return self._event_cache
        stat = self.journal.stat()
        identity = (stat.st_dev, stat.st_ino)
        if (identity != self._journal_identity or stat.st_size < self._journal_offset
                or (stat.st_size == self._journal_offset and self._journal_mtime != stat.st_mtime_ns)):
            self._event_cache, self._event_by_id = [], {}
            self._journal_offset = 0
            self._invalidate_projection = True
        self._journal_identity = identity
        with self.journal.open("rb") as stream:
            stream.seek(self._journal_offset)
            while True:
                position = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    warning = f"Preserved incomplete journal tail at byte {position}; later events remain recoverable"
                    if warning not in self.warnings:
                        self.warnings.append(warning)
                        LOGGER.warning(warning)
                    break
                self._journal_offset = stream.tell()
                if not line.strip():
                    continue
                try:
                    event = json.loads(line.decode("utf-8"))
                    if not all(key in event for key in ("event_id", "kind", "payload", "occurred_at")):
                        raise ValueError("Invalid event envelope")
                except (ValueError, TypeError, UnicodeError):
                    warning = f"Preserved unreadable journal data at byte {position}; inspect the local journal before removing anything"
                    if warning not in self.warnings:
                        self.warnings.append(warning)
                        LOGGER.warning(warning)
                    continue
                if event["event_id"] not in self._event_by_id:
                    if self._event_cache and event["occurred_at"] < self._event_cache[-1]["occurred_at"]:
                        self._invalidate_projection = True
                    self._event_cache.append(event)
                    self._event_by_id[event["event_id"]] = event
        if self._invalidate_projection:
            self._event_cache.sort(key=lambda event: (event["occurred_at"], event["event_id"]))
        self._journal_mtime = stat.st_mtime_ns
        return self._event_cache

    def _acks(self):
        if not self.ack_file.exists():
            return self._ack_cache
        try:
            stat = self.ack_file.stat()
            identity = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
            if identity == self._ack_identity:
                return self._ack_cache
            value = json.loads(self.ack_file.read_text(encoding="utf-8"))
            value = value if isinstance(value, dict) else {}
            if not self._ack_cache.keys() <= value.keys():
                self._invalidate_projection = True
            self._new_ack_ids.update(value.keys() - self._ack_cache.keys())
            self._ack_cache, self._ack_identity = value, identity
            return self._ack_cache
        except (OSError, ValueError):
            # PostgreSQL event dedup safely recovers from a missing/corrupt local acknowledgement.
            return {}

    def _state(self):
        events, acks = self._events(), self._acks()
        if self._invalidate_projection:
            self._projection = copy.deepcopy(self._remote_state)
            self._projected_count = 0
            self._invalidate_projection = False
        for event in events[self._projected_count:]:
            ack = acks.get(event["event_id"], {})
            _project(self._projection, event, imported=ack.get("committed_at", False))
        self._projected_count = len(events)
        for identifier in self._new_ack_ids:
            event = self._event_by_id.get(identifier)
            if event and event["kind"] == "import_requested":
                _project(self._projection, event, imported=acks[identifier].get("committed_at", False))
        self._new_ack_ids.clear()
        return self._projection

    def _append(self, kind, payload, event_id=None):
        safe_payload = sanitize(payload)
        if not isinstance(safe_payload, dict):
            raise ValueError("Memory event payload must be an object")
        identifier = str(event_id or uuid.uuid4())
        with self._lock("memory_journal"):
            events = self._events()
            previous = self._event_by_id.get(identifier)
            if previous:
                if previous["kind"] != kind or previous["payload"] != safe_payload:
                    raise ValueError("An event ID cannot be reused with a different payload")
            else:
                stamp = _now()
                if events and stamp <= events[-1]["occurred_at"]:
                    from datetime import timedelta
                    stamp = (datetime.fromisoformat(events[-1]["occurred_at"]) + timedelta(microseconds=1)).isoformat()
                event = {"event_id": identifier, "kind": str(kind), "occurred_at": stamp, "payload": safe_payload}
                self.state_dir.mkdir(parents=True, exist_ok=True)
                encoded = (json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                with self.journal.open("a+b") as stream:
                    stream.seek(0, 2)
                    if stream.tell():
                        stream.seek(-1, 2)
                        if stream.read(1) != b"\n":
                            stream.write(b"\n")
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
        return identifier

    def emit(self, kind, payload, event_id=None):
        identifier = self._append(kind, payload, event_id)
        if self.database_enabled:
            try:
                self._replay(1000, respect_backoff=True)
            except RuntimeError:
                # Another process holds replay; its next pass will include this durable event.
                LOGGER.info("Another replay is active; the new event remains safely queued")
        return {"event_id": identifier, "synced": identifier in self._acks()}

    def checkpoint(self, source, school_identifier="", academic_year="", scope_key=""):
        self.bootstrap(source, academic_year)
        key = _checkpoint_key(dict(source=source, school_identifier=school_identifier,
                                   academic_year=academic_year, scope_key=scope_key))
        return copy.deepcopy(self._state()["checkpoints"].get(key))

    def tasks(self, source, scope_key=None, academic_year=""):
        self.bootstrap(source, academic_year)
        return [copy.deepcopy(task) for task in self._state()["tasks"].values()
                if task.get("source") == source and str(task.get("academic_year") or "") == str(academic_year or "")
                and (scope_key is None or task.get("scope_key", "") == scope_key)]

    def task(self, task_id, source=None, academic_year=""):
        """Read one task without copying the full source task history."""
        if source is not None:
            self.bootstrap(source, academic_year)
        return copy.deepcopy(self._state()["tasks"].get(str(task_id)))

    def bootstrap(self, source, academic_year=""):
        """Read database memory once per source/year; local WAL still works offline."""
        cache_key = (source, str(academic_year or ""))
        if not self.database_enabled or cache_key in self._bootstrapped:
            return {"loaded": 0, "cached": cache_key in self._bootstrapped}
        if time.time() < self._bootstrap_retry.get(cache_key, 0):
            return {"loaded": 0, "skipped_backoff": True}
        if self.retry_file.exists():
            try:
                if time.time() < json.loads(self.retry_file.read_text()).get("next_attempt", 0):
                    return {"loaded": 0, "skipped_backoff": True}
            except (OSError, ValueError, TypeError):
                pass
        connection = None
        try:
            connection = self._connection()
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '5s'")
                    cursor.execute("SELECT 'task',payload FROM school_scraping.scrape_tasks WHERE source=%s AND academic_year=%s "
                                   "UNION ALL SELECT 'checkpoint',payload FROM school_scraping.scrape_checkpoints WHERE source=%s AND academic_year=%s",
                                   (*cache_key, *cache_key))
                    rows = cursor.fetchall()
            for kind, payload in rows:
                if kind == "task":
                    self._remote_state["tasks"][payload["task_id"]] = payload
                else:
                    self._remote_state["checkpoints"][_checkpoint_key(payload)] = payload
            self._bootstrapped.add(cache_key)
            self._invalidate_projection = True
            return {"loaded": len(rows), "cached": False}
        except Exception as error:
            self._bootstrap_retry[cache_key] = time.time() + self.retry_seconds
            LOGGER.warning("Database memory lookup failed (%s); using the durable local journal", type(error).__name__)
            return {"loaded": 0, "failed": True}
        finally:
            if connection is not None:
                connection.close()

    def _connection(self):
        if self.connect:
            return self.connect()
        # Lazy access only when enabled; never log settings or connection exceptions verbatim.
        import psycopg
        from dotenv import load_dotenv
        load_dotenv()
        if any(not os.getenv(key) for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")):
            raise RuntimeError("Database settings are incomplete; scraping events remain queued")
        return psycopg.connect(host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
                               dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
                               password=os.getenv("DB_PASSWORD"), connect_timeout=5)

    def replay(self, limit=1000, respect_backoff=False):
        return self._replay(limit, respect_backoff=respect_backoff)

    def _replay(self, limit, respect_backoff=False):
        if limit < 1:
            raise ValueError("Replay limit must be positive")
        counts = {"synced": 0, "duplicates": 0, "failed": 0, "pending": 0, "skipped_backoff": 0}
        if not self.database_enabled:
            counts["pending"] = sum(event["event_id"] not in self._acks() for event in self._events())
            return counts
        if respect_backoff and self.retry_file.exists():
            try:
                if time.time() < json.loads(self.retry_file.read_text()).get("next_attempt", 0):
                    counts["skipped_backoff"] = 1
                    counts["pending"] = sum(event["event_id"] not in self._acks() for event in self._events())
                    return counts
            except (OSError, ValueError, TypeError):
                pass
        with self._lock("memory_replay", wait=1):
            # Do not mutate the cached acknowledged set before _acks() sees
            # the durable replacement; cached pending checkpoints need that
            # transition to be projected after an old import finally commits.
            acks, events = dict(self._acks()), self._events()
            pending = [event for event in events if event["event_id"] not in acks]
            if not pending:
                return counts
            connection = None
            try:
                connection = self._connection()
                blocked_entities = {}
                for event in pending[:limit]:
                    entity = _checkpoint_key(event["payload"])
                    event_hash = event["payload"].get("content_hash") or event["payload"].get("raw_sha256")
                    if event_hash in blocked_entities.get(entity, set()) and event["kind"] in TASK_KINDS:
                        continue
                    try:
                        inserted = self._apply_db(connection, event)
                        acks[event["event_id"]] = {"committed_at": _now()}
                        atomic_json(self.ack_file, acks)
                        counts["synced"] += 1
                        counts["duplicates"] += int(not inserted)
                    except Exception as error:
                        counts["failed"] += 1
                        # Preserve a rejected historical input for review/retry,
                        # while allowing corrected source content to progress.
                        blocked_entities.setdefault(entity, set()).add(event_hash)
                        atomic_json(self.retry_file, {"next_attempt": time.time() + self.retry_seconds,
                                                     "error": safe_error(error)})
                        LOGGER.warning("Scraping memory transaction failed (%s); journal retained for replay", type(error).__name__)
                        failure_id = event["event_id"] + ":failure:" + type(error).__name__
                        failure_payload = {key: event["payload"][key] for key in
                                           ("run_id", "source", "school_identifier", "academic_year", "scope_key", "task_id")
                                           if key in event["payload"]}
                        failure_payload.update(sanitize_error(error), failed_event_id=event["event_id"])
                        if not any(item["event_id"] == failure_id for item in self._events()):
                            self._append("event", failure_payload, failure_id)
                        if connection.closed or getattr(error, "sqlstate", "") in {"42P01", "42501", "3F000"}:
                            break
            except Exception as error:
                counts["failed"] += 1
                atomic_json(self.retry_file, {"next_attempt": time.time() + self.retry_seconds,
                                             "error": safe_error(error)})
                origin = pending[0]
                failure_id = origin["event_id"] + ":connection_failure:" + type(error).__name__
                if not any(item["event_id"] == failure_id for item in self._events()):
                    failure_payload = {key: origin["payload"][key] for key in
                                       ("run_id", "source", "school_identifier", "academic_year", "scope_key", "task_id")
                                       if key in origin["payload"]}
                    failure_payload.update(sanitize_error(error), failed_event_id=origin["event_id"])
                    self._append("event", failure_payload, failure_id)
                LOGGER.warning("Scraping database is unavailable (%s); journal retained for replay", type(error).__name__)
            finally:
                if connection is not None:
                    connection.close()
            counts["pending"] = sum(event["event_id"] not in acks for event in self._events())
            if not counts["failed"] and counts["synced"]:
                atomic_json(self.retry_file, {"next_attempt": 0, "error": None})
        return counts

    def _apply_db(self, connection, event):
        from psycopg.types.json import Jsonb
        payload, source, run_id = event["payload"], event["payload"].get("source", "unknown"), event["payload"].get("run_id")
        with connection.transaction():
            with connection.cursor() as cursor:
                if run_id:
                    cursor.execute("INSERT INTO school_scraping.scrape_runs(run_id,source,started_at,last_event_at) "
                                   "VALUES (%s,%s,%s,%s) ON CONFLICT(run_id) DO NOTHING",
                                   (run_id, source, event["occurred_at"], event["occurred_at"]))
                cursor.execute("INSERT INTO school_scraping.scrape_events(event_id,run_id,source,kind,occurred_at,payload) "
                               "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(event_id) DO NOTHING RETURNING event_id",
                               (event["event_id"], run_id, source, event["kind"], event["occurred_at"], Jsonb(payload)))
                if cursor.fetchone() is None:
                    return False
                imported = event["kind"] == "import_requested"
                if imported:
                    if self.import_handler is None:
                        raise RuntimeError("No transactional school import handler is configured")
                    self.import_handler(connection, payload)
                state = _empty_state()
                task_id = _task_id(payload)
                if event["kind"] in TASK_KINDS:
                    cursor.execute("SELECT payload FROM school_scraping.scrape_tasks WHERE task_id=%s FOR UPDATE", (task_id,))
                    row = cursor.fetchone()
                    if row:
                        state["tasks"][task_id] = row[0]
                if imported and payload.get("school_identifier"):
                    cursor.execute("SELECT payload FROM school_scraping.scrape_tasks WHERE source=%s "
                                   "AND academic_year=%s AND school_identifier=%s ORDER BY task_id FOR UPDATE",
                                   (source, payload.get("academic_year") or "", payload["school_identifier"]))
                    for row in cursor.fetchall():
                        state["tasks"][row[0]["task_id"]] = row[0]
                key = _checkpoint_key(payload)
                if event["kind"] in {"raw_saved", "import_requested", "scope_checked", "detail_filtered"}:
                    cursor.execute("SELECT payload FROM school_scraping.scrape_checkpoints WHERE source=%s AND academic_year=%s "
                                   "AND school_identifier=%s AND scope_key=%s FOR UPDATE", key)
                    row = cursor.fetchone()
                    if row:
                        state["checkpoints"][key] = row[0]
                if run_id and event["kind"] in {"run_started", "run_finished"}:
                    cursor.execute("SELECT payload FROM school_scraping.scrape_runs WHERE run_id=%s FOR UPDATE", (run_id,))
                    row = cursor.fetchone()
                    if row:
                        state["runs"][run_id] = row[0]
                _project(state, event, _now() if imported else False)
                if run_id in state["runs"]:
                    run = state["runs"][run_id]
                    cursor.execute("UPDATE school_scraping.scrape_runs SET scope_key=%s,status=%s,started_at=%s,finished_at=%s,"
                                   "last_event_at=%s,payload=%s WHERE run_id=%s AND last_event_at<=%s",
                                   (run.get("scope_key", ""), run["status"], run.get("started_at", event["occurred_at"]),
                                    run.get("finished_at"), run["last_event_at"], Jsonb(run), run_id, event["occurred_at"]))
                for projected_task_id, task in state["tasks"].items():
                    cursor.execute("INSERT INTO school_scraping.scrape_tasks(task_id,run_id,source,academic_year,school_identifier,"
                                   "scope_key,status,attempts,next_retry_at,last_event_at,payload) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                   "ON CONFLICT(task_id) DO UPDATE SET run_id=EXCLUDED.run_id,scope_key=EXCLUDED.scope_key,status=EXCLUDED.status,"
                                   "attempts=EXCLUDED.attempts,next_retry_at=EXCLUDED.next_retry_at,last_event_at=EXCLUDED.last_event_at,payload=EXCLUDED.payload "
                                   "WHERE scrape_tasks.last_event_at<=EXCLUDED.last_event_at",
                                   (projected_task_id, task.get("run_id"), source, task.get("academic_year") or "", task.get("school_identifier") or "",
                                    task.get("scope_key") or "", task["status"], task["attempts"], task.get("next_retry_at"), task["last_event_at"], Jsonb(task)))
                if key in state["checkpoints"]:
                    checkpoint = state["checkpoints"][key]
                    cursor.execute("INSERT INTO school_scraping.scrape_checkpoints(source,academic_year,school_identifier,scope_key,checkpoint_status,"
                                   "last_detail_check,last_successful_check,last_successful_save,next_check_at,last_known_content_hash,raw_path,imported,last_event_at,payload) "
                                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source,academic_year,school_identifier,scope_key) "
                                   "DO UPDATE SET checkpoint_status=EXCLUDED.checkpoint_status,last_detail_check=EXCLUDED.last_detail_check,"
                                   "last_successful_check=EXCLUDED.last_successful_check,last_successful_save=EXCLUDED.last_successful_save,"
                                   "next_check_at=EXCLUDED.next_check_at,last_known_content_hash=EXCLUDED.last_known_content_hash,raw_path=EXCLUDED.raw_path,"
                                   "imported=EXCLUDED.imported,last_event_at=EXCLUDED.last_event_at,payload=EXCLUDED.payload "
                                   "WHERE scrape_checkpoints.last_event_at<=EXCLUDED.last_event_at",
                                   (*key, checkpoint["checkpoint_status"], checkpoint.get("last_detail_check"), checkpoint.get("last_successful_check"),
                                    checkpoint.get("last_successful_save"), checkpoint.get("next_check_at"), checkpoint.get("last_known_content_hash"),
                                    checkpoint.get("raw_path"), checkpoint.get("imported", False), checkpoint["last_event_at"], Jsonb(checkpoint)))
                if event["kind"] == "task_failed" or payload.get("error_message"):
                    cursor.execute("SELECT task_id FROM school_scraping.scrape_tasks WHERE task_id=%s", (task_id,))
                    found_task = cursor.fetchone()
                    error_task = found_task[0] if found_task else None
                    already_resolved = False
                    if payload.get("failed_event_id"):
                        cursor.execute("SELECT event_id FROM school_scraping.scrape_events WHERE event_id=%s",
                                       (payload["failed_event_id"],))
                        already_resolved = cursor.fetchone() is not None
                    cursor.execute("INSERT INTO school_scraping.scrape_errors(error_id,run_id,task_id,source,error_category,error_message,sanitized_traceback,occurred_at,payload,retry_count,resolution_status,resolved_at,resolution_notes) "
                                   "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(error_id) DO NOTHING",
                                   (event["event_id"], run_id, error_task, source, payload.get("error_category", "ScrapingError"),
                                    payload.get("error_message", "Task failed"), payload.get("sanitized_traceback"), event["occurred_at"], Jsonb(payload),
                                    int(payload.get("attempt_count") or payload.get("attempts") or 0),
                                    "resolved" if already_resolved else "open", _now() if already_resolved else None,
                                    "Queued transaction succeeded before failure journal replay" if already_resolved else None))
                if event["kind"] == "task_completed" or imported:
                    cursor.execute("UPDATE school_scraping.scrape_errors SET resolution_status='resolved',resolved_at=%s,"
                                   "resolution_notes=%s WHERE (task_id=%s OR (%s AND task_id IN (SELECT task_id FROM "
                                   "school_scraping.scrape_tasks WHERE source=%s AND academic_year=%s AND school_identifier=%s))) "
                                   "AND resolution_status='open' AND (occurred_at<=%s OR payload->>'failed_event_id'=%s) RETURNING error_id",
                                   (_now(), "Task completed after retry or authorized import", task_id, bool(imported and payload.get("school_identifier")),
                                    source, payload.get("academic_year") or "", payload.get("school_identifier") or "",
                                    event["occurred_at"], event["event_id"]))
                    for resolved in cursor.fetchall():
                        cursor.execute("INSERT INTO school_scraping.scrape_events(event_id,run_id,source,kind,occurred_at,payload) "
                                       "VALUES (%s,%s,%s,'error_resolved',%s,%s) ON CONFLICT(event_id) DO NOTHING",
                                       (event["event_id"] + ":resolved:" + resolved[0], run_id, source, _now(),
                                        Jsonb({"error_id": resolved[0], "school_identifier": payload.get("school_identifier")})))
        return True
