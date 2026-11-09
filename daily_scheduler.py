"""Daily, timezone-aware scheduling for the two dynamic school scrapers.

Importing this module performs no I/O and never launches a job.
"""

import argparse
import copy
import json
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from school_scraping.common import SourceLock
from school_scraping.config import (
    ConfigurationError, add_arguments, explicit_options, load_config, resolve_source_args,
)


PROJECT_DIR = Path(__file__).resolve().parent
SCHEDULE_TIME = "02:00"
SCRIPTS = {"udise": "udise_dynamic_scraper.py", "saras": "saras_dynamic_scraper.py"}


def schedule_time(value):
    """Validate the wall-clock time before any job can be scheduled."""
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as error:
        raise argparse.ArgumentTypeError("Use a valid 24-hour time, such as 02:00.") from error
    return parsed.strftime("%H:%M")


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Must be greater than zero.")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    parser.add_argument("--time", type=schedule_time, default=SCHEDULE_TIME,
                        help="Daily local time in HH:MM format (default: 02:00).")
    parser.add_argument("--timezone", default="Asia/Kolkata",
                        help="IANA timezone (default: Asia/Kolkata).")
    parser.add_argument("--source", choices=["udise", "saras", "both"], default="both")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--state", action="append", default=[],
                       help="Override configured scope with state names; repeat for multiple states.")
    scope.add_argument("--all-states", action="store_true",
                       help="Explicitly request every state available from each source.")
    parser.add_argument("--district", action="append", default=[], help="District name; repeat as needed.")
    parser.add_argument("--pincode", action="append", default=[], help="Six-digit pincode; repeat as needed.")
    parser.add_argument("--academic-year", default="2025-26", help="UDISE academic year (default: 2025-26).")
    parser.add_argument("--year-id", type=int, help="Verified UDISE year identifier; optional if discovered.")
    parser.add_argument("--udise-school-list", "--school-list", dest="school_list", action="append", default=[],
                        help="Authorized saved UDISE school-list JSON; repeat to add lists.")
    parser.add_argument("--udise-headers-file", type=Path,
                        help="Local JSON file containing authorized UDISE HTTP headers; never logged.")
    parser.add_argument("--manual-captcha", type=positive_int,
                        help="Seconds to allow manual browser CAPTCHA completion in either scraper.")
    parser.add_argument("--headless", action="store_true", help="Run SARAS browser without a visible window.")
    parser.add_argument("--limit", type=positive_int, help="Maximum schools per source per run.")
    parser.add_argument("--refresh", action="store_true", help="Check for intentional source updates.")
    parser.add_argument("--delay", type=float, default=0.5, help="Minimum delay between source requests.")
    parser.add_argument("--retries", type=int, default=3, help="Bounded transient source retries.")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_DIR / "data")
    parser.add_argument("--report-dir", type=Path, default=PROJECT_DIR / "reports" / "scraping")
    parser.add_argument("--log-file", type=Path, default=PROJECT_DIR / "logs" / "daily_scheduler.log")
    parser.add_argument("--once", action="store_true", help="Run one cycle immediately, then exit.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show schedule and commands without launching jobs or writing files.")
    return parser


def get_next_run(schedule_at=SCHEDULE_TIME, timezone="Asia/Kolkata", now=None, last_run_date=None):
    """Find the next real wall-clock occurrence, including DST gaps and folds."""
    zone = ZoneInfo(timezone)
    now = now or datetime.now(zone)
    if now.tzinfo is None:
        raise ValueError("now must contain timezone information")
    now = now.astimezone(zone)
    hour, minute = map(int, schedule_time(schedule_at).split(":"))
    # UTC round-trip rejects nonexistent times during a DST spring jump.
    utc = ZoneInfo("UTC")
    for offset in range(3):
        day = now.date() + timedelta(days=offset)
        if last_run_date is not None and day <= last_run_date:
            continue
        candidates = []
        for fold in (0, 1):
            local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=fold)
            roundtrip = local.astimezone(utc).astimezone(zone)
            if (roundtrip.date(), roundtrip.hour, roundtrip.minute) != (day, hour, minute):
                continue
            if local.timestamp() > now.timestamp():
                candidates.append(local)
        if candidates:
            return min(candidates, key=lambda candidate: candidate.timestamp())
    raise ValueError("Could not find the next valid scheduled time")


