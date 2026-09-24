import json
import re
from pathlib import Path


LIST_FILE = Path("data/mumbai_school_list.json")
RAW_DIR = Path("data/Mumbai/2025-26")

REPORT_DIR = Path("data/audit")
REPORT_FILE = REPORT_DIR / "mumbai_list_vs_raw_report.json"


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


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def normalize_udise(value):
    if value is None:
        return None

    digits = re.sub(r"\D", "", str(value).strip())

    if not digits:
        return None

    if len(digits) < 11:
        digits = digits.zfill(11)

    return digits


def find_value(obj, keys):
    if isinstance(obj, dict):

        for key in keys:
            if key in obj:
                value = obj[key]

                if value is not None and str(value).strip():
                    return value

        for value in obj.values():
            result = find_value(value, keys)

            if result is not None:
                return result

    elif isinstance(obj, list):

        for item in obj:
            result = find_value(item, keys)

            if result is not None:
                return result

    return None


def extract_list_records(obj):
    records = {}

    def walk(value):

        if isinstance(value, dict):

            udise = find_direct_value(value, UDISE_KEYS)

            if udise:
                code = normalize_udise(udise)

                if code and code not in records:
                    records[code] = {
                        "udise_code": code,
                        "school_name": find_direct_value(
                            value,
                            NAME_KEYS
                        ),
                    }

            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)

        elif isinstance(value, list):

            for child in value:
                walk(child)

    walk(obj)

    return records


def find_direct_value(record, keys):
    if not isinstance(record, dict):
        return None

    for key in keys:
        value = record.get(key)

        if value is not None and str(value).strip():
            return value

    return None


def scan_raw_school_files():
    records = {}
    invalid_files = []
    duplicate_codes = {}

    for path in RAW_DIR.rglob("*.json"):

        # Ignore generated summary/error files
        if path.name.startswith("_"):
            continue

        if "_errors" in path.parts:
            continue

        try:
            data = load_json(path)
        except Exception as exc:
            invalid_files.append({
                "file": str(path),
                "error": str(exc),
            })
            continue

        udise = find_value(data, UDISE_KEYS)
        code = normalize_udise(udise)

        if not code:
            invalid_files.append({
                "file": str(path),
                "error": "UDISE code not found",
            })
            continue

        name = find_value(data, NAME_KEYS)

        record = {
            "udise_code": code,
            "school_name": name,
            "file": str(path),
        }

        if code in records:

            duplicate_codes.setdefault(
                code,
                [records[code]]
            ).append(record)

        else:
            records[code] = record

    return records, invalid_files, duplicate_codes


def main():

    print("=" * 78)
    print("MUMBAI UDISE LIST VS RAW SCHOOL FILE AUDIT")
    print("=" * 78)

    if not LIST_FILE.exists():
        print(f"ERROR: List file not found: {LIST_FILE}")
        return

    if not RAW_DIR.exists():
        print(f"ERROR: Raw directory not found: {RAW_DIR}")
        return

    school_list_json = load_json(LIST_FILE)

    expected = extract_list_records(school_list_json)

    raw_records, invalid_files, duplicate_codes = (
        scan_raw_school_files()
    )

    expected_codes = set(expected.keys())
    raw_codes = set(raw_records.keys())

    missing_codes = sorted(expected_codes - raw_codes)
    extra_codes = sorted(raw_codes - expected_codes)
    common_codes = sorted(expected_codes & raw_codes)

    print()
    print(f"School-list UDISE codes       : {len(expected_codes)}")
    print(f"Raw school UDISE codes        : {len(raw_codes)}")
    print(f"Present in both               : {len(common_codes)}")
    print(f"Missing raw school records    : {len(missing_codes)}")
    print(f"Extra raw school records      : {len(extra_codes)}")
    print(f"Invalid raw files             : {len(invalid_files)}")
    print(f"Duplicate UDISE codes         : {len(duplicate_codes)}")

    print()
    print("=" * 78)
    print("MISSING SCHOOL RECORDS")
    print("=" * 78)

    missing_records = []

    if missing_codes:

        for code in missing_codes:

            school = expected.get(code, {})

            record = {
                "udise_code": code,
                "school_name": school.get("school_name"),
            }

            missing_records.append(record)

            print(
                f"{code} | "
                f"{school.get('school_name')}"
            )

    else:
        print("None")

    print()
    print("=" * 78)
    print("EXTRA RAW RECORDS")
    print("=" * 78)

    extra_records = []

    if extra_codes:

        for code in extra_codes:

            school = raw_records.get(code, {})

            record = {
                "udise_code": code,
                "school_name": school.get("school_name"),
                "file": school.get("file"),
            }

            extra_records.append(record)

            print(
                f"{code} | "
                f"{school.get('school_name')} | "
                f"{school.get('file')}"
            )

    else:
        print("None")

    report = {
        "location": "Mumbai",

        "summary": {
            "school_list_count": len(expected_codes),
            "raw_school_count": len(raw_codes),
            "common_count": len(common_codes),
            "missing_raw_count": len(missing_codes),
            "extra_raw_count": len(extra_codes),
            "invalid_raw_files": len(invalid_files),
            "duplicate_udise_codes": len(duplicate_codes),
        },

        "missing_raw_records": missing_records,
        "extra_raw_records": extra_records,
        "invalid_files": invalid_files,

        "duplicate_codes": {
            code: records
            for code, records in duplicate_codes.items()
        },
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            report,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("=" * 78)
    print("AUDIT COMPLETED")
    print("=" * 78)

    print(f"Report: {REPORT_FILE}")
    print()
    print("No existing school data was modified.")


if __name__ == "__main__":
    main()