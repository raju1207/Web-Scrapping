import contextlib
import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import daily_scheduler as scheduler


class SchedulerTests(unittest.TestCase):
    def test_timezone_rollover(self):
        zone = ZoneInfo("Asia/Kolkata")
        before = datetime(2026, 11, 9, 1, 59, tzinfo=zone)
        after = datetime(2026, 11, 9, 2, 0, tzinfo=zone)
        self.assertEqual(scheduler.get_next_run(now=before).day, 9)
        self.assertEqual(scheduler.get_next_run(now=after).day, 10)
        self.assertEqual(scheduler.get_next_run(now=before).utcoffset().total_seconds(), 19800)

    def test_pending_import_and_resolved_captcha_do_not_override_future_scope(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        job = SimpleNamespace(sync_pending=False, refresh=False, academic_year="2025-26",
                              scope_key="test", retry_failed=False, max_task_attempts=5)
        memory = Mock()
        memory.tasks.return_value = [
            {"task_status": "pending_import", "attempt_count": 0},
            {"task_status": "completed", "error_category": "captcha_or_authentication"},
        ]
        memory.checkpoint.return_value = {"next_check_at": "2099-01-01T00:00:00+00:00"}
        self.assertEqual(scheduler.location_due("saras", job, memory), (False, "location_not_due"))

    def test_exhausted_failure_without_scope_checkpoint_does_not_launch(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        job = SimpleNamespace(sync_pending=False, refresh=False, academic_year="2025-26",
                              scope_key="test", retry_failed=False, max_task_attempts=5)
        memory = Mock()
        memory.tasks.return_value = [{"task_status": "failed", "attempt_count": 5}]
        memory.checkpoint.return_value = None
        self.assertEqual(scheduler.location_due("saras", job, memory),
                         (False, "failed_task_backoff_or_attempt_limit"))

    def test_dst_gap_is_not_scheduled(self):
        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 8, 0, 0, tzinfo=zone)
        next_run = scheduler.get_next_run("02:30", "America/New_York", now)
        self.assertEqual(next_run.day, 9)

    def test_dst_fold_selects_remaining_occurrence(self):
        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
        next_run = scheduler.get_next_run("01:30", "America/New_York", now)
        self.assertEqual(next_run.day, 1)
        self.assertEqual(next_run.fold, 1)

    def test_daily_job_does_not_repeat_on_second_dst_fold(self):
        zone = ZoneInfo("America/New_York")
        now = datetime(2026, 11, 1, 1, 45, tzinfo=zone, fold=0)
        next_run = scheduler.get_next_run("01:30", "America/New_York", now,
                                          last_run_date=now.date())
        self.assertEqual(next_run.day, 2)
        self.assertEqual(next_run.hour, 1)
        self.assertEqual(next_run.minute, 30)

    def test_commands_only_target_new_scrapers_and_udise_year(self):
        args = scheduler.build_parser().parse_args([
            "--state", "Punjab", "--state", "Haryana", "--pincode", "134001",
            "--academic-year", "2026-27", "--year-id", "0", "--limit", "1"])
        commands = scheduler.build_commands(args)
        self.assertEqual([source for source, _ in commands], ["udise", "saras"])
        self.assertTrue(commands[0][1][1].endswith("udise_dynamic_scraper.py"))
        self.assertTrue(commands[1][1][1].endswith("saras_dynamic_scraper.py"))
        self.assertIn("--year-id", commands[0][1])
        self.assertIn("0", commands[0][1])
        self.assertNotIn("--year-id", commands[1][1])
        self.assertNotIn("--academic-year", commands[1][1])

    def test_dry_run_has_no_side_effects_or_job_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            absent = Path(folder) / "must_not_exist"
            output = io.StringIO()
            with patch.object(scheduler.subprocess, "Popen") as popen, \
                    patch.object(scheduler, "SourceLock") as lock, contextlib.redirect_stdout(output):
                code = scheduler.main(["--dry-run", "--state", "Punjab", "--data-dir", str(absent)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["dry_run"])
            self.assertFalse(absent.exists())
            popen.assert_not_called()
            lock.assert_not_called()

    def test_disabled_default_locations_leave_scheduler_idle(self):
        with patch.object(scheduler.subprocess, "Popen") as popen, \
                patch.object(scheduler, "_memory") as memory, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scheduler.main(["--once"]), 0)
        popen.assert_not_called()
        memory.assert_not_called()

    def test_source_failure_does_not_prevent_second_source(self):
        args = scheduler.build_parser().parse_args(["--state", "Punjab"])
        events = []

        class FinishedProcess:
            def __init__(self, returncode):
                self.returncode = returncode

            def poll(self):
                return self.returncode

        with patch.object(scheduler.subprocess, "Popen",
                          side_effect=[FinishedProcess(3), FinishedProcess(0)]) as popen:
            results = scheduler.run_daily_job(args, threading.Event(),
                                              lambda event, **data: events.append((event, data)))
        self.assertEqual(popen.call_count, 2)
        self.assertEqual([result["status"] for result in results], ["failed", "success"])
        self.assertIn("duration_seconds", results[0])
        self.assertTrue(results[0]["error"])

    def test_shutdown_stops_child_and_skips_following_source(self):
        args = scheduler.build_parser().parse_args(["--state", "Punjab"])
        stop = threading.Event()

        class RunningProcess:
            returncode = None

            def poll(self):
                stop.set()
                return None

        def stop_child(process):
            process.returncode = 130

        with patch.object(scheduler.subprocess, "Popen", return_value=RunningProcess()) as popen, \
                patch.object(scheduler, "stop_process", side_effect=stop_child) as shutdown:
            results = scheduler.run_daily_job(args, stop, lambda *args, **kwargs: None)
        self.assertEqual(popen.call_count, 1)
        shutdown.assert_called_once()
        self.assertEqual(results[0]["status"], "cancelled")

    def prepared(self, root, *extra):
        argv = ["--state", "Punjab", "--source", "udise", "--data-dir", str(root), *extra]
        return scheduler.prepare_args(scheduler.build_parser().parse_args(argv), argv)

    def test_config_locations_remain_paired_in_child_commands(self):
        with tempfile.TemporaryDirectory() as root:
            config = json.loads(Path(scheduler.load_config()["_path"]).read_text())
            config["scheduler"]["sources"] = ["udise"]
            for location in config["sources"]["udise"]["locations"]:
                location["enabled"] = True
            path = Path(root) / "config.json"
            path.write_text(json.dumps(config))
            argv = ["--config", str(path), "--dry-run"]
            args = scheduler.prepare_args(scheduler.build_parser().parse_args(argv), argv)
            commands = scheduler.build_commands(args)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][1][commands[0][1].index("--location") + 1], "punjab")
        self.assertIn("140001", commands[0][1])
        self.assertNotIn("134001", commands[0][1])
        self.assertEqual(commands[1][1][commands[1][1].index("--location") + 1], "haryana")
        self.assertIn("134001", commands[1][1])
        self.assertIn("--no-database", commands[0][1])

    def test_future_checkpoint_skips_child_and_records_scheduler_summary(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.prepared(root)
            memory = Mock()
            memory.tasks.return_value = []
            memory.checkpoint.return_value = {"checkpoint_status": "completed",
                "next_check_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
            with patch.object(scheduler, "_memory", return_value=memory), \
                    patch.object(scheduler.subprocess, "Popen") as popen:
                results = scheduler.run_daily_job(args, threading.Event(), lambda *args, **kwargs: None)
            self.assertEqual(results[0]["status"], "skipped")
            self.assertEqual(results[0]["reason"], "location_not_due")
            popen.assert_not_called()
            memory.replay.assert_not_called()
            self.assertEqual(memory.emit.call_args_list[0].args[0], "run_started")
            self.assertEqual(memory.emit.call_args_list[-1].args[0], "run_finished")
            self.assertEqual(memory.emit.call_args_list[-1].args[1]["counts"]["skipped"], 1)

    def test_eligible_failure_runs_before_location_checkpoint_is_due(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.prepared(root)
            memory = Mock()
            memory.tasks.return_value = [{"task_status": "failed", "attempt_count": 1,
                                         "next_retry_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()}]
            memory.checkpoint.return_value = {"next_check_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
            process = Mock(returncode=0)
            process.poll.return_value = 0
            with patch.object(scheduler, "_memory", return_value=memory), \
                    patch.object(scheduler.subprocess, "Popen", return_value=process) as popen:
                results = scheduler.run_daily_job(args, threading.Event(), lambda *args, **kwargs: None)
            self.assertEqual(results[0]["status"], "success")
            popen.assert_called_once()
            memory.tasks.assert_called_once_with("udise", scope_key=args.resolved_jobs[0][1].scope_key,
                                                  academic_year="2025-26")

    def test_manual_tasks_wait_for_explicit_user_retry(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.prepared(root)
            memory = Mock()
            memory.tasks.return_value = [{"task_status": "manual_intervention", "attempt_count": 1,
                                         "next_retry_at": None, "error_category": "captcha"}]
            memory.checkpoint.return_value = None
            due, reason = scheduler.location_due("udise", args.resolved_jobs[0][1], memory)
            self.assertFalse(due)
            self.assertEqual(reason, "manual_intervention_required")
            args.resolved_jobs[0][1].retry_failed = True
            self.assertTrue(scheduler.location_due("udise", args.resolved_jobs[0][1], memory)[0])

    def test_retry_delay_and_max_attempts_are_respected_automatically(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.prepared(root)
            memory = Mock()
            memory.checkpoint.return_value = {"next_check_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
            for task in [{"task_status": "failed", "attempt_count": 5, "next_retry_at": None},
                         {"task_status": "failed", "attempt_count": 1,
                          "next_retry_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()}]:
                with self.subTest(task=task):
                    memory.tasks.return_value = [task]
                    self.assertFalse(scheduler.location_due("udise", args.resolved_jobs[0][1], memory)[0])

    def test_enabled_database_replay_precedes_due_check(self):
        with tempfile.TemporaryDirectory() as root:
            args = self.prepared(root, "--database")
            memory = Mock()
            memory.tasks.return_value = []
            memory.checkpoint.return_value = {"next_check_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}
            with patch.object(scheduler, "_memory", return_value=memory), \
                    patch.object(scheduler.subprocess, "Popen") as popen:
                scheduler.run_daily_job(args, threading.Event(), lambda *args, **kwargs: None)
            memory.replay.assert_called_once()
            calls = [call[0] for call in memory.mock_calls]
            self.assertLess(calls.index("replay"), calls.index("checkpoint"))
            popen.assert_not_called()

    def test_scheduler_persists_local_history_with_database_disabled(self):
        from school_scraping.memory import MemoryStore
        with tempfile.TemporaryDirectory() as root:
            args = self.prepared(root)
            process = Mock(returncode=0)
            process.poll.return_value = 0
            with patch.object(scheduler.subprocess, "Popen", return_value=process), \
                    patch.object(MemoryStore, "_connection") as connect:
                results = scheduler.run_daily_job(args, threading.Event(), lambda *args, **kwargs: None)
            self.assertEqual(results[0]["status"], "success")
            journal = Path(root) / ".scraping_state/events.jsonl"
            events = [json.loads(line) for line in journal.read_text().splitlines()]
            self.assertEqual([event["kind"] for event in events], ["run_started", "event", "run_finished"])
            self.assertTrue(all(event["payload"]["source"] == "scheduler" for event in events))
            self.assertEqual(events[-1]["payload"]["counts"]["success"], 1)
            self.assertEqual(len({event["payload"]["run_id"] for event in events}), 1)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
