"""Incremental freshness/recovery tests using local journals and isolated PG17."""

import copy
import contextlib
import io
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

import saras_dynamic_scraper as saras
from database.scrape_pipeline import import_scraped_record
from school_scraping.common import ManualIntervention
from school_scraping.incremental import IncrementalRuntime, content_hash, task_key, sync_pending
from school_scraping.memory import MemoryStore


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 9, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **kwargs):
        self.now += timedelta(**kwargs)


def options(root, *, database=False, state="Punjab", retry_failed=False, year="2025-26"):
    return SimpleNamespace(data_dir=Path(root) / "data", state_dir=Path(root) / "state",
                           state=[state], district=[], pincode=[], all_states=False,
                           academic_year=year, database=database, refresh=False,
                           retry_failed=retry_failed, refresh_days=30, check_interval_hours=24,
                           retry_delay_minutes=60, max_task_attempts=3, limit=None)


def raw_record(source, identifier):
    if source == "saras":
        return {"metadata": {"affiliation_number": identifier, "state": "PUNJAB"},
                "listing_data": {"affiliation_number": identifier},
                "school_details": {"Affiliation Number": identifier,
                                   "Name of Institution": "Incremental " + identifier,
                                   "State": "PUNJAB", "District": "LUDHIANA",
                                   "Postal Address": "School Road", "Pin Code": "141001"}, "links": []}
    return {"metadata": {"academic_year": "2025-26", "location": "Punjab", "udise_code": identifier},
            "school_summary": {"udiseschCode": identifier, "schoolName": "Incremental " + identifier,
                               "stateName": "PUNJAB", "districtName": "LUDHIANA", "pincode": 141001},
            "school_profile": {"address": "School Road"},
            "student_teacher_statistics": {"totalBoy": 2, "totalGirl": 3, "totalCount": 5},
            "infrastructure_facilities": {"rampsYn": 1}, "report_card": {"totalTeacher": 1},
            "school_history": []}