def build_commands(args):
    if hasattr(args, "resolved_jobs"):
        return [(source, _job_command(args, source, job)) for source, job in args.resolved_jobs]
    commands = []
    sources = list(SCRIPTS) if args.source == "both" else [args.source]
    for source in sources:
        command = [sys.executable, str(PROJECT_DIR / SCRIPTS[source])]
        for state in args.state:
            command.extend(["--state", state])
        if args.all_states:
            command.append("--all-states")
        for option in ("district", "pincode"):
            for value in getattr(args, option):
                command.extend([f"--{option}", value])
        for option in ("data_dir", "report_dir", "limit"):
            value = getattr(args, option)
            if value is not None:
                command.extend(["--" + option.replace("_", "-"), str(value)])
        if args.refresh:
            command.append("--refresh")
        if args.manual_captcha is not None:
            command.extend(["--manual-captcha", str(args.manual_captcha)])
        if source == "udise":
            command.extend(["--academic-year", args.academic_year])
            if args.year_id is not None:
                command.extend(["--year-id", str(args.year_id)])
            for school_list in args.school_list:
                command.extend(["--school-list", school_list])
            if args.udise_headers_file is not None:
                command.extend(["--headers-file", str(args.udise_headers_file)])
        elif args.headless:
            command.append("--headless")
        commands.append((source, command))
    return commands


def prepare_args(args, argv=None):
    """Resolve each enabled location independently using the shared configuration."""
    config = load_config(args.config)
    explicit = explicit_options(argv)
    if "time" not in explicit:
        args.time = config["scheduler"]["time"]
    if "timezone" not in explicit:
        args.timezone = config["scheduler"]["timezone"]
    sources = (["udise", "saras"] if args.source == "both" else [args.source]) if "source" in explicit else config["scheduler"]["sources"]
    if "source" not in explicit and len(sources) == 1:
        args.source = sources[0]
    args.scheduler_enabled = config["scheduler"]["enabled"]
    args.explicit_scope = bool(explicit & {"state", "all_states"})
    args.resolved_jobs = []
    for source in sources:
        source_args = copy.copy(args)
        source_args.headers_file = args.udise_headers_file
        jobs = resolve_source_args(source_args, source, argv)
        args.resolved_jobs.extend((source, job) for job in jobs)
    return args


def _job_command(args, source, job):
    command = [sys.executable, str(PROJECT_DIR / SCRIPTS[source]), "--config", str(job.config)]
    if args.explicit_scope:
        for state in job.state:
            command.extend(["--state", state])
        if job.all_states:
            command.append("--all-states")
    elif job.location_name != "maintenance":
        command.extend(["--location", job.location_name])
    # Forward explicit resolved filters and settings so parent/child scope keys agree.
    for option in ("district", "pincode"):
        for value in getattr(job, option):
            command.extend([f"--{option}", str(value)])
    for option in ("data_dir", "report_dir", "limit", "state_dir", "refresh_days",
                   "check_interval_hours", "retry_delay_minutes", "max_task_attempts", "delay", "retries"):
        value = getattr(job, option, None)
        if value is not None:
            command.extend(["--" + option.replace("_", "-"), str(value)])
    command.append("--database" if job.database else "--no-database")
    for option in ("refresh", "retry_failed", "sync_pending"):
        if getattr(job, option, False):
            command.append("--" + option.replace("_", "-"))
    if job.manual_captcha is not None:
        command.extend(["--manual-captcha", str(job.manual_captcha)])
    if source == "udise":
        command.extend(["--academic-year", job.academic_year])
        if job.year_id is not None:
            command.extend(["--year-id", str(job.year_id)])
        for path in job.school_list:
            command.extend(["--school-list", str(path)])
        if job.headers_file is not None:
            command.extend(["--headers-file", str(job.headers_file)])
    elif job.headless:
        command.append("--headless")
    return command


