
import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import scraper


# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = Path("data/Delhi/2025-26")
DEFAULT_UDISE_CODE = "07060214901"

BACKUP_DIR = Path("data/refresh_backups/UDISE")
REPORT_DIR = Path("reports/udise_refresh")

REQUEST_DELAY = getattr(scraper, "REQUEST_DELAY", 0.4)

# Raw JSON section -> scraper API function
FETCHERS = {
    "school_profile": ("profile", scraper.fetch_profile),
    "student_teacher_statistics": (
        "student_teacher",
        scraper.fetch_student_teacher,
    ),
    "infrastructure_facilities": (
        "facilities",
        scraper.fetch_facilities,
    ),
    "report_card": ("report_card", scraper.fetch_report_card),
}


def short_value(value, limit=180):
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "... [truncated]"


def compare_values(old, new, path=""):
    """Return field-level differences between JSON-compatible values."""
    differences = []

    if isinstance(old, dict) and isinstance(new, dict):
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else key

            if key not in old:
                differences.append((child, "<MISSING>", new[key]))
            elif key not in new:
                differences.append((child, old[key], "<MISSING>"))
            else:
                differences.extend(
                    compare_values(old[key], new[key], child)
                )

    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            differences.append(
                (f"{path} [list length]", len(old), len(new))
            )

        for index, (left, right) in enumerate(zip(old, new)):
            differences.extend(
                compare_values(left, right, f"{path}[{index}]")
            )

    elif old != new:
        differences.append((path or "$", old, new))

    return differences


def fetch_section(fetch_function, *args):
    """Fetch one section without treating failed requests as empty data."""
    try:
        result = fetch_function(*args)
    except Exception as error:
        return {
            "ok": False,
            "error": f"{type(error).__name__}: {error}",
        }, None

    if not isinstance(result, dict) or not result.get("success"):
        error = (
            result.get("error", "Unknown API error")
            if isinstance(result, dict)
            else "Unexpected API result format"
        )
        return {"ok": False, "error": str(error)}, None

    extracted = scraper.extract_api_data(result)

    if extracted is None:
        return {
            "ok": False,
            "error": "API returned no extractable data; saved section preserved.",
        }, None

    return {
        "ok": True,
        "url": result.get("url"),
        "result": result,
    }, extracted


def find_saved_file(udise_code):
    matches = list(DATA_DIR.glob(f"*_{udise_code}.json"))

    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one file for UDISE {udise_code}, "
            f"but found {len(matches)} in {DATA_DIR}."
        )

    return matches[0]


def write_json_atomically(path, data):
    """Write to a temporary file, then replace the destination."""
    temp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as file:
            json.dump(data, file, indent=4, ensure_ascii=False)
            file.write("\n")

        temp_path.replace(path)

    finally:
        if temp_path.exists():
            temp_path.unlink()


