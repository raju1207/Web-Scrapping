import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path


# ============================================================
# DAILY SCHEDULER CONFIGURATION
# ============================================================

# Change this to whatever time you want.
#
# Example:
# "02:00" = 2:00 AM
# "06:30" = 6:30 AM
# "21:00" = 9:00 PM
#
SCHEDULE_TIME = "02:00"

PROJECT_DIR = Path(__file__).resolve().parent

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / "daily_scheduler.log"


# ============================================================
# SCRIPTS TO RUN
# ============================================================

SCRIPTS = [
    "scraper.py",
    "saras_scraper.py",
    "saras_scraper_mumbai.py",
]


# ============================================================
# LOGGING
# ============================================================

def write_log(message):
    timestamp = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    line = f"[{timestamp}] {message}"

    print(line)

    with open(
        LOG_FILE,
        "a",
        encoding="utf-8"
    ) as file:
        file.write(line + "\n")


# ============================================================
# RUN ONE PYTHON SCRIPT
# ============================================================

def run_script(script_name):

    script_path = PROJECT_DIR / script_name

    if not script_path.exists():

        write_log(
            f"ERROR: Script not found: {script_name}"
        )

        return False

    write_log(
        f"STARTING: {script_name}"
    )

    start_time = time.time()

    try:

        result = subprocess.run(
            [
                sys.executable,
                str(script_path)
            ],
            cwd=str(PROJECT_DIR),
            check=False
        )

        elapsed = time.time() - start_time

        if result.returncode == 0:

            write_log(
                f"COMPLETED: {script_name} "
                f"(exit code 0, "
                f"{elapsed:.1f} seconds)"
            )

            return True

        write_log(
            f"FAILED: {script_name} "
            f"(exit code {result.returncode}, "
            f"{elapsed:.1f} seconds)"
        )

        return False

    except Exception as error:

        write_log(
            f"ERROR running {script_name}: "
            f"{error}"
        )

        return False


# ============================================================
# RUN DAILY JOB
# ============================================================

def run_daily_job():

    write_log("")
    write_log("=" * 70)
    write_log("DAILY WEB SCRAPING JOB STARTED")
    write_log("=" * 70)

    successful = 0
    failed = 0

    for script in SCRIPTS:

        result = run_script(script)

        if result:
            successful += 1
        else:
            failed += 1

    write_log("")
    write_log(
        f"Daily scraping finished. "
        f"Successful: {successful}, "
        f"Failed: {failed}"
    )

    write_log("=" * 70)
    write_log("DAILY WEB SCRAPING JOB FINISHED")
    write_log("=" * 70)
    write_log("")


# ============================================================
# CALCULATE NEXT RUN
# ============================================================

def get_next_run():

    hour, minute = map(
        int,
        SCHEDULE_TIME.split(":")
    )

    now = datetime.now()

    next_run = now.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    )

    # If today's scheduled time has already passed,
    # schedule for tomorrow.
    if next_run <= now:

        next_run += timedelta(
            days=1
        )

    return next_run


# ============================================================
# MAIN SCHEDULER LOOP
# ============================================================

def main():

    write_log("")
    write_log("=" * 70)
    write_log("DAILY SCHEDULER STARTED")
    write_log("=" * 70)

    write_log(
        f"Project directory: {PROJECT_DIR}"
    )

    write_log(
        f"Scheduled time: {SCHEDULE_TIME}"
    )

    write_log(
        "Scheduler is running. "
        "Keep this process running."
    )

    while True:

        next_run = get_next_run()

        write_log(
            f"Next run: "
            f"{next_run.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        while True:

            now = datetime.now()

            remaining = (
                next_run - now
            ).total_seconds()

            if remaining <= 0:
                break

            # Sleep in smaller intervals so the
            # scheduler remains responsive.
            time.sleep(
                min(remaining, 60)
            )

        # Run today's scraping job.
        run_daily_job()

        # Small protection delay before calculating
        # the next day's schedule.
        time.sleep(5)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:

        main()

    except KeyboardInterrupt:

        write_log(
            "Scheduler stopped by user."
        )

        print(
            "\nScheduler stopped."
        )