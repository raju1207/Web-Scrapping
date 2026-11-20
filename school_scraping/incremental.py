"""Collection lifecycle shared by the two source entry points.

Listing checks never extend the lifetime of a saved detail record. Only a detail
fetch can do that; PostgreSQL import completion is tracked separately.
"""
import hashlib
import json
import logging
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from school_scraping.common import ManualIntervention, atomic_json, read_json
from school_scraping.config import scope_key

LOGGER = logging.getLogger("school_scraping")


def utcnow():
    return datetime.now(timezone.utc)


def timestamp(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
            tzinfo=datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo or timezone.utc).astimezone(timezone.utc)
    return None


def content_hash(source, record):
    keys = (("school_summary", "school_profile", "student_teacher_statistics",
             "infrastructure_facilities", "report_card", "school_history")
            if source == "udise" else ("school_details", "links"))
    value = {key: record.get(key) for key in keys}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def task_key(source, identifier, year="", scope="", phase="detail"):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
        [source, year or "", str(identifier), scope if not identifier else "", phase], separators=(",", ":"))))


class IncrementalRuntime:
    def __init__(self, args, source, memory=None, clock=utcnow):
        self.args, self.source, self.clock = args, source, clock
        self.year = getattr(args, "academic_year", "") if source == "udise" else ""
        self.scope = getattr(args, "scope_key", None) or scope_key(
            source, getattr(args, "state", []), getattr(args, "district", []),
            getattr(args, "pincode", []), getattr(args, "all_states", False))
        self.state_dir = Path(getattr(args, "state_dir", None) or (Path(args.data_dir) / ".scraping_state"))
        self.database = bool(getattr(args, "database", False))
        self.refresh_days = getattr(args, "refresh_days", None)
        self.refresh_days = 30 if self.refresh_days is None else self.refresh_days
        self.check_hours = getattr(args, "check_interval_hours", None)
        self.check_hours = 24 if self.check_hours is None else self.check_hours
        self.retry_minutes = getattr(args, "retry_delay_minutes", None)
        self.retry_minutes = 60 if self.retry_minutes is None else self.retry_minutes
        self.max_attempts = getattr(args, "max_task_attempts", None) or 5
        self.run_id = str(uuid.uuid4())
        self.started_at = None
        self.finished = False
        self.imported = 0
        self.pending_imports = 0
        self.replay_summary = None
        self._tasks = {}
        if memory is None:
            from school_scraping.memory import MemoryStore
            from database.scrape_pipeline import import_scraped_record
            memory = MemoryStore(self.state_dir, database_enabled=self.database,
                                 import_handler=import_scraped_record,
                                 retry_seconds=max(1, self.retry_minutes * 60))
        self.memory = memory

    def _payload(self, identifier="", phase="detail", **fields):
        return {"run_id": self.run_id, "source": self.source, "scope_key": self.scope,
                "academic_year": self.year, "school_identifier": str(identifier),
                "task_id": task_key(self.source, identifier, self.year, self.scope, phase),
                "phase": phase, **fields}

    def emit(self, kind, **fields):
        return self.memory.emit(kind, self._payload(**fields))

    def start(self):
        if self.started_at is not None:
            return
        self.started_at = self.clock()
        self.memory.emit("run_started", {
            "run_id": self.run_id, "source": self.source,
            "started_at": self.started_at.isoformat(), "scope_key": self.scope,
            "scope": {"states": getattr(self.args, "state", []),
                      "districts": getattr(self.args, "district", []),
                      "pincodes": getattr(self.args, "pincode", []),
                      "all_states": getattr(self.args, "all_states", False),
                      "academic_year": self.year},
        })
        if self.database:
            self.replay_summary = self.memory.replay(respect_backoff=True)
        self._refresh_tasks()

    def _refresh_tasks(self):
        self._tasks = {str(task.get("task_id")): task for task in self.memory.tasks(
            self.source, academic_year=self.year)}

    def checkpoint(self, identifier=""):
        return self.memory.checkpoint(self.source, school_identifier=str(identifier),
                                      academic_year=self.year,
                                      scope_key=self.scope if not identifier else "") or {}

    def eligible(self, task, explicit=False):
        status = task.get("task_status", task.get("status", ""))
        if status in ("completed", "skipped", "filtered", "unchanged"):
            return False
        if status in ("manual_intervention", "paused", "blocked") and not explicit:
            return False
        if int(task.get("attempt_count") or 0) >= self.max_attempts and not explicit:
            return False
        due = timestamp(task.get("next_retry_at"))
        return bool(explicit or due is None or due <= self.clock())

    def scope_due(self):
        if getattr(self.args, "refresh", False) or getattr(self.args, "retry_failed", False):
            return True
        self._refresh_tasks()
        tasks = [task for task in self._tasks.values() if task.get("scope_key") == self.scope]
        if any(task.get("task_status", task.get("status")) in ("manual_intervention", "paused", "blocked") for task in tasks):
            return False
        failures = [task for task in tasks if task.get("task_status", task.get("status")) in ("failed", "pending", "running", "retry")]
        if any(self.eligible(task) for task in failures):
            return True
        if failures:
            return False
        next_check = timestamp(self.checkpoint().get("next_check_at"))
        return next_check is None or next_check <= self.clock()

    def retry_rows(self, explicit=None):
        self._refresh_tasks()
        explicit = getattr(self.args, "retry_failed", False) if explicit is None else explicit
        rows = []
        for task in self._tasks.values():
            if task.get("scope_key") != self.scope:
                continue
            if task.get("phase", "detail") != "detail" or not task.get("school_identifier"):
                continue
            if task.get("task_status", task.get("status")) not in (
                    "failed", "pending", "running", "retry", "manual_intervention", "paused", "blocked"):
                continue
            if self.eligible(task, explicit=explicit) and isinstance(task.get("task_input"), dict):
                rows.append(task)
        return rows

    def discovered(self, identifier, row, state=None, district=None):
        old = self._tasks.get(task_key(self.source, identifier, self.year, self.scope))
        # Discovery must not reset a previous failure's attempt count/retry time.
        if old is None:
            self.emit("task_discovered", identifier=identifier, state=state, district=district,
                      pincode=None, task_input=row, task_status="pending")
        self.event("school_discovered", identifier=identifier, state=state, district=district)

    def detail_due(self, identifier, existing, path=None, *, change_indicator=None, reliable=False):
        task = self._tasks.get(task_key(self.source, identifier, self.year, self.scope))
        if task and task.get("task_status", task.get("status")) in (
                "failed", "paused", "blocked", "manual_intervention", "running", "retry"):
            if not self.eligible(task, explicit=getattr(self.args, "retry_failed", False)):
                return False
            return True
        if not existing:
            return True
        if getattr(self.args, "refresh", False):
            return True
        checkpoint = self.checkpoint(identifier)
        if reliable and change_indicator is not None:
            if checkpoint.get("source_change_indicator") != change_indicator:
                return True
        # Neither a listing request nor an import changes last_detail_check.
        checked = timestamp(checkpoint.get("last_detail_check"))
        if checked is None and path and Path(path).exists():
            checked = datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)
        return checked is None or checked + timedelta(days=self.refresh_days) <= self.clock()

    def attempting(self, identifier, row=None, state=None, district=None):
        self.emit("task_started", identifier=identifier, state=state, district=district,
                  task_input=row or {}, task_status="running",
                  last_attempt_at=self.clock().isoformat())

    def event(self, name, identifier="", **fields):
        self.emit("event", identifier=identifier, event=name, **fields)
        LOGGER.info("%s", json.dumps({"event": name, "source": self.source,
                                      "run_id": self.run_id, "school_identifier": str(identifier)}))

    def failed(self, identifier, error, row=None, state=None, district=None, phase="detail"):
        from school_scraping.memory import sanitize
        category = ("captcha_or_authentication" if isinstance(error, ManualIntervention)
                    else "validation" if isinstance(error, (ValueError, TypeError))
                    else "network_timeout" if "timeout" in type(error).__name__.lower()
                    else "source_error")
        manual = isinstance(error, ManualIntervention)
        prior = self._tasks.get(task_key(self.source, identifier, self.year, self.scope, phase), {})
        attempts = max(1, int(prior.get("attempt_count") or 0) + 1)
        next_retry = self.clock() + timedelta(minutes=self.retry_minutes * min(2 ** (attempts - 1), 32))
        self.emit("task_failed", identifier=identifier, phase=phase,
                  task_input=row or {}, state=state, district=district,
                  task_status="manual_intervention" if manual else "failed",
                  error_category=category, error_message=sanitize(str(error)),
                  sanitized_traceback=sanitize("".join(traceback.format_exception(type(error), error, error.__traceback__))),
                  next_retry_at=None if manual else next_retry.isoformat(),
                  attempt_count=attempts,
                  last_attempt_at=self.clock().isoformat())
        self._refresh_tasks()

    def completed(self, identifier, status="skipped", **fields):
        self.emit("task_completed", identifier=identifier, task_status=status,
                  completed_at=self.clock().isoformat(), **fields)
        self._tasks.pop(task_key(self.source, identifier, self.year, self.scope), None)

    def filtered_record(self, identifier):
        """Reuse verified nonmatching PIN details until their own detail TTL."""
        checkpoint = self.checkpoint(identifier)
        if getattr(self.args, "refresh", False) or checkpoint.get("checkpoint_status") != "filtered":
            return None
        checked = timestamp(checkpoint.get("last_detail_check"))
        if checked is None or checked + timedelta(days=self.refresh_days) <= self.clock():
            return None
        path = checkpoint.get("filtered_path")
        try:
            record = read_json(path) if path else None
            from school_scraping.common import complete_record, record_identifier
            if (complete_record(self.source, record) and record_identifier(self.source, record) == str(identifier)
                    and content_hash(self.source, record) == checkpoint.get("content_hash")):
                return record
            return None
        except (OSError, ValueError, TypeError):
            return None

    def filtered(self, identifier, record, verified_pin=None):
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        path = self.state_dir / "filtered_payloads" / self.source / (self.year or "current") / (digest + ".json")
        if not path.exists():
            atomic_json(path, record)
        checked = self.clock()
        self.emit("detail_filtered", identifier=identifier, task_status="filtered",
                  filtered_path=str(path.resolve()), verified_pincode=verified_pin,
                  content_hash=content_hash(self.source, record), last_detail_check=checked.isoformat(),
                  next_check_at=(checked + timedelta(days=self.refresh_days)).isoformat())
        self.completed(identifier, "filtered")

    def persist(self, identifier, record, raw_path, *, fetched=True):
        """Queue an immutable import, independently of the latest raw file path."""
        raw_path = Path(raw_path).resolve()
        raw_bytes = raw_path.read_bytes()
        digest = hashlib.sha256(raw_bytes).hexdigest()
        content = content_hash(self.source, record)
        checkpoint = self.checkpoint(identifier)
        if not fetched and checkpoint.get("imported") and checkpoint.get("last_known_content_hash") == content:
            return "already_imported"
        import_task = self.memory.task(task_key(self.source, identifier, self.year, self.scope, "import"),
                                       source=self.source, academic_year=self.year) or {}
        queued_import = (import_task.get("status", import_task.get("task_status")) == "pending_import"
                         and (import_task.get("content_hash") or import_task.get("raw_sha256")) == content)
        if (not fetched and checkpoint.get("checkpoint_status") == "pending_import"
                and checkpoint.get("pending_content_hash") == content and queued_import):
            self.pending_imports += 1
            return "pending_import"
        snapshot = self.state_dir / "import_payloads" / (digest + ".json")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if snapshot.exists():
            if hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest:
                raise ValueError("Immutable import snapshot failed its hash check")
        else:
            # Preserve the exact saved bytes, including formatting, for archive import.
            from tempfile import NamedTemporaryFile
            import os
            with NamedTemporaryFile(dir=snapshot.parent, prefix=".pending-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(raw_bytes)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, snapshot)
            finally:
                if temporary.exists():
                    temporary.unlink()
        checked = self.clock() if fetched else datetime.fromtimestamp(raw_path.stat().st_mtime, timezone.utc)
        next_check = checked + timedelta(days=self.refresh_days)
        payload = self._payload(identifier, phase="import", raw_path=str(raw_path),
                                snapshot_path=str(snapshot.resolve()), raw_sha256=digest,
                                content_hash=content, region=(record.get("metadata") or {}).get("location"),
                                data_root=str(Path(self.args.data_dir).resolve()),
                                last_detail_check=checked.isoformat(), next_check_at=next_check.isoformat())
        self.memory.emit("raw_saved", {**payload, "phase": "detail",
                                      "task_id": task_key(self.source, identifier, self.year, self.scope),
                                      "task_status": "pending_import", "fetched": fetched})
        self._tasks.pop(task_key(self.source, identifier, self.year, self.scope), None)
        if checkpoint.get("imported") and checkpoint.get("last_known_content_hash") == content:
            self.completed(identifier, "unchanged")
            return "already_imported"
        # Each durable request has one stable ID for replay. A later run can
        # safely resubmit after a crash because the existing pipeline upserts.
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
            ["import:v1", self.run_id, self.source, self.year, str(identifier), digest])))
        result = self.memory.emit("import_requested", payload, event_id=event_id)
        if result.get("synced"):
            self.imported += 1
            self.completed(identifier, "completed")
            return "imported"
        self.pending_imports += 1
        return "pending_import"

    def finish(self, report, *, scope_complete=False):
        if self.finished:
            return
        self.finished = True
        counts = report.setdefault("counts", {})
        counts["imported"] = self.imported
        counts["pending_import"] = self.pending_imports
        if self.replay_summary is not None:
            report["recovery"] = self.replay_summary
        if self.database and self.pending_imports and report.get("status") in ("completed", "not_due"):
            report["status"] = "pending_database"
        finished = self.clock()
        if scope_complete and report.get("status") in ("completed", "pending_database"):
            self.emit("scope_checked", phase="discovery", complete=report["status"] == "completed",
                      last_listing_check=finished.isoformat(),
                      last_successful_check=finished.isoformat() if report["status"] == "completed" else None,
                      next_check_at=(finished + timedelta(hours=self.check_hours)).isoformat())
        self.memory.emit("run_finished", {
            "run_id": self.run_id, "source": self.source, "status": report.get("status", "failed"),
            "counts": counts, "finished_at": finished.isoformat(),
            "duration_seconds": max(0, (finished - (self.started_at or finished)).total_seconds()),
        })
        report["run_id"] = self.run_id
        report["database_enabled"] = self.database


