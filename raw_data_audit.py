from pathlib import Path
import json
from collections import Counter

# ============================================================
# 1. PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

DATASETS = {
    "UDISE Chandigarh": DATA_DIR / "Chandigarh" / "2025-26",
    "UDISE Delhi": DATA_DIR / "Delhi" / "2025-26",
    "UDISE Mumbai": DATA_DIR / "Mumbai" / "2025-26",
}


# ============================================================
# 2. FIELDS TO AUDIT
# Exact paths based on the SchoolFinder Field Mapping Matrix
# ============================================================

FIELD_PATHS = {
    # Identity
    "school_name": ("school_summary", "schoolName"),
    "udise_code": ("school_summary", "udiseschCode"),

    # Location
    "state": ("school_summary", "stateName"),
    "district": ("school_summary", "districtName"),
    "block": ("school_summary", "blockName"),
    "cluster": ("school_summary", "clusterName"),
    "village_ward": ("school_summary", "villageName"),
    "address": ("school_summary", "address"),
    "pincode": ("school_summary", "pincode"),

    # Contact
    "email": ("school_summary", "email"),
    "phone": ("school_profile", "schPhone"),
    "website": ("school_profile", "website"),

    # Classification
    "management_type": ("school_summary", "schMgmtDesc"),
    "school_category": ("school_summary", "schCatDesc"),
    "school_type": ("school_summary", "schTypeDesc"),
    "class_from": ("school_summary", "classFrm"),
    "class_to": ("school_summary", "classTo"),
    "operational_status": ("school_summary", "schoolStatusName"),

    # School information
    "estd_year": ("school_profile", "estdYear"),
    "head_name": ("school_profile", "headMasterName"),
    "minority_school": ("school_profile", "minorityYn"),
    "cwsn_school": ("school_profile", "cwsnSchYn"),
    "shift_school": ("school_profile", "shiftSchYn"),
    "residential_type": ("school_profile", "resiSchDesc"),

    # Board
    "board_secondary": ("school_profile", "boardSecName"),
    "board_higher_secondary": ("school_profile", "boardHighSecName"),

    # Student statistics
    "total_students": (
        "student_teacher_statistics",
        "totalCount"
    ),
    "total_boys": (
        "student_teacher_statistics",
        "totalBoy"
    ),
    "total_girls": (
        "student_teacher_statistics",
        "totalGirl"
    ),

    # Teacher statistics
    "total_teachers": ("report_card", "totalTeacher"),
    "male_teachers": (
        "student_teacher_statistics",
        "totalTeacherMale"
    ),
    "female_teachers": (
        "student_teacher_statistics",
        "totalTeacherFemale"
    ),

    # Infrastructure
    "classrooms_total": (
        "infrastructure_facilities",
        "clsrmsInst"
    ),
    "library": (
        "infrastructure_facilities",
        "libraryYn"
    ),
    "playground": (
        "infrastructure_facilities",
        "playgroundYn"
    ),
    "ramps": (
        "infrastructure_facilities",
        "rampsYn"
    ),
    "handrails": (
        "infrastructure_facilities",
        "handrailsYn"
    ),
    "internet": (
        "infrastructure_facilities",
        "internetYn"
    ),
}


# ============================================================
# 3. VALUES THAT WE CONSIDER MISSING
# ============================================================

def is_missing(value):
    """
    Return True only when the value is genuinely empty.

    IMPORTANT:
    0 and False are NOT considered missing.
    """

    if value is None:
        return True

    if isinstance(value, str):
        cleaned = value.strip().lower()

        if cleaned in {
            "",
            "null",
            "none",
            "nan",
            "n/a",
            "na"
        }:
            return True

    return False


# ============================================================
# 4. READ A NESTED JSON FIELD
# ============================================================

def get_nested_value(data, path):
    """
    Example:

    path:
        ("school_summary", "schoolName")

    reads:

        data["school_summary"]["schoolName"]

    Safely returns None if the path does not exist.
    """

    current = data

    for key in path:

        if not isinstance(current, dict):
            return None

        if key not in current:
            return None

        current = current[key]

    return current


# ============================================================
# 5. FIND SCHOOL JSON FILES
# ============================================================

def get_school_files(folder):
    """
    Get individual school JSON files.

    Files beginning with "_" are ignored because files such as:

        _summary.json
        _all_schools.json

    are aggregate files, not individual schools.
    """

    if not folder.exists():
        return []

    return sorted(
        file
        for file in folder.glob("*.json")
        if not file.name.startswith("_")
    )


# ============================================================
# 6. AUDIT ONE DATASET
# ============================================================

