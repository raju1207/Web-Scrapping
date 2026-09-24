import json
import re
from pathlib import Path


OLD_FILE = Path("data/delhi_school_list.json")
FRESH_FILE = Path("data/udise_fresh_lists/delhi_fresh.json")

REPORT_DIR = Path("data/audit")
REPORT_FILE = REPORT_DIR / "delhi_fresh_list_comparison.json"


UDISE_KEYS = [
    "udiseSchCode",
    "udiseschCode",
    "udise_code",
    "udiseCode",
    "udiseSchoolCode",
    "schoolUdiseCode",
]

NAME_KEYS = [
    "schoolName",
    "school_name",
    "schName",
    "name",
]

DISTRICT_KEYS = [
    "districtName",
    "district_name",
    "district",
]

BLOCK_KEYS = [
    "blockName",
    "block_name",
    "block",
]


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def normalize_udise(value):
    if value is None:
        return None

    value = str(value).strip()

    # Keep digits only
    digits = re.sub(r"\D", "", value)

    if not digits:
        return None

    # UDISE+ codes normally contain 11 digits.
    # If JSON stored it as a number, a leading zero may have disappeared.
    if len(digits) < 11:
        digits = digits.zfill(11)

    return digits


def first_value(record, keys):
    for key in keys:
        value = record.get(key)

        if value is not None and str(value).strip():
            return value

    return None


def extract_school_records(obj):
    """
    Recursively searches the JSON and extracts dictionaries
    containing a recognizable UDISE school code.
    """

    records = []

    if isinstance(obj, dict):

        udise_value = first_value(obj, UDISE_KEYS)

        if udise_value is not None:
            udise_code = normalize_udise(udise_value)

            if udise_code:
                records.append(
                    {
                        "udise_code": udise_code,
                        "school_name": first_value(obj, NAME_KEYS),
                        "district": first_value(obj, DISTRICT_KEYS),
                        "block": first_value(obj, BLOCK_KEYS),
                        "raw": obj,
                    }
                )

        for value in obj.values():
            if isinstance(value, (dict, list)):
                records.extend(extract_school_records(value))

    elif isinstance(obj, list):

        for item in obj:
            records.extend(extract_school_records(item))

    return records


def deduplicate(records):
    unique = {}

    for record in records:
        code = record["udise_code"]

        if code not in unique:
            unique[code] = record

    return unique


def compact_record(record):
    return {
        "udise_code": record.get("udise_code"),
        "school_name": record.get("school_name"),
        "district": record.get("district"),
        "block": record.get("block"),
    }


def main():

    print("=" * 76)
    print("UDISE+ DELHI SCHOOL LIST COMPARISON")
    print("=" * 76)

    if not OLD_FILE.exists():
        print(f"\nERROR: Old list not found:")
        print(OLD_FILE)
        return

    if not FRESH_FILE.exists():
        print(f"\nERROR: Fresh list not found:")
        print(FRESH_FILE)
        return

    old_json = load_json(OLD_FILE)
    fresh_json = load_json(FRESH_FILE)

    old_records_raw = extract_school_records(old_json)
    fresh_records_raw = extract_school_records(fresh_json)

    old_records = deduplicate(old_records_raw)
    fresh_records = deduplicate(fresh_records_raw)

    old_codes = set(old_records.keys())
    fresh_codes = set(fresh_records.keys())

    only_old_codes = sorted(old_codes - fresh_codes)
    only_fresh_codes = sorted(fresh_codes - old_codes)
    common_codes = sorted(old_codes & fresh_codes)

    only_old = [
        compact_record(old_records[code])
        for code in only_old_codes
    ]

    only_fresh = [
        compact_record(fresh_records[code])
        for code in only_fresh_codes
    ]

    print()
    print(f"Old unique UDISE codes       : {len(old_codes)}")
    print(f"Fresh unique UDISE codes     : {len(fresh_codes)}")
    print(f"Present in both              : {len(common_codes)}")
    print(f"Only in OLD                  : {len(only_old_codes)}")
    print(f"Only in FRESH                : {len(only_fresh_codes)}")

    print()
    print("=" * 76)
    print("ONLY IN OLD LIST")
    print("=" * 76)

    if only_old:
        for school in only_old:
            print(
                f"{school['udise_code']} | "
                f"{school['school_name']} | "
                f"{school['district']} | "
                f"{school['block']}"
            )
    else:
        print("None")

    print()
    print("=" * 76)
    print("ONLY IN FRESH LIST")
    print("=" * 76)

    if only_fresh:
        for school in only_fresh:
            print(
                f"{school['udise_code']} | "
                f"{school['school_name']} | "
                f"{school['district']} | "
                f"{school['block']}"
            )
    else:
        print("None")

    report = {
        "location": "Delhi",
        "old_file": str(OLD_FILE),
        "fresh_file": str(FRESH_FILE),

        "summary": {
            "old_unique_udise_codes": len(old_codes),
            "fresh_unique_udise_codes": len(fresh_codes),
            "present_in_both": len(common_codes),
            "only_in_old": len(only_old_codes),
            "only_in_fresh": len(only_fresh_codes),
        },

        "only_in_old": only_old,
        "only_in_fresh": only_fresh,

        "common_udise_codes": common_codes,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    with open(REPORT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            report,
            file,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("=" * 76)
    print("COMPARISON COMPLETED")
    print("=" * 76)

    print(f"Report saved: {REPORT_FILE}")
    print()
    print("No existing UDISE data was modified.")


if __name__ == "__main__":
    main()
