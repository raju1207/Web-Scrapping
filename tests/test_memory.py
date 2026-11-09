"""Memory durability/recovery tests; PostgreSQL cases require an explicit test DSN."""

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from school_scraping.memory import MemoryStore, sanitize, sanitize_error


class MemoryOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.memory = MemoryStore(self.root / "memory")
        self.payload = {"source": "saras", "school_identifier": "1630001", "academic_year": "",
                        "scope_key": "punjab", "run_id": "test-run", "content_hash": "a" * 64,
                        "raw_path": "data/CBSE_SARAS/punjab/schools/school.json"}

    def tearDown(self):
        self.temporary.cleanup()

    def test_construction_and_empty_getters_do_not_create_files_or_connect(self):
        connection = Mock(side_effect=AssertionError("Connection must stay lazy"))
        memory = MemoryStore(self.root / "absent", connect=connection)
        self.assertIsNone(memory.checkpoint("saras", "1630001"))
        self.assertEqual(memory.tasks("saras"), [])
        self.assertFalse(memory.state_dir.exists())
        connection.assert_not_called()

    def test_wal_is_fsynced_and_rebuilt_after_restart(self):
        with patch("school_scraping.memory.os.fsync", wraps=os.fsync) as fsync:
            result = self.memory.emit("raw_saved", self.payload)
        self.assertFalse(result["synced"])
        fsync.assert_called()
        checkpoint = MemoryStore(self.memory.state_dir).checkpoint("saras", "1630001")
        self.assertEqual(checkpoint["checkpoint_status"], "pending_import")
        self.assertFalse(checkpoint["imported"])
        self.assertTrue(checkpoint["last_detail_check"])
        self.assertNotIn("last_successful_save", checkpoint)

    def test_stable_event_ids_are_idempotent_and_reject_conflicting_payload(self):
        first = self.memory.emit("task_discovered", self.payload, event_id="stable-id")
        self.assertEqual(first, self.memory.emit("task_discovered", self.payload, event_id="stable-id"))
        self.assertEqual(len(self.memory.journal.read_text().splitlines()), 1)
        with self.assertRaises(ValueError):
            self.memory.emit("task_completed", self.payload, event_id="stable-id")

    def test_task_aliases_attempts_and_failed_state_survive_rediscovery(self):
        self.memory.emit("task_discovered", self.payload)
        self.memory.emit("task_started", {**self.payload, "attempt_count": 3})
        self.memory.emit("task_failed", {**self.payload, "task_status": "retry_wait", "error_category": "TimeoutError"})
        self.memory.emit("task_discovered", self.payload)
        task = self.memory.tasks("saras", "punjab")[0]
        self.assertEqual(task["status"], "retry_wait")
        self.assertEqual(task["task_status"], task["status"])
        self.assertEqual(task["attempt_count"], 3)

    def test_checkpoint_uses_source_and_year_not_school_name_or_scope(self):
        self.memory.emit("raw_saved", {**self.payload, "source": "udise", "academic_year": "2025-26"})
        self.assertIsNone(self.memory.checkpoint("udise", "1630001", "2024-25"))
        self.assertIsNone(self.memory.checkpoint("saras", "1630001", "2025-26"))
        self.assertIsNotNone(self.memory.checkpoint("udise", "1630001", "2025-26", "different-state"))

    def test_incomplete_scope_never_becomes_successful_discovery(self):
        self.memory.emit("scope_checked", {"source": "saras", "scope_key": "punjab", "complete": False})
        checkpoint = self.memory.checkpoint("saras", scope_key="punjab")
        self.assertEqual(checkpoint["checkpoint_status"], "incomplete")
        self.assertNotIn("last_successful_check", checkpoint)
        self.memory.emit("scope_checked", {"source": "saras", "scope_key": "punjab", "complete": True})
        self.assertTrue(self.memory.checkpoint("saras", scope_key="punjab")["last_successful_check"])

    def test_pending_import_cannot_claim_success_while_database_is_disabled(self):
        self.memory.emit("raw_saved", self.payload)
        self.memory.emit("import_requested", self.payload)
        checkpoint = self.memory.checkpoint("saras", "1630001")
        self.assertFalse(checkpoint["imported"])
        self.assertNotIn("last_successful_save", checkpoint)
        self.assertEqual(self.memory.replay()["pending"], 2)

    def test_cached_pending_checkpoint_advances_after_later_commit(self):
        self.memory.emit("raw_saved", self.payload)
        self.memory.emit("import_requested", self.payload)
        self.assertFalse(self.memory.checkpoint("saras", "1630001")["imported"])
        self.memory.database_enabled = True
        self.memory.connect = Mock(return_value=Mock(closed=False))
        self.memory._bootstrapped.add(("saras", ""))
        with patch.object(self.memory, "_apply_db", return_value=True):
            self.assertEqual(self.memory.replay()["synced"], 2)
        self.assertTrue(self.memory.checkpoint("saras", "1630001")["imported"])

    def test_partial_utf8_tail_is_preserved_and_new_events_remain_readable(self):
        self.memory.emit("task_discovered", self.payload)
        with self.memory.journal.open("ab") as stream:
            stream.write(b'{"event_id":"partial-\xe2\x82')
        self.memory.emit("raw_saved", self.payload)
        self.assertIsNotNone(self.memory.checkpoint("saras", "1630001"))
        self.assertTrue(self.memory.warnings)
        self.assertIn(b"partial-", self.memory.journal.read_bytes())

    def test_database_outage_backoff_does_not_drop_events(self):
        connection = Mock(side_effect=OSError("password=private-secret"))
        memory = MemoryStore(self.root / "outage", database_enabled=True, connect=connection, retry_seconds=60)
        memory.emit("task_discovered", self.payload)
        memory.emit("raw_saved", self.payload)
        self.assertEqual(connection.call_count, 1)
        self.assertEqual(len(memory._events()), 3)
        self.assertEqual(sum(event["kind"] == "event" for event in memory._events()), 1)
        self.assertNotIn("private-secret", memory.retry_file.read_text())
        self.assertIsNotNone(memory.checkpoint("saras", "1630001"))

    def test_sanitization_covers_nested_credentials_urls_and_tracebacks(self):
        value = {"password": "secret-one", "headers": {"Authorization": "secret-two"},
                 "task_input": {"detail_url": "https://user:secret-three@example.com/path?id=123&token=secret-four"},
                 "error_message": "{'password': 'secret-five'} Authorization=secret-six Bearer secret-seven"}
        encoded = json.dumps(sanitize(value))
        for secret in ("secret-one", "secret-two", "secret-three", "secret-four", "secret-five", "secret-six", "secret-seven"):
            self.assertNotIn(secret, encoded)
        try:
            raise RuntimeError("password=private-eight")
        except RuntimeError as error:
            self.assertNotIn("private-eight", json.dumps(sanitize_error(error)))