def audit_dataset(dataset_name, folder):

    print()
    print("=" * 80)
    print(dataset_name)
    print("=" * 80)

    print(f"Folder: {folder}")

    files = get_school_files(folder)

    print(f"Individual JSON files found: {len(files)}")

    # --------------------------------------------------------
    # Counters
    # --------------------------------------------------------

    valid_records = 0
    invalid_json_files = []

    udise_codes = []

    missing_counts = Counter()

    missing_examples = {
        field: []
        for field in FIELD_PATHS
    }

    # --------------------------------------------------------
    # Process every school file
    # --------------------------------------------------------

    for file_path in files:

        try:

            with file_path.open(
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

        except Exception as error:

            invalid_json_files.append(
                {
                    "file": file_path.name,
                    "error": str(error)
                }
            )

            continue

        valid_records += 1

        # ----------------------------------------------------
        # Extract UDISE code
        # ----------------------------------------------------

        udise_code = get_nested_value(
            data,
            FIELD_PATHS["udise_code"]
        )

        if not is_missing(udise_code):
            udise_codes.append(str(udise_code).strip())

        # ----------------------------------------------------
        # Check every important field
        # ----------------------------------------------------

        for field_name, path in FIELD_PATHS.items():

            value = get_nested_value(
                data,
                path
            )

            if is_missing(value):

                missing_counts[field_name] += 1

                # Save only first 5 examples
                if len(missing_examples[field_name]) < 5:

                    missing_examples[field_name].append(
                        file_path.name
                    )

    # --------------------------------------------------------
    # Find duplicate UDISE codes
    # --------------------------------------------------------

    code_counts = Counter(udise_codes)

    duplicate_codes = {
        code: count
        for code, count in code_counts.items()
        if count > 1
    }

    # --------------------------------------------------------
    # Count missing UDISE codes
    # --------------------------------------------------------

    missing_udise_count = (
        valid_records - len(udise_codes)
    )

    # --------------------------------------------------------
    # Print basic validation
    # --------------------------------------------------------

    print()
    print("RECORD VALIDATION")
    print("-" * 80)

    print(f"Files found       : {len(files)}")
    print(f"Valid JSON        : {valid_records}")
    print(f"Invalid JSON      : {len(invalid_json_files)}")
    print(f"UDISE codes found : {len(udise_codes)}")
    print(f"Unique UDISE codes: {len(set(udise_codes))}")
    print(f"Missing UDISE code: {missing_udise_count}")
    print(f"Duplicate codes   : {len(duplicate_codes)}")

    # --------------------------------------------------------
    # Missing-value report
    # --------------------------------------------------------

    print()
    print("MISSING VALUE REPORT")
    print("-" * 80)

    print(
        f"{'Field':<30}"
        f"{'Missing':>10}"
        f"{'Present':>10}"
        f"{'Missing %':>12}"
    )

    print("-" * 62)

    for field_name in FIELD_PATHS:

        missing = missing_counts[field_name]

        present = valid_records - missing

        if valid_records:

            missing_percentage = (
                missing / valid_records
            ) * 100

        else:
            missing_percentage = 0

        print(
            f"{field_name:<30}"
            f"{missing:>10}"
            f"{present:>10}"
            f"{missing_percentage:>11.2f}%"
        )

    # --------------------------------------------------------
    # Print duplicate codes
    # --------------------------------------------------------

    if duplicate_codes:

        print()
        print("DUPLICATE UDISE CODES")
        print("-" * 80)

        for code, count in duplicate_codes.items():

            print(
                f"{code}: {count} records"
            )

    # --------------------------------------------------------
    # Print invalid JSON files
    # --------------------------------------------------------

    if invalid_json_files:

        print()
        print("INVALID JSON FILES")
        print("-" * 80)

        for item in invalid_json_files:

            print(
                f"{item['file']} -> "
                f"{item['error']}"
            )

    # --------------------------------------------------------
    # Return report
    # --------------------------------------------------------

    return {
        "dataset": dataset_name,
        "folder": str(folder),

        "total_files": len(files),
        "valid_json": valid_records,
        "invalid_json": len(invalid_json_files),

        "udise_codes_found": len(udise_codes),
        "unique_udise_codes": len(set(udise_codes)),
        "missing_udise_codes": missing_udise_count,

        "duplicate_udise_codes": duplicate_codes,

        "missing_values": {
            field: {
                "missing": missing_counts[field],
                "present": (
                    valid_records -
                    missing_counts[field]
                ),
                "missing_percentage": round(
                    (
                        missing_counts[field]
                        / valid_records
                        * 100
                    )
                    if valid_records
                    else 0,
                    2
                ),
                "example_files": missing_examples[field]
            }
            for field in FIELD_PATHS
        },

        "invalid_files": invalid_json_files
    }


# ============================================================
# 7. MAIN PROGRAM
# ============================================================

def main():

    print()
    print("=" * 80)
    print("SCHOOLFINDER RAW DATA AUDIT")
    print("=" * 80)

    reports = []

    for dataset_name, folder in DATASETS.items():

        report = audit_dataset(
            dataset_name,
            folder
        )

        reports.append(report)

    # --------------------------------------------------------
    # Save audit report
    # --------------------------------------------------------

    output_folder = (
        DATA_DIR /
        "audit"
    )

    output_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        output_folder /
        "raw_data_audit_report.json"
    )

    with output_file.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            reports,
            file,
            indent=2,
            ensure_ascii=False
        )

    print()
    print("=" * 80)
    print("AUDIT COMPLETED")
    print("=" * 80)

    print(
        f"Report saved to: {output_file}"
    )

    print()
    print(
        "IMPORTANT: No raw JSON files were modified."
    )


# ============================================================
# 8. RUN PROGRAM
# ============================================================

if __name__ == "__main__":
    main()