def save_record(args, source, identifier, record):
    path = (args.data_dir / "Punjab" / "2025-26" / f"{identifier}.json" if source == "udise"
            else args.data_dir / "CBSE_SARAS" / "punjab" / "schools" / f"{identifier}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class RuntimeOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.args = options(self.temporary.name)
        self.clock = Clock()
        self.memory = MemoryStore(self.args.state_dir)
        self.runtime = IncrementalRuntime(self.args, "saras", self.memory, self.clock)
        self.runtime.start()
        self.identifier = "1630001"
        self.record = raw_record("saras", self.identifier)
        self.path = save_record(self.args, "saras", self.identifier, self.record)
        self.row = {"affiliation_number": self.identifier, "detail_url": saras.BASE_URL + "/school"}

    def fail_detail(self, error=RuntimeError("Source unavailable")):
        self.runtime.discovered(self.identifier, self.row, "Punjab")
        self.runtime.attempting(self.identifier, self.row, "Punjab")
        self.runtime.failed(self.identifier, error, self.row, "Punjab")

    def test_listing_and_pending_import_do_not_extend_detail_ttl(self):
        self.runtime.persist(self.identifier, self.record, self.path)
        checked = self.runtime.checkpoint(self.identifier)["last_detail_check"]
        self.clock.advance(days=10)
        self.runtime.discovered(self.identifier, self.row)
        self.assertEqual(self.runtime.persist(self.identifier, self.record, self.path, fetched=False), "pending_import")
        self.assertEqual(self.runtime.checkpoint(self.identifier)["last_detail_check"], checked)
        self.assertFalse(self.runtime.detail_due(self.identifier, self.record, self.path))
        self.clock.advance(days=21)
        self.assertTrue(self.runtime.detail_due(self.identifier, self.record, self.path))

    def test_pending_import_is_not_a_source_retry(self):
        self.runtime.discovered(self.identifier, self.row)
        self.runtime.attempting(self.identifier, self.row)
        self.runtime.persist(self.identifier, self.record, self.path)
        self.assertEqual(self.runtime.retry_rows(explicit=True), [])

    def test_filtered_pin_details_are_cached_until_expiry_without_import(self):
        self.runtime.filtered(self.identifier, self.record, "141001")
        self.clock.advance(days=1)
        self.assertEqual(self.runtime.filtered_record(self.identifier), self.record)
        self.assertEqual(self.runtime.checkpoint(self.identifier)["checkpoint_status"], "filtered")
        self.assertFalse(any(event["kind"] == "import_requested" for event in self.memory._events()))
        self.clock.advance(days=30)
        self.assertIsNone(self.runtime.filtered_record(self.identifier))

    def test_altered_filtered_cache_is_not_trusted(self):
        self.runtime.filtered(self.identifier, self.record, "141001")
        checkpoint = self.runtime.checkpoint(self.identifier)
        altered = copy.deepcopy(self.record)
        altered["school_details"]["Pin Code"] = "110001"
        Path(checkpoint["filtered_path"]).write_text(json.dumps(altered), encoding="utf-8")
        self.assertIsNone(self.runtime.filtered_record(self.identifier))

    def test_explicit_sync_drains_multiple_batches_without_source_access(self):
        memory = Mock()
        memory.replay.side_effect = [
            {"synced": 1000, "duplicates": 0, "failed": 0, "pending": 1},
            {"synced": 1, "duplicates": 0, "failed": 0, "pending": 0},
        ]
        self.args.database = True
        with patch("school_scraping.memory.MemoryStore", return_value=memory), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync_pending(self.args), 0)
        self.assertEqual(memory.replay.call_count, 2)

    def test_crash_after_raw_checkpoint_cannot_lose_import_request(self):
        # Simulate durable raw_saved followed by process death before queuing ETL.
        self.runtime.emit("raw_saved", identifier=self.identifier,
                          content_hash=content_hash("saras", self.record),
                          raw_path=str(self.path), task_status="pending_import",
                          last_detail_check=self.clock.now.isoformat())
        restarted = IncrementalRuntime(self.args, "saras", self.memory, self.clock)
        restarted.start()
        self.assertEqual(restarted.persist(self.identifier, self.record, self.path,
                                           fetched=False), "pending_import")
        imports = [event for event in self.memory._events() if event["kind"] == "import_requested"]
        self.assertEqual(len(imports), 1)
        self.assertTrue(Path(imports[0]["payload"]["snapshot_path"]).is_file())

    def test_failure_backoff_max_attempts_and_explicit_override(self):
        self.fail_detail()
        self.assertFalse(self.runtime.detail_due(self.identifier, None))
        self.clock.advance(minutes=61)
        self.assertTrue(self.runtime.detail_due(self.identifier, None))
        for _ in range(2):
            self.fail_detail()
            self.clock.advance(days=1)
        self.assertFalse(self.runtime.detail_due(self.identifier, None))
        self.args.retry_failed = True
        self.assertTrue(self.runtime.detail_due(self.identifier, None))

    def test_manual_pause_survives_other_geographic_scope(self):
        self.fail_detail(ManualIntervention("Manual access required"))
        self.clock.advance(days=90)
        other_args = options(self.temporary.name, state="Haryana")
        other = IncrementalRuntime(other_args, "saras", self.memory, self.clock)
        other.start()
        self.assertFalse(other.detail_due(self.identifier, None))
        other_args.retry_failed = True
        self.assertTrue(other.detail_due(self.identifier, None))

    def test_manual_discovery_scope_stays_paused_until_explicit_retry(self):
        self.runtime.failed("", ManualIntervention("Manual access required"), phase="discovery")
        self.clock.advance(days=10)
        other = IncrementalRuntime(self.args, "saras", self.memory, self.clock)
        other.start()
        self.assertFalse(other.scope_due())
        self.args.retry_failed = True
        self.assertTrue(other.scope_due())

    def test_discovery_attempts_exhaust_without_detail_started_event(self):
        for _ in range(3):
            self.runtime.failed("", RuntimeError("Listing unavailable"), phase="discovery")
            self.clock.advance(days=1)
        tasks = [task for task in self.memory.tasks("saras", scope_key=self.runtime.scope)
                 if task.get("phase") == "discovery"]
        self.assertEqual(tasks[0]["attempt_count"], 3)
        self.assertFalse(self.runtime.scope_due())

    def test_source_identity_is_stable_across_scopes_and_separate_by_year_source(self):
        self.assertEqual(task_key("udise", "03010100101", "2025-26", "Punjab"),
                         task_key("udise", "03010100101", "2025-26", "Haryana"))
        self.assertNotEqual(task_key("udise", "03010100101", "2025-26"),
                            task_key("udise", "03010100101", "2026-27"))
        self.assertNotEqual(task_key("udise", "1630001"), task_key("saras", "1630001"))

    def test_hash_excludes_listing_metadata_but_detects_detail_change(self):
        moved = copy.deepcopy(self.record)
        moved["metadata"]["selected_district"] = "Another query"
        moved["listing_data"]["serial_number"] = "42"
        self.assertEqual(content_hash("saras", moved), content_hash("saras", self.record))
        moved["school_details"]["Postal Address"] = "New Road"
        self.assertNotEqual(content_hash("saras", moved), content_hash("saras", self.record))

    def test_verified_change_indicator_can_trigger_refresh_before_ttl(self):
        self.runtime.emit("raw_saved", identifier=self.identifier,
                          source_change_indicator="official-version-1",
                          last_detail_check=self.clock.now.isoformat())
        self.assertFalse(self.runtime.detail_due(self.identifier, self.record, self.path,
                                                change_indicator="official-version-1", reliable=True))
        self.assertTrue(self.runtime.detail_due(self.identifier, self.record, self.path,
                                               change_indicator="official-version-2", reliable=True))
        self.assertFalse(self.runtime.detail_due(self.identifier, self.record, self.path,
                                                change_indicator="unreliable-listing", reliable=False))

    def test_completed_listing_can_wait_for_database_without_network_retry(self):
        def unavailable():
            raise ConnectionError("Injected isolated database outage")

        args = options(self.temporary.name, database=True)
        memory = MemoryStore(args.state_dir, database_enabled=True,
                             connect=unavailable, import_handler=import_scraped_record,
                             retry_seconds=3600)
        runtime = IncrementalRuntime(args, "saras", memory, self.clock)
        with self.assertLogs("school_scraping", level="WARNING"):
            runtime.start()
        runtime.persist(self.identifier, self.record, self.path)
        report = {"status": "completed", "counts": {}}
        runtime.finish(report, scope_complete=True)
        self.assertEqual(report["status"], "pending_database")
        self.assertFalse(runtime.checkpoint(self.identifier).get("imported", False))
        self.assertEqual(runtime.checkpoint()["checkpoint_status"], "incomplete")
        other = IncrementalRuntime(args, "saras", memory, self.clock)
        other.start()
        self.assertEqual(other.replay_summary["skipped_backoff"], 1)
        self.assertFalse(other.scope_due())
        self.assertFalse(other.detail_due(self.identifier, self.record, self.path))

    def test_retry_failed_uses_saved_input_without_rediscovering(self):
        self.fail_detail()
        self.args.retry_failed = True

        class SavedInputBrowser:
            def __init__(self, record):
                self.record, self.calls = record, []

            def states(self):
                raise AssertionError("Retrying a saved school must not rediscover states")

            def detail(self, school):
                self.calls.append(school)
                return {"fields": self.record["school_details"], "links": []}

        browser = SavedInputBrowser(self.record)
        report = saras.run(self.args, browser, self.runtime)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(browser.calls, [self.row])


