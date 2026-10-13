
import json
import time
from pathlib import Path

import scraper


# ============================================================
# CONFIGURATION
# Comparison-only mode: never modifies existing raw JSON files.
# ============================================================

DATA_DIR = Path("data/Delhi/2025-26")
UDISE_CODE = "07060409205"
REQUEST_DELAY = getattr(scraper, "REQUEST_DELAY", 0.4)

SECTIONS = {
    "school_profile": scraper.fetch_profile,
    "student_teacher_statistics": scraper.fetch_student_teacher,
    "infrastructure_facilities": scraper.fetch_facilities,
    "report_card": scraper.fetch_report_card,
}


def short_value(value, limit=180):
    """Format a value for readable terminal output."""
    text = repr(value)
    if len(text) > limit:
        return text[:limit] + "... [truncated]"
    return text


def compare_values(old, new, path=""):
    """Return field-level differences between two JSON values."""
    differences = []

    if isinstance(old, dict) and isinstance(new, dict):
        all_keys = sorted(set(old) | set(new))

        for key in all_keys:
            child_path = f"{path}.{key}" if path else key

            if key not in old:
                differences.append(
                    (child_path, "<MISSING>", new[key])
                )
            elif key not in new:
                differences.append(
                    (child_path, old[key], "<MISSING>")
                )
            else:
                differences.extend(
                    compare_values(old[key], new[key], child_path)
                )

    elif isinstance(old, list) and isinstance(new, list):
        if len(old) != len(new):
            differences.append(
                (f"{path} [list length]", len(old), len(new))
            )

        for index, (old_item, new_item) in enumerate(zip(old, new)):
            differences.extend(
                compare_values(old_item, new_item, f"{path}[{index}]")
            )

    elif old != new:
        differences.append((path, old, new))

    return differences


def main():
    print("=" * 70)
    print("UDISE+ DAILY REFRESH — COMPARISON-ONLY TEST")
    print("=" * 70)
    print("Mode: READ ONLY")
    print("Existing raw JSON files will NOT be modified.")
    print(f"UDISE code: {UDISE_CODE}")
    print()

    matches = list(DATA_DIR.glob(f"*_{UDISE_CODE}.json"))

    if len(matches) != 1:
        print(f"Expected one saved JSON file; found {len(matches)}.")
        print(f"Folder: {DATA_DIR.resolve()}")
        print("No API requests were made.")
        return

    source_file = matches[0]

    with source_file.open("r", encoding="utf-8") as file:
        saved = json.load(file)

    print(f"Saved file: {source_file}")
    print(f"Saved school: {saved.get('metadata', {}).get('school_name')}")
    print(f"Saved school ID: {saved.get('metadata', {}).get('school_id')}")
    print()

    results = {}

    # Fetch each section and keep all fresh results in memory only.
    for section, fetch_function in SECTIONS.items():
        print(f"Fetching: {section}")

        try:
            result = fetch_function(UDISE_CODE)
        except Exception as error:
            print(f"  REQUEST ERROR: {error}")
            results[section] = {"comparable": False}
            time.sleep(REQUEST_DELAY)
            continue

        if not isinstance(result, dict) or not result.get("success"):
            error_message = (
                result.get("error", "Unknown API error")
                if isinstance(result, dict)
                else "Unexpected result format"
            )
            print(f"  REQUEST FAILED: {error_message}")
            results[section] = {"comparable": False}
        else:
            fresh = scraper.extract_api_data(result)

            if fresh is None:
                response = result.get("response")
                response_keys = (
                    list(response.keys())
                    if isinstance(response, dict)
                    else type(response).__name__
                )
                print("  API request succeeded, but data extraction returned None.")
                print(f"  Response structure: {response_keys}")
                print("  Section will NOT be treated as an empty dataset.")
                results[section] = {"comparable": False}
            else:
                results[section] = {
                    "comparable": True,
                    "data": fresh,
                }
                print(f"  Retrieved data type: {type(fresh).__name__}")

        time.sleep(REQUEST_DELAY)

    # History uses the school ID stored in the existing file.
    school_id = saved.get("metadata", {}).get("school_id")

    if school_id:
        print("Fetching: school_history")

        try:
            result = scraper.fetch_history(school_id)

            if not isinstance(result, dict) or not result.get("success"):
                error_message = (
                    result.get("error", "Unknown API error")
                    if isinstance(result, dict)
                    else "Unexpected result format"
                )
                print(f"  REQUEST FAILED: {error_message}")
                results["school_history"] = {"comparable": False}
            else:
                fresh = scraper.extract_api_data(result)

                if fresh is None:
                    response = result.get("response")
                    response_keys = (
                        list(response.keys())
                        if isinstance(response, dict)
                        else type(response).__name__
                    )
                    print("  Data extraction returned None; not comparing.")
                    print(f"  Response structure: {response_keys}")
                    results["school_history"] = {"comparable": False}
                else:
                    results["school_history"] = {
                        "comparable": True,
                        "data": fresh,
                    }
                    print(f"  Retrieved data type: {type(fresh).__name__}")

        except Exception as error:
            print(f"  REQUEST ERROR: {error}")
            results["school_history"] = {"comparable": False}
    else:
        print("Skipping history: saved school ID is missing.")
        results["school_history"] = {"comparable": False}

    print()
    print("=" * 70)
    print("COMPARISON RESULTS")
    print("=" * 70)

    for section, result in results.items():
        if not result.get("comparable"):
            print(f"\n{section}: NOT COMPARED")
            continue

        old = saved.get(section)
        new = result["data"]

        differences = compare_values(old, new)

        print(f"\n{section}:")

        if not differences:
            print("  No differences found.")
            continue

        print(f"  Differences found: {len(differences)}")

        for field, old_value, new_value in differences[:30]:
            print(f"  Field: {field}")
            print(f"    Saved: {short_value(old_value)}")
            print(f"    Fresh: {short_value(new_value)}")

        if len(differences) > 30:
            print(f"  ... {len(differences) - 30} more differences.")

    print()
    print("=" * 70)
    print("TEST FINISHED")
    print("No raw JSON file was written or overwritten.")
    print("=" * 70)


if __name__ == "__main__":
    main()