class _DatabaseMemoryView:
    """Adapt the existing lifecycle projector without opening a file journal."""
    def __init__(self, store, source, year):
        self.store, self.source, self.year = store, source, year

    def emit(self, kind, payload, event_id=None):
        from school_scraping.database_only import DatabaseOnlyError
        result = self.store.event(kind, payload)
        if result.get("stopped"):
            raise DatabaseOnlyError("Database-only collection stopped after a storage failure")
        return result

    def tasks(self, source=None, scope_key=None, academic_year=""):
        if source not in (None, self.source):
            raise ValueError("Database runtime source mismatch")
        return [row for row in self.store.tasks(scope_key=scope_key)
                if row.get("storage_mode") == "database"]

    def checkpoint(self, source, school_identifier="", academic_year="", scope_key=""):
        if source != self.source:
            raise ValueError("Database runtime source mismatch")
        return self.store.checkpoint(school_identifier, scope_key=scope_key)

    def task(self, identifier, source=None, academic_year=""):
        return next((row for row in self.tasks(source) if row.get("task_id") == identifier), None)

    def replay(self, *args, **kwargs):
        raise RuntimeError("Database-only mode never replays filesystem journals")


class DatabaseOnlyRuntime(IncrementalRuntime):
    """Shared collection policy backed exclusively by committed PostgreSQL state."""
    database_only = True

    def __init__(self, args, source, store=None, clock=utcnow):
        from school_scraping.database_only import DatabaseOnlyStore
        year = getattr(args, "academic_year", "") if source == "udise" else ""
        self.store = store or DatabaseOnlyStore(source, year)
        self.new_only = bool(getattr(args, "new_only", False))
        self._checked, self._existing, self._checkpoints = set(), set(), {}
        self._database_failure = None
        self.metrics = {"listing_candidates_examined": 0, "existing_records_skipped": 0,
                        "new_details_fetched": 0, "detail_fetches_avoided": 0}
        super().__init__(args, source, memory=_DatabaseMemoryView(self.store, source, year), clock=clock)

    def start(self):
        if self.started_at is not None:
            return
        self.started_at = self.clock()
        self.memory.emit("run_started", {"run_id": self.run_id, "source": self.source,
            "started_at": self.started_at.isoformat(), "scope_key": self.scope,
            "scope": {"states": getattr(self.args, "state", []),
                      "districts": getattr(self.args, "district", []),
                      "pincodes": getattr(self.args, "pincode", []),
                      "all_states": getattr(self.args, "all_states", False), "academic_year": self.year}})
        self._refresh_tasks()

    def checkpoint(self, identifier=""):
        # New-only listing discovery remains authoritative even after a previous
        # complete geographic check. Manual pauses/backoff still use DB tasks.
        if not identifier and self.new_only:
            return {}
        if identifier not in self._checkpoints:
            self._checkpoints[identifier] = self.store.checkpoint(str(identifier), scope_key=self.scope if not identifier else "") or {}
        return self._checkpoints[identifier]

    def prefetch(self, identifiers):
        from school_scraping.database_only import DatabaseOnlyError, validate_identifier
        if self.store.failed:
            raise DatabaseOnlyError("Database-only lookup stopped after a storage failure")
        values = list(dict.fromkeys(validate_identifier(self.source, value) for value in identifiers))
        missing = [value for value in values if value not in self._checked]
        if missing:
            self._existing.update(self.store.lookup_committed(missing))
            self._checked.update(missing)

    def skip_existing(self, identifier):
        self.prefetch([identifier])
        if identifier not in self._existing:
            return False
        if not self.new_only:
            if getattr(self.args, "refresh", False):
                return False
            checkpoint = self.checkpoint(identifier)
            if not checkpoint.get("imported") or checkpoint.get("checkpoint_status") != "completed":
                return False
            checked = timestamp(checkpoint.get("last_detail_check"))
            if checked is None or checked + timedelta(days=self.refresh_days) <= self.clock():
                return False
        self.metrics["existing_records_skipped"] += 1
        self.metrics["detail_fetches_avoided"] += 1
        self.completed(identifier, "skipped")
        return True

    def note_detail_fetch(self):
        self.metrics["new_details_fetched"] += 1

    def note_candidate(self):
        self.metrics["listing_candidates_examined"] += 1

    def filtered_record(self, identifier):
        return None  # Never consult a legacy filtered_payloads path.

    def filtered(self, identifier, record, verified_pin=None):
        self.emit("detail_filtered", identifier=identifier, task_status="filtered",
                  verified_pincode=verified_pin, content_hash=content_hash(self.source, record),
                  last_detail_check=self.clock().isoformat(),
                  next_check_at=(self.clock() + timedelta(days=self.refresh_days)).isoformat())
        self.completed(identifier, "filtered")

    def persist(self, identifier, record, raw_path=None, *, fetched=True):
        from school_scraping.database_only import DatabaseOnlyError
        metadata = record.get("metadata") or {}
        region = metadata.get("location") or metadata.get("state") or (getattr(self.args, "state", []) or ["Unknown"])[0]
        result = self.store.import_record(identifier, record, region, scope_key=self.scope,
            run_id=self.run_id, observed_at=self.clock(), new_only=self.new_only)
        self._checkpoints.pop(identifier, None)
        self._existing.add(identifier)
        if result.get("status") == "already_committed":
            self.metrics["existing_records_skipped"] += 1
            self.completed(identifier, "unchanged")
            return "already_imported"
        if not result.get("acknowledged"):
            raise DatabaseOnlyError("Database import did not acknowledge a successful commit")
        self.imported += 1
        return "imported"

    def failed(self, identifier, error, row=None, state=None, district=None, phase="detail"):
        from school_scraping.database_only import DatabaseOnlyError
        if isinstance(error, DatabaseOnlyError):
            self._database_failure = {"message": str(error),
                "data_commit_confirmed": error.data_committed, "commit_outcome_unknown": error.commit_outcome_unknown}
        if self.store.failed:
            return  # Do not retry a broken DB to log a DB failure.
        super().failed(identifier, error, row, state, district, phase)

    def finish(self, report, *, scope_complete=False):
        from school_scraping.database_only import DatabaseOnlyError
        if self.metrics["existing_records_skipped"]:
            coverage = report.setdefault("coverage", {})
            coverage.update(details_complete=False, scope_complete=False)
            scope_complete = False
        self._report_counts(report)
        try:
            if not self.store.failed:
                super().finish(report, scope_complete=scope_complete)
            else:
                self.finished = True
        except DatabaseOnlyError as error:
            self._database_failure = {"message": str(error), "data_commit_confirmed": error.data_committed,
                                      "commit_outcome_unknown": error.commit_outcome_unknown}
            report["status"] = "failed"
        self._report_counts(report)
        if self._database_failure:
            report["database_failure"] = self._database_failure
        if self.metrics["existing_records_skipped"]:
            report.get("coverage", {}).setdefault("limitations", []).append(
                "Committed identities skipped without fresh detail/PIN/geography verification")

    def _report_counts(self, report):
        counts = report.setdefault("counts", {})
        counts.update(self.metrics)
        stats = self.store.stats
        counts.update(identifiers_checked=stats.get("identifiers_checked", 0),
            canonical_validation_passed=stats.get("canonical_validated", 0),
            new_imports_committed=stats.get("imports_committed", 0),
            import_acknowledgements=stats.get("import_acknowledgements", 0),
            imported=max(self.imported, stats.get("imports_committed", 0)), pending_import=0,
            pending_acknowledgements=max(0, stats.get("imports_committed", 0) - stats.get("import_acknowledgements", 0)),
            local_school_json_files_generated=0,
            database_lookup_duration_seconds=stats.get("database_lookup_duration_seconds", 0.0))
        report.update(storage_mode="database", new_only=self.new_only, offline_durability=False,
                      run_id=self.run_id, database_enabled=True)

    def close(self):
        self.store.close()


def make_runtime(args, source):
    return DatabaseOnlyRuntime(args, source) if getattr(args, "storage_mode", "files") == "database" else IncrementalRuntime(args, source)


def sync_pending(args):
    """Explicit maintenance mode never opens a source browser or requests data."""
    from school_scraping.memory import MemoryStore
    from database.scrape_pipeline import import_scraped_record
    state_dir = getattr(args, "state_dir", None) or (Path(args.data_dir) / ".scraping_state")
    memory = MemoryStore(state_dir, database_enabled=bool(getattr(args, "database", False)),
                         import_handler=import_scraped_record)
    result = {"synced": 0, "duplicates": 0, "failed": 0, "pending": 0, "batches": 0}
    for _ in range(100):
        batch = memory.replay()
        result["batches"] += 1
        for field in ("synced", "duplicates", "failed"):
            result[field] += batch.get(field, 0)
        result["pending"] = batch.get("pending", 0)
        if batch.get("failed") or not batch.get("pending") or not batch.get("synced"):
            break
    print(json.dumps({"mode": "sync_pending", "database_enabled": bool(getattr(args, "database", False)),
                      "replay": result}, default=str, indent=2))
    return int(bool(result["failed"] or (getattr(args, "database", False) and result["pending"])))
