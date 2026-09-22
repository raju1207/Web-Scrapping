from pathlib import Path
import json
from collections import Counter, defaultdict


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
# 2. IMPORTANT FIELD MAPPINGS
# ============================================================

FIELD_PATHS = {
    # --------------------------------------------------------
    # School identity
    # --------------------------------------------------------
    "school_name": (
        "school_summary",
        "schoolName"
    ),

    "udise_code": (
        "school_summary",
        "udiseschCode"
    ),

    # --------------------------------------------------------
    # Location
    # --------------------------------------------------------
    "state": (
        "school_summary",
        "stateName"
    ),

    "district": (
        "school_summary",
        "districtName"
    ),

    "block": (
        "school_summary",
        "blockName"
    ),

    "cluster": (
        "school_summary",
        "clusterName"
    ),

    "village_ward": (
        "school_summary",
        "villageName"
    ),

    "address": (
        "school_summary",
        "address"
    ),

    "pincode": (
        "school_summary",
        "pincode"
    ),

    # --------------------------------------------------------
    # Contact
    # --------------------------------------------------------
    "email": (
        "school_summary",
        "email"
    ),

    "phone": (
        "school_profile",
        "schPhone"
    ),

    "website": (
        "school_profile",
        "website"
    ),

    # --------------------------------------------------------
    # Classification
    # --------------------------------------------------------
    "management_type": (
        "school_summary",
        "schMgmtDesc"
    ),

    "school_category": (
        "school_summary",
        "schCatDesc"
    ),

    "school_type": (
        "school_summary",
        "schTypeDesc"
    ),

    "class_from": (
        "school_summary",
        "classFrm"
    ),

    "class_to": (
        "school_summary",
        "classTo"
    ),

    "operational_status": (
        "school_summary",
        "schoolStatusName"
    ),

    # --------------------------------------------------------
    # School profile
    # --------------------------------------------------------
    "estd_year": (
        "school_profile",
        "estdYear"
    ),

    "head_name": (
        "school_profile",
        "headMasterName"
    ),

    "minority_school": (
        "school_profile",
        "minorityYn"
    ),

    "cwsn_school": (
        "school_profile",
        "cwsnSchYn"
    ),

    "shift_school": (
        "school_profile",
        "shiftSchYn"
    ),

    "residential_type": (
        "school_profile",
        "resiSchDesc"
    ),

    # --------------------------------------------------------
    # Board
    # --------------------------------------------------------
    "board_secondary": (
        "school_profile",
        "boardSecName"
    ),

    "board_higher_secondary": (
        "school_profile",
        "boardHighSecName"
    ),

    # --------------------------------------------------------
    # Student statistics
    # --------------------------------------------------------
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

    "male_teachers": (
        "student_teacher_statistics",
        "totalTeacherMale"
    ),

    "female_teachers": (
        "student_teacher_statistics",
        "totalTeacherFemale"
    ),

    # --------------------------------------------------------
    # Teacher statistics
    # --------------------------------------------------------
    "total_teachers": (
        "report_card",
        "totalTeacher"
    ),

    # --------------------------------------------------------
    # Infrastructure
    # --------------------------------------------------------
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
# 3. MISSING VALUE TYPES
# ============================================================

VALID_VALUE = "VALID_VALUE"

SECTION_MISSING = "SECTION_MISSING"

SECTION_NULL = "SECTION_NULL"

SECTION_EMPTY = "SECTION_EMPTY"

FIELD_NOT_PRESENT = "FIELD_NOT_PRESENT"

FIELD_NULL = "FIELD_NULL"

FIELD_EMPTY_STRING = "FIELD_EMPTY_STRING"


# ============================================================
# 4. GET SCHOOL FILES
# ============================================================

def get_school_files(folder):

    if not folder.exists():
        return []

    return sorted(
        file
        for file in folder.glob("*.json")
        if not file.name.startswith("_")
    )


# ============================================================
# 5. GET UDISE CODE
# ============================================================

def get_udise_code(data):

    summary = data.get("school_summary")

    if not isinstance(summary, dict):
        return None

    value = summary.get("udiseschCode")

    if value is None:
        return None

    return str(value).strip()


# ============================================================
# 6. CLASSIFY ONE FIELD
# ============================================================

def classify_field(data, path):

    section_name = path[0]
    field_name = path[1]

    # --------------------------------------------------------
    # Section does not exist at all
    # --------------------------------------------------------

    if section_name not in data:

        return {
            "status": SECTION_MISSING,
            "value": None
        }

    section = data[section_name]

    # --------------------------------------------------------
    # Section exists but value is null
    # --------------------------------------------------------

    if section is None:

        return {
            "status": SECTION_NULL,
            "value": None
        }

    # --------------------------------------------------------
    # Section exists but is not a dictionary
    # --------------------------------------------------------

    if not isinstance(section, dict):

        return {
            "status": SECTION_EMPTY,
            "value": None
        }

    # --------------------------------------------------------
    # Section exists but is empty {}
    # --------------------------------------------------------

    if not section:

        return {
            "status": SECTION_EMPTY,
            "value": None
        }

    # --------------------------------------------------------
    # Field key does not exist
    # --------------------------------------------------------

    if field_name not in section:

        return {
            "status": FIELD_NOT_PRESENT,
            "value": None
        }

    value = section[field_name]

    # --------------------------------------------------------
    # Field exists but value is null
    # --------------------------------------------------------

    if value is None:

        return {
            "status": FIELD_NULL,
            "value": None
        }

    # --------------------------------------------------------
    # Empty string
    # --------------------------------------------------------

    if isinstance(value, str):

        cleaned = value.strip()

        if cleaned == "":

            return {
                "status": FIELD_EMPTY_STRING,
                "value": value
            }

        if cleaned.lower() in {
            "null",
            "none",
            "nan",
            "n/a",
            "na"
        }:

            return {
                "status": FIELD_EMPTY_STRING,
                "value": value
            }

    # --------------------------------------------------------
    # Otherwise it is a valid source value
    # --------------------------------------------------------

    return {
        "status": VALID_VALUE,
        "value": value
    }


# ============================================================
# 7. AUDIT ONE DATASET
# ============================================================

def classify_dataset(dataset_name, folder):

    print()
    print("=" * 100)
    print(dataset_name)
    print("=" * 100)

    files = get_school_files(folder)

    print(f"Files found: {len(files)}")

    # Counts per field
    field_status_counts = {
        field: Counter()
        for field in FIELD_PATHS
    }

    # Overall classification counts
    overall_counts = Counter()

    # Example records for each problem
    examples = defaultdict(list)

    # School-level problem report
    problem_records = []

    valid_json_count = 0
    invalid_json_count = 0

    for file_path in files:

        try:

            with file_path.open(
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

        except Exception as error:

            invalid_json_count += 1

            problem_records.append({
                "file": file_path.name,
                "udise_code": None,
                "error": str(error),
                "problems": [
                    {
                        "status": "INVALID_JSON"
                    }
                ]
            })

            continue

        valid_json_count += 1

        udise_code = get_udise_code(data)

        school_problems = []

        # ----------------------------------------------------
        # Check every mapped field
        # ----------------------------------------------------

        for canonical_field, path in FIELD_PATHS.items():

            result = classify_field(
                data,
                path
            )

            status = result["status"]

            field_status_counts[
                canonical_field
            ][status] += 1

            overall_counts[status] += 1

            # ------------------------------------------------
            # Save problems only
            # ------------------------------------------------

            if status != VALID_VALUE:

                problem = {
                    "field": canonical_field,
                    "source_section": path[0],
                    "source_field": path[1],
                    "status": status
                }

                school_problems.append(problem)

                example_key = (
                    canonical_field,
                    status
                )

                if len(examples[example_key]) < 5:

                    examples[example_key].append({
                        "file": file_path.name,
                        "udise_code": udise_code
                    })

        if school_problems:

            problem_records.append({
                "file": file_path.name,
                "udise_code": udise_code,
                "problems": school_problems
            })

    # ========================================================
    # PRINT SUMMARY
    # ========================================================

    print()
    print("JSON VALIDATION")
    print("-" * 100)

    print(f"Valid JSON   : {valid_json_count}")
    print(f"Invalid JSON : {invalid_json_count}")

    print()
    print("FIELD CLASSIFICATION REPORT")
    print("-" * 100)

    print(
        f"{'Field':<28}"
        f"{'Valid':>8}"
        f"{'SecNull':>10}"
        f"{'FldNull':>10}"
        f"{'NotFound':>10}"
        f"{'Empty':>10}"
    )

    print("-" * 76)

    for field_name in FIELD_PATHS:

        counts = field_status_counts[field_name]

        print(
            f"{field_name:<28}"
            f"{counts[VALID_VALUE]:>8}"
            f"{counts[SECTION_NULL]:>10}"
            f"{counts[FIELD_NULL]:>10}"
            f"{counts[FIELD_NOT_PRESENT]:>10}"
            f"{counts[FIELD_EMPTY_STRING]:>10}"
        )

    print()
    print("OVERALL CLASSIFICATION")
    print("-" * 100)

    for status in [
        VALID_VALUE,
        SECTION_MISSING,
        SECTION_NULL,
        SECTION_EMPTY,
        FIELD_NOT_PRESENT,
        FIELD_NULL,
        FIELD_EMPTY_STRING
    ]:

        print(
            f"{status:<25}: "
            f"{overall_counts[status]}"
        )

    print()
    print(
        "Schools with at least one classified "
        f"missing field: {len(problem_records)}"
    )

    # ========================================================
    # RETURN REPORT
    # ========================================================

    return {
        "dataset": dataset_name,
        "folder": str(folder),

        "total_files": len(files),

        "valid_json": valid_json_count,
        "invalid_json": invalid_json_count,

        "overall_classification": dict(
            overall_counts
        ),

        "field_classification": {
            field: dict(counts)
            for field, counts
            in field_status_counts.items()
        },

        "examples": {
            f"{field}__{status}": records
            for (field, status), records
            in examples.items()
        },

        "problem_records": problem_records
    }


# ============================================================
# 8. MAIN
# ============================================================

def main():

    print()
    print("=" * 100)
    print("SCHOOLFINDER MISSING VALUE CLASSIFIER")
    print("=" * 100)

    reports = []

    for dataset_name, folder in DATASETS.items():

        report = classify_dataset(
            dataset_name,
            folder
        )

        reports.append(report)

    # ========================================================
    # SAVE REPORT
    # ========================================================

    output_folder = DATA_DIR / "audit"

    output_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        output_folder /
        "missing_value_classification_report.json"
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
    print("=" * 100)
    print("CLASSIFICATION COMPLETED")
    print("=" * 100)

    print(
        f"Report saved to: {output_file}"
    )

    print()
    print(
        "No raw school JSON files were modified."
    )


# ============================================================
# 9. RUN
# ============================================================

if __name__ == "__main__":
    main()