def main():
    parser = argparse.ArgumentParser(
        description="Safely refresh one UDISE+ raw JSON record."
    )
    parser.add_argument(
        "--udise",
        default=DEFAULT_UDISE_CODE,
        help="UDISE code to refresh (default: operational test school).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply successful changes after creating a backup.",
    )
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY RUN"
    print("=" * 72)
    print("UDISE+ SAFE DAILY REFRESH")
    print(f"Mode: {mode}")
    print(f"UDISE code: {args.udise}")
    print("=" * 72)

    source_file = find_saved_file(args.udise)

    with source_file.open("r", encoding="utf-8") as file:
        original = json.load(file)

    if not isinstance(original, dict):
        raise RuntimeError("Saved JSON is not a JSON object. No changes made.")

    metadata = original.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("Saved metadata is invalid. No changes made.")

    saved_code = str(metadata.get("udise_code", ""))
    if saved_code != args.udise:
        raise RuntimeError(
            f"UDISE mismatch: file contains {saved_code}, "
            f"requested {args.udise}. No changes made."
        )

    candidate = dict(original)
    api_status = dict(original.get("_api_status") or {})
    report_sections = {}
    changed_fields = []
    successful_sections = 0

    print(f"File: {source_file}")
    print(f"School: {metadata.get('school_name', 'Unknown')}")
    print()

    # Fetch the four school-detail sections.
    for section, (status_key, fetch_function) in FETCHERS.items():
        print(f"Fetching {section}...")

        status, fresh = fetch_section(fetch_function, args.udise)

        if not status["ok"]:
            print(f"  FAILED: {status['error']}")
            report_sections[section] = {
                "status": "failed",
                "error": status["error"],
                "saved_data_preserved": True,
            }
        else:
            successful_sections += 1
            old = original.get(section)
            differences = compare_values(old, fresh)

            print(f"  API success; differences: {len(differences)}")

            report_sections[section] = {
                "status": "success",
                "differences": [
                    {
                        "field": field,
                        "saved": short_value(old_value, 500),
                        "fresh": short_value(new_value, 500),
                    }
                    for field, old_value, new_value in differences
                ],
            }

            if differences:
                candidate[section] = fresh
                changed_fields.extend(
                    (section, field) for field, _, _ in differences
                )

            # Store the latest API status only for successful requests.
            api_status[status_key] = status["result"]

        time.sleep(REQUEST_DELAY)

    # Fetch school history using the saved school ID.
    school_id = metadata.get("school_id")

    if school_id:
        print("Fetching school_history...")

        status, fresh_history = fetch_section(
            scraper.fetch_history, school_id
        )

        if not status["ok"]:
            print(f"  FAILED: {status['error']}")
            report_sections["school_history"] = {
                "status": "failed",
                "error": status["error"],
                "saved_data_preserved": True,
            }
        else:
            successful_sections += 1
            old_history = original.get("school_history")
            differences = compare_values(old_history, fresh_history)

            print(f"  API success; differences: {len(differences)}")

            report_sections["school_history"] = {
                "status": "success",
                "differences": [
                    {
                        "field": field,
                        "saved": short_value(old_value, 500),
                        "fresh": short_value(new_value, 500),
                    }
                    for field, old_value, new_value in differences
                ],
            }

            if differences:
                candidate["school_history"] = fresh_history
                changed_fields.extend(
                    ("school_history", field)
                    for field, _, _ in differences
                )

            api_status["history"] = status["result"]
    else:
        print("Skipping school_history: saved school ID is missing.")
        report_sections["school_history"] = {
            "status": "skipped",
            "reason": "Saved school ID is missing.",
            "saved_data_preserved": True,
        }

    # Change the stored API status only if its corresponding data changed.
    # Failed requests never replace the previous API status.
    candidate["_api_status"] = api_status

    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "source": "UDISE+",
        "udise_code": args.udise,
        "school_name": metadata.get("school_name"),
        "mode": mode,
        "source_file": str(source_file),
        "successful_sections": successful_sections,
        "changed_fields": [
            {"section": section, "field": field}
            for section, field in changed_fields
        ],
        "sections": report_sections,
        "raw_file_updated": False,
    }

    print()
    print("=" * 72)
    print("SUMMARY")
    print(f"Successful sections: {successful_sections}/5")
    print(f"Changed data fields: {len(changed_fields)}")

    if successful_sections == 0:
        print("No usable API sections; saved raw file will not be updated.")

    elif not changed_fields:
        print("No school-data changes detected.")

    elif not args.apply:
        print("DRY RUN: changes detected but not applied.")

    else:
        # Back up the exact original file before any modification.
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_file = BACKUP_DIR / f"{source_file.stem}_{timestamp}.json"
        backup_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, backup_file)

        # Verify the backup is readable before replacing the original.
        with backup_file.open("r", encoding="utf-8") as file:
            backup_data = json.load(file)

        if backup_data != original:
            raise RuntimeError(
                f"Backup verification failed: {backup_file}. "
                "Original raw file was not replaced."
            )

        write_json_atomically(source_file, candidate)
        report["raw_file_updated"] = True
        report["backup_file"] = str(backup_file)
        print(f"Backup created: {backup_file}")
        print(f"Updated raw file: {source_file}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / (
        f"udise_{args.udise}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
    )

    with report_path.open("w", encoding="utf-8") as file:
        json.dump(report, file, indent=4, ensure_ascii=False)
        file.write("\n")

    print(f"Report saved: {report_path}")
    print("=" * 72)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        print(f"\nSTOPPED SAFELY: {error}")
        raise SystemExit(1)