@unittest.skipUnless(os.getenv("SCHOOL_SCRAPING_TEST_DSN"), "Explicit isolated PostgreSQL DSN was not supplied")
class MemoryPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        cls.dsn = os.environ["SCHOOL_SCRAPING_TEST_DSN"]
        settings = conninfo_to_dict(cls.dsn)
        if settings.get("host") != "127.0.0.1" or settings.get("port") != "55439" or settings.get("user") != "memory_test_admin":
            raise RuntimeError("Memory integration tests require the explicitly isolated workspace cluster")
        cls.connect = staticmethod(lambda: psycopg.connect(cls.dsn))
        with cls.connect() as connection:
            migration = (Path(__file__).resolve().parents[1] / "database/migrations/001_scraping_memory.sql").read_text()
            connection.execute(migration)
            connection.execute(migration)
            connection.execute("CREATE TABLE IF NOT EXISTS school_scraping.memory_test_imports (identifier TEXT PRIMARY KEY)")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.identifier = "memory-" + uuid.uuid4().hex
        self.payload = {"source": "saras", "school_identifier": self.identifier, "academic_year": "",
                        "run_id": uuid.uuid4().hex, "scope_key": "isolated-memory-test", "content_hash": "a" * 64,
                        "raw_path": "data/CBSE_SARAS/test/schools/test.json", "snapshot_path": "immutable-test-snapshot.json"}

    def tearDown(self):
        self.temporary.cleanup()

    def handler(self, connection, payload):
        connection.execute("INSERT INTO school_scraping.memory_test_imports(identifier) VALUES (%s) ON CONFLICT DO NOTHING",
                           (payload["school_identifier"],))

    def memory(self, handler=None, enabled=True):
        return MemoryStore(self.root / "state", database_enabled=enabled, connect=self.connect,
                           import_handler=handler or self.handler, retry_seconds=0)

    def test_real_transaction_import_checkpoint_and_duplicate_replay(self):
        handler = Mock(side_effect=self.handler)
        memory = self.memory(handler, enabled=False)
        memory.emit("raw_saved", self.payload)
        imported = memory.emit("import_requested", self.payload)
        memory.database_enabled = True
        result = memory.replay()
        self.assertEqual(result["synced"], 2)
        self.assertEqual(handler.call_count, 1)
        checkpoint = memory.checkpoint("saras", self.identifier)
        self.assertTrue(checkpoint["imported"])
        self.assertTrue(checkpoint["last_successful_save"])
        memory.ack_file.write_text("{}", encoding="utf-8")  # Simulate crash after DB commit, before durable ack.
        replayed = memory.replay()
        self.assertEqual(replayed["duplicates"], 2)
        self.assertEqual(handler.call_count, 1)
        with self.connect() as connection:
            row = connection.execute("SELECT checkpoint_status,imported FROM school_scraping.scrape_checkpoints "
                                     "WHERE source='saras' AND school_identifier=%s", (self.identifier,)).fetchone()
            self.assertEqual(row, ("completed", True))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM school_scraping.scrape_events WHERE event_id=%s",
                                                (imported["event_id"],)).fetchone()[0], 1)

    def test_failed_import_rolls_back_event_and_school_writes_then_recovers(self):
        def fail_after_insert(connection, payload):
            self.handler(connection, payload)
            raise ValueError("password=not-for-logs")
        memory = self.memory(fail_after_insert, enabled=False)
        memory.emit("raw_saved", self.payload)
        event = memory.emit("import_requested", self.payload)
        memory.database_enabled = True
        result = memory.replay()
        self.assertEqual(result["failed"], 1)
        self.assertFalse(memory.checkpoint("saras", self.identifier)["imported"])
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM school_scraping.memory_test_imports WHERE identifier=%s",
                                                (self.identifier,)).fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM school_scraping.scrape_events WHERE event_id=%s",
                                                (event["event_id"],)).fetchone()[0], 0)
        self.assertNotIn("not-for-logs", memory.journal.read_text())
        memory.import_handler = self.handler
        memory.replay()
        self.assertTrue(memory.checkpoint("saras", self.identifier)["imported"])
        with self.connect() as connection:
            errors = connection.execute("SELECT resolution_status FROM school_scraping.scrape_errors WHERE payload->>'failed_event_id'=%s",
                                        (event["event_id"],)).fetchall()
            self.assertEqual(errors, [("resolved",)])

    def test_missing_import_handler_never_acknowledges_queued_import(self):
        memory = self.memory(enabled=False)
        memory.import_handler = None
        event = memory.emit("import_requested", self.payload)
        memory.database_enabled = True
        memory.replay()
        self.assertNotIn(event["event_id"], memory._acks())
        self.assertFalse(memory.checkpoint("saras", self.identifier)["imported"])

    def test_failed_import_does_not_starve_unrelated_events(self):
        def selective(connection, payload):
            if payload["school_identifier"] == self.identifier:
                raise ValueError("Intentional test rejection")
            self.handler(connection, payload)
        memory = self.memory(selective, enabled=False)
        memory.emit("import_requested", self.payload)
        other = {**self.payload, "school_identifier": self.identifier + "-other"}
        memory.emit("import_requested", other)
        memory.emit("run_finished", {**self.payload, "status": "partial_failure"})
        memory.database_enabled = True
        result = memory.replay()
        self.assertEqual(result["failed"], 1)
        self.assertTrue(memory.checkpoint("saras", other["school_identifier"])["imported"])
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT status FROM school_scraping.scrape_runs WHERE run_id=%s",
                                                (self.payload["run_id"],)).fetchone()[0], "partial_failure")

    def test_rejected_old_input_does_not_block_corrected_school_content(self):
        def corrected_only(connection, payload):
            if payload["content_hash"] == "a" * 64:
                raise ValueError("Historical invalid source data retained for review")
            self.handler(connection, payload)
        memory = self.memory(corrected_only, enabled=False)
        rejected = memory.emit("import_requested", self.payload)
        corrected = {**self.payload, "content_hash": "b" * 64}
        memory.emit("raw_saved", corrected)
        memory.emit("import_requested", corrected)
        memory.database_enabled = True
        result = memory.replay()
        self.assertEqual(result["failed"], 1)
        checkpoint = memory.checkpoint("saras", self.identifier)
        self.assertTrue(checkpoint["imported"])
        self.assertEqual(checkpoint["last_known_content_hash"], "b" * 64)
        self.assertNotIn(rejected["event_id"], memory._acks())
        self.assertTrue(any(event["event_id"] == rejected["event_id"] for event in memory._events()))

    def test_changed_content_cannot_inherit_previous_import_success(self):
        memory = self.memory()
        memory.emit("raw_saved", self.payload)
        memory.emit("import_requested", self.payload)
        original = memory.checkpoint("saras", self.identifier)
        memory.emit("raw_saved", {**self.payload, "content_hash": "b" * 64})
        changed = memory.checkpoint("saras", self.identifier)
        self.assertFalse(changed["imported"])
        self.assertEqual(changed["checkpoint_status"], "pending_import")
        self.assertEqual(changed["last_successful_save"], original["last_successful_save"])
        self.assertEqual(changed["last_known_content_hash"], "a" * 64)

    def test_filtered_details_do_not_claim_import_success(self):
        handler = Mock(side_effect=self.handler)
        memory = self.memory(handler)
        memory.emit("detail_filtered", {**self.payload, "task_status": "filtered",
                                         "filtered_path": "isolated-filtered-record.json"})
        checkpoint = memory.checkpoint("saras", self.identifier)
        self.assertEqual(checkpoint["checkpoint_status"], "filtered")
        self.assertFalse(checkpoint["imported"])
        self.assertIsNone(checkpoint.get("last_successful_save"))
        handler.assert_not_called()

    def test_unchanged_successful_content_stays_imported_after_detail_check(self):
        memory = self.memory()
        memory.emit("raw_saved", self.payload)
        memory.emit("import_requested", self.payload)
        original = memory.checkpoint("saras", self.identifier)
        memory.emit("raw_saved", self.payload)
        checked = memory.checkpoint("saras", self.identifier)
        self.assertTrue(checked["imported"])
        self.assertEqual(checked["checkpoint_status"], "completed")
        self.assertEqual(checked["last_successful_save"], original["last_successful_save"])
        own_tasks = [task for task in memory.tasks("saras")
                     if task.get("school_identifier") == self.identifier]
        self.assertTrue(own_tasks)
        self.assertTrue(all(task["task_status"] == "completed" for task in own_tasks))

    def test_database_memory_bootstraps_a_new_local_journal(self):
        memory = self.memory()
        memory.emit("raw_saved", self.payload)
        memory.emit("import_requested", self.payload)
        restored = MemoryStore(self.root / "new-machine-state", database_enabled=True,
                               connect=self.connect, import_handler=self.handler)
        self.assertTrue(restored.checkpoint("saras", self.identifier)["imported"])
        self.assertTrue(any(task.get("school_identifier") == self.identifier
                            for task in restored.tasks("saras")))

    def test_import_commit_resolves_detail_phase_errors_atomically(self):
        memory = self.memory(enabled=False)
        detail = {**self.payload, "task_id": self.identifier + ":detail", "phase": "detail"}
        memory.emit("task_started", detail)
        error = memory.emit("task_failed", {**detail, "error_category": "TimeoutError", "error_message": "Timed out"})
        memory.emit("raw_saved", detail)
        memory.emit("import_requested", {**self.payload, "task_id": self.identifier + ":import", "phase": "import"})
        memory.database_enabled = True
        self.assertEqual(memory.replay()["failed"], 0)
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT resolution_status FROM school_scraping.scrape_errors WHERE error_id=%s",
                                                (error["event_id"],)).fetchone()[0], "resolved")
            self.assertEqual(connection.execute("SELECT status FROM school_scraping.scrape_tasks WHERE task_id=%s",
                                                (detail["task_id"],)).fetchone()[0], "completed")

    def test_pending_import_with_later_same_hash_check_still_finishes(self):
        memory = self.memory(enabled=False)
        memory.emit("raw_saved", self.payload)
        memory.emit("import_requested", self.payload)
        memory.emit("raw_saved", self.payload)
        memory.database_enabled = True
        memory.replay()
        self.assertTrue(memory.checkpoint("saras", self.identifier)["imported"])
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT imported FROM school_scraping.scrape_checkpoints WHERE school_identifier=%s",
                                                (self.identifier,)).fetchone()[0], True)

    def test_success_resolves_previous_task_errors(self):
        memory = self.memory()
        memory.emit("task_started", {**self.payload, "attempt_count": 2})
        failed = memory.emit("task_failed", {**self.payload, "error_category": "TimeoutError", "error_message": "Timed out", "attempt_count": 2})
        memory.emit("task_completed", self.payload)
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT resolution_status,retry_count FROM school_scraping.scrape_errors WHERE error_id=%s",
                                                (failed["event_id"],)).fetchone(), ("resolved", 2))


if __name__ == "__main__":
    unittest.main()