class RunLog:
    """Append structured events; preserve all earlier scheduler log content."""

    def __init__(self, path, timezone):
        self.path = Path(path)
        self.zone = ZoneInfo(timezone)

    def __call__(self, event, **fields):
        from school_scraping.memory import sanitize
        record = {"timestamp": datetime.now(self.zone).isoformat(), "event": event, **fields}
        line = json.dumps(sanitize(record), ensure_ascii=False)
        print(line, flush=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


def stop_process(process):
    """Stop a child and reap it so shutdown cannot leave a running scrape."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ValueError):
            process.terminate()
    else:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _memory(job):
    # Imports are deferred so --help and --dry-run cannot initialize storage/DBs.
    from school_scraping.memory import MemoryStore
    from database.scrape_pipeline import import_scraped_record
    return MemoryStore(job.state_dir, database_enabled=job.database,
                       import_handler=import_scraped_record,
                       retry_seconds=job.retry_delay_minutes * 60)


def _timestamp(value):
    if not value:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def location_due(source, job, memory, now=None):
    """Use detail/import checkpoints, never listing success, to decide a run."""
    now = now or datetime.now(timezone.utc)
    if job.sync_pending:
        return True, "pending_sync_requested"
    if job.refresh:
        return True, "refresh_requested"
    year = job.academic_year if source == "udise" else ""
    tasks = memory.tasks(source, scope_key=job.scope_key, academic_year=year)
    manual_pending = False
    waiting_failure = False
    for task in tasks:
        status = task.get("task_status", task.get("status", ""))
        if status in {"completed", "skipped", "unchanged", "filtered", "pending_import", "import_pending"}:
            continue
        manual = status in {"manual_intervention", "paused", "blocked"} or any(
            marker in str(task.get("error_category", "")).casefold()
            for marker in ("captcha", "manual", "authentication", "access_blocked"))
        if manual and not job.retry_failed:
            manual_pending = True
            continue
        if status not in {"failed", "pending", "running", "retry", "retry_wait", "manual_intervention", "paused", "blocked"}:
            continue
        waiting_failure = True
        if int(task.get("attempt_count") or 0) >= job.max_task_attempts:
            continue
        next_retry = _timestamp(task.get("next_retry_at"))
        if next_retry is None or next_retry <= now:
            return True, "eligible_failed_task"
    if job.retry_failed:
        return True, "failed_task_retry_requested"
    if manual_pending:
        return False, "manual_intervention_required"
    if waiting_failure:
        return False, "failed_task_backoff_or_attempt_limit"
    checkpoint = memory.checkpoint(source, school_identifier="", academic_year=year,
                                   scope_key=job.scope_key)
    next_check = _timestamp((checkpoint or {}).get("next_check_at"))
    if next_check is not None and next_check > now:
        return False, "location_not_due"
    return True, "location_due"


def run_daily_job(args, stop_event, log):
    """Run each source independently; source failure does not stop its peer."""
    results = []
    run_id = str(uuid.uuid4())
    run_started = datetime.now(timezone.utc).isoformat()
    run_clock = time.monotonic()
    memory_by_path = {}
    scheduler_memory = None
    if hasattr(args, "resolved_jobs") and args.resolved_jobs:
        for source, job in args.resolved_jobs:
            key = (str(job.state_dir), job.database)
            if key not in memory_by_path:
                memory_by_path[key] = _memory(job)
                if job.database:
                    memory_by_path[key].replay(respect_backoff=True)
            scheduler_memory = scheduler_memory or memory_by_path[key]
        scheduler_memory.emit("run_started", {"run_id": run_id, "source": "scheduler",
                                               "started_at": run_started, "status": "running",
                                               "scope": {"locations": [job.location_name for _, job in args.resolved_jobs]}})
    for index, (source, command) in enumerate(build_commands(args)):
        if stop_event.is_set():
            break
        job = args.resolved_jobs[index][1] if hasattr(args, "resolved_jobs") else None
        if job is not None:
            memory = memory_by_path[(str(job.state_dir), job.database)]
            due, reason = location_due(source, job, memory)
            if not due:
                result = {"source": source, "location": job.location_name, "status": "skipped",
                          "reason": reason, "duration_seconds": 0, "exit_code": None, "error": None}
                log("job_skipped", **result)
                memory.emit("event", {"run_id": run_id, "source": "scheduler", "kind": "location_skipped",
                                      "scope_key": job.scope_key, "location": job.location_name, "reason": reason})
                results.append(result)
                continue
        started = datetime.now(ZoneInfo(args.timezone)).isoformat()
        clock_start = time.monotonic()
        log("job_started", source=source, started_at=started)
        process = None
        error = None
        returncode = None
        try:
            options = {"cwd": str(PROJECT_DIR)}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            process = subprocess.Popen(command, **options)
            while process.poll() is None:
                if stop_event.wait(0.25):
                    stop_process(process)
                    break
            returncode = process.returncode
        except Exception as exception:
            error = f"{type(exception).__name__}: {exception}"
            if process is not None:
                stop_process(process)
        status = "cancelled" if stop_event.is_set() else "success" if returncode == 0 else "failed"
        result = {"source": source, "status": status, "started_at": started,
                  "ended_at": datetime.now(ZoneInfo(args.timezone)).isoformat(),
                  "duration_seconds": round(time.monotonic() - clock_start, 3),
                  "exit_code": returncode, "error": error}
        if job is not None:
            result["location"] = job.location_name
        if status == "failed" and not error:
            result["error"] = f"Scraper exited with code {returncode}; see its run report."
        log("job_finished", **result)
        if scheduler_memory is not None:
            scheduler_memory.emit("event", {**result, "child_source": source, "run_id": run_id,
                                            "source": "scheduler", "kind": "child_finished"})
        results.append(result)
    summary = {"run_id": run_id, "source": "scheduler", "started_at": run_started,
               "finished_at": datetime.now(timezone.utc).isoformat(),
               "status": "interrupted" if stop_event.is_set() else "failed" if any(result["status"] == "failed" for result in results) else "completed",
               "duration_seconds": round(time.monotonic() - run_clock, 3),
               "counts": {status: sum(result["status"] == status for result in results)
                          for status in ("success", "failed", "skipped", "cancelled")}}
    log("cycle_finished", **summary)
    if scheduler_memory is not None:
        scheduler_memory.emit("run_finished", summary)
    return results


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        prepare_args(args, argv)
    except ConfigurationError as error:
        parser.error(str(error))
    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        parser.error("Unknown timezone; use an IANA name, such as Asia/Kolkata. Install tzdata if needed.")
    for value in args.pincode:
        for code in value.split(","):
            if len(code.strip()) != 6 or not code.strip().isdigit() or code.strip().startswith("0"):
                parser.error("Pincodes must contain six digits and cannot begin with zero.")
    if args.dry_run:
        print(json.dumps({"dry_run": True, "source": args.source, "time": args.time,
                          "timezone": args.timezone,
                          "next_run": get_next_run(args.time, args.timezone).isoformat(),
                          "scope_configured": bool(args.resolved_jobs),
                          "scheduler_enabled": args.scheduler_enabled,
                          "locations": [{"source": source, "name": job.location_name,
                                         "scope_key": job.scope_key, "state": job.state,
                                         "districts": job.district, "pincodes": job.pincode,
                                         "database_enabled": job.database}
                                        for source, job in args.resolved_jobs],
                          "commands": [{"source": source, "argv": command}
                                       for source, command in build_commands(args)]}, indent=2))
        return 0
    if not args.scheduler_enabled or not args.resolved_jobs:
        print("Scheduler is idle: enable a configured location or choose --state NAME. No jobs were launched.")
        return 0
    stop_event = threading.Event()

    def request_stop(signum, frame):
        stop_event.set()

    previous_handlers = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, request_stop)
    log = RunLog(args.log_file, args.timezone)
    try:
        with SourceLock(args.data_dir, "scheduler"):
            log("scheduler_started", time=args.time, timezone=args.timezone, source=args.source)
            if args.once:
                results = run_daily_job(args, stop_event, log)
                return 130 if stop_event.is_set() else int(any(item["status"] not in {"success", "skipped"} for item in results))
            last_run_date = None
            while not stop_event.is_set():
                next_run = get_next_run(args.time, args.timezone, last_run_date=last_run_date)
                log("scheduler_waiting", next_run=next_run.isoformat())
                while not stop_event.is_set():
                    remaining = next_run.timestamp() - time.time()
                    if remaining <= 0:
                        break
                    stop_event.wait(min(remaining, 30))
                if not stop_event.is_set():
                    # A repeated DST wall time must not launch a second daily cycle.
                    last_run_date = next_run.date()
                    run_daily_job(args, stop_event, log)
            return 0
    except (OSError, RuntimeError) as error:
        log("scheduler_error", error=f"{type(error).__name__}: {error}")
        return 1
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        log("scheduler_stopped")


if __name__ == "__main__":
    raise SystemExit(main())