@unittest.skipUnless(os.getenv("SCHOOL_SCRAPING_TEST_DSN"), "Isolated PostgreSQL test DSN not supplied")
class RuntimePostgresTests(unittest.TestCase):
    def setUp(self):
        self.dsn = os.environ["SCHOOL_SCRAPING_TEST_DSN"]
        settings = conninfo_to_dict(self.dsn)
        if settings.get("host") != "127.0.0.1" or settings.get("port") != "55439":
            self.skipTest("Not the explicitly provisioned isolated PostgreSQL test cluster")
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.args = options(self.temporary.name, database=True)
        self.clock = Clock()
        self.identifier = str(uuid.uuid4().int % 10 ** 11).zfill(11)
        self.record = raw_record("udise", self.identifier)
        self.path = save_record(self.args, "udise", self.identifier, self.record)
        self.memory = MemoryStore(self.args.state_dir, database_enabled=True,
                                  connect=lambda: psycopg.connect(self.dsn),
                                  import_handler=import_scraped_record, retry_seconds=0)
        self.runtime = IncrementalRuntime(self.args, "udise", self.memory, self.clock)
        self.runtime.start()

    def test_checkpoint_success_follows_pipeline_commit_and_replay_is_idempotent(self):
        self.assertEqual(self.runtime.persist(self.identifier, self.record, self.path), "imported")
        checkpoint = self.runtime.checkpoint(self.identifier)
        self.assertTrue(checkpoint["imported"])
        self.assertEqual(checkpoint["last_detail_check"], self.clock.now.isoformat())
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s",
                                               (self.identifier,)).fetchone()[0], 1)
        self.clock.advance(days=10)
        self.assertEqual(self.runtime.persist(self.identifier, self.record, self.path, fetched=False), "already_imported")
        self.assertEqual(self.runtime.checkpoint(self.identifier)["last_detail_check"], checkpoint["last_detail_check"])
        replay = self.memory.replay()
        self.assertEqual(replay["failed"], 0)
        self.assertEqual(len([event for event in self.memory._events() if event["kind"] == "import_requested"]), 1)

    def test_pipeline_failure_keeps_raw_and_recovers_without_detail_fetch(self):
        failed = [False]

        def handler(connection, payload):
            result = import_scraped_record(connection, payload)
            if not failed[0]:
                failed[0] = True
                raise RuntimeError("Injected failure after school writes")
            return result

        self.memory.import_handler = handler
        with self.assertLogs("school_scraping", level="WARNING"):
            self.assertEqual(self.runtime.persist(self.identifier, self.record, self.path), "pending_import")
        self.assertFalse(self.runtime.checkpoint(self.identifier)["imported"])
        self.assertTrue(self.path.is_file())
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s",
                                               (self.identifier,)).fetchone()[0], 0)
        self.clock.advance(days=1)
        recovered = IncrementalRuntime(self.args, "udise", self.memory, self.clock)
        recovered.start()
        self.assertTrue(recovered.checkpoint(self.identifier)["imported"])
        self.assertFalse(recovered.detail_due(self.identifier, self.record, self.path))
        self.assertEqual(recovered.persist(self.identifier, self.record, self.path, fetched=False), "already_imported")
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s",
                                               (self.identifier,)).fetchone()[0], 1)

    def test_unchanged_detail_refresh_updates_freshness_without_another_import(self):
        self.runtime.persist(self.identifier, self.record, self.path)
        initial = len([event for event in self.memory._events() if event["kind"] == "import_requested"])
        self.clock.advance(days=31)
        self.assertTrue(self.runtime.detail_due(self.identifier, self.record, self.path))
        self.assertEqual(self.runtime.persist(self.identifier, self.record, self.path, fetched=True), "already_imported")
        self.assertFalse(self.runtime.detail_due(self.identifier, self.record, self.path))
        self.assertEqual(len([event for event in self.memory._events() if event["kind"] == "import_requested"]), initial)
        self.assertEqual(self.runtime.checkpoint(self.identifier)["last_detail_check"], self.clock.now.isoformat())

    def test_changed_detail_has_new_hash_and_updates_existing_source(self):
        self.runtime.persist(self.identifier, self.record, self.path)
        before_hash = self.runtime.checkpoint(self.identifier)["last_known_content_hash"]
        self.clock.advance(days=31)
        self.record["school_profile"]["address"] = "Changed Road"
        self.path = save_record(self.args, "udise", self.identifier, self.record)
        self.assertEqual(self.runtime.persist(self.identifier, self.record, self.path), "imported")
        self.assertNotEqual(self.runtime.checkpoint(self.identifier)["last_known_content_hash"], before_hash)
        with psycopg.connect(self.dsn) as connection:
            self.assertEqual(connection.execute("SELECT count(*), max(address) FROM school_scraping.school_source_records WHERE udise_code=%s",
                                               (self.identifier,)).fetchone(), (1, "Changed Road"))
