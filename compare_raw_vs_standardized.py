import json
from pathlib import Path
from collections import Counter, defaultdict

DATASETS = {
    "chandigarh": {
        "raw_dir": Path("data/Chandigarh/2025-26"),
        "standardized": Path(
            "data/standardized/udise/chandigarh/_all_schools.json"
        ),
    },
    "delhi": {
        "raw_dir": Path("data/Delhi/2025-26"),
        "standardized": Path(
            "data/standardized/udise/delhi/_all_schools.json"
        ),
    },
    "mumbai": {
        "raw_dir": Path("data/Mumbai/2025-26"),
        "standardized": Path(
            "data/standardized/udise/mumbai/_all_schools.json"
        ),
    },
}

OUTPUT = Path("data/audit/raw_vs_standardized_report.json")


# raw path -> standardized path
FIELD_MAP = {
    "school_name": (
        ("school_summary", "schoolName"),
        ("school_identity", "school_name"),
    ),
    "udise_code": (
        ("school_summary", "udiseschCode"),
        ("school_identity", "udise_code"),
    ),
    "state": (
        ("school_summary", "stateName"),
        ("location", "state"),
    ),
    "district": (
        ("school_summary", "districtName"),
        ("location", "district"),
    ),
    "block": (
        ("school_summary", "blockName"),
        ("location", "block"),
    ),
    "address": (
        ("school_summary", "address"),
        ("location", "address"),
    ),
    "pincode": (
        ("school_summary", "pincode"),
        ("location", "pincode"),
    ),

    # These are especially important because the existing
    # standardizer appears to be losing them.
    "management_type": (
        ("school_summary", "schMgmtDesc"),
        ("school_information", "management_type"),
    ),
    "school_category": (
        ("school_summary", "schCatDesc"),
        ("school_information", "school_category"),
    ),
    "school_type": (
        ("school_summary", "schTypeDesc"),
        ("school_information", "school_type"),
    ),

    "foundation_year": (
        ("school_profile", "estdYear"),
        ("school_information", "foundation_year"),
    ),
    "website": (
        ("school_profile", "website"),
        ("contact", "website"),
    ),
    "phone": (
        ("school_profile", "schPhone"),
        ("contact", "phone"),
    ),
    "email": (
        ("school_profile", "email"),
        ("contact", "email"),
    ),
    "principal_name": (
        ("school_profile", "headMasterName"),
        ("principal", "name"),
    ),

    "total_boys": (
        ("student_teacher_statistics", "totalBoy"),
        ("student_statistics", "totalBoy"),
    ),
    "total_girls": (
        ("student_teacher_statistics", "totalGirl"),
        ("student_statistics", "totalGirl"),
    ),
    "total_students": (
        ("student_teacher_statistics", "totalCount"),
        ("student_statistics", "totalCount"),
    ),
    "teacher_contract": (
        ("student_teacher_statistics", "totalTeacherCon"),
        ("teacher_statistics", "totalTeacherCon"),
    ),
    "teacher_regular": (
        ("student_teacher_statistics", "totalTeacherReg"),
        ("teacher_statistics", "totalTeacherReg"),
    ),
    "teacher_male": (
        ("student_teacher_statistics", "totalTeacherMale"),
        ("teacher_statistics", "totalTeacherMale"),
    ),
    "teacher_female": (
        ("student_teacher_statistics", "totalTeacherFemale"),
        ("teacher_statistics", "totalTeacherFemale"),
    ),

    "classrooms": (
        ("infrastructure_facilities", "clsrmsInst"),
        ("infrastructure", "clsrmsInst"),
    ),
    "library": (
        ("infrastructure_facilities", "libraryYn"),
        ("infrastructure", "libraryYn"),
    ),
    "playground": (
        ("infrastructure_facilities", "playgroundYn"),
        ("infrastructure", "playgroundYn"),
    ),
    "ramps": (
        ("infrastructure_facilities", "rampsYn"),
        ("infrastructure", "rampsYn"),
    ),
    "handrails": (
        ("infrastructure_facilities", "handrailsYn"),
        ("infrastructure", "handrailsYn"),
    ),
    "internet": (
        ("infrastructure_facilities", "internetYn"),
        ("infrastructure", "internetYn"),
    ),

    "report_total_teacher": (
        ("report_card", "totalTeacher"),
        ("report_card", "totalTeacher"),
    ),
}


def get_path(obj, path):
    current = obj

    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]

    return True, current


def is_missing(value):
    if value is None:
        return True

    if isinstance(value, str) and value.strip() == "":
        return True

    return False


def normalize(value):
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()

        # Only normalization for comparison.
        # We are NOT changing source data.
        return " ".join(value.split()).casefold()

    return str(value).strip().casefold()


def classify(raw_exists, raw_value, std_exists, std_value):
    raw_missing = (not raw_exists) or is_missing(raw_value)
    std_missing = (not std_exists) or is_missing(std_value)

    if not raw_missing and not std_missing:
        if normalize(raw_value) == normalize(std_value):
            return "PASS"
        return "VALUE_MISMATCH"

    if not raw_missing and std_missing:
        return "MAPPING_LOSS"

    if raw_missing and std_missing:
        return "SOURCE_GAP"

    if raw_missing and not std_missing:
        return "ENRICHED_OR_DERIVED"

    return "UNKNOWN"


def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def raw_udise(record):
    exists, value = get_path(
        record,
        ("school_summary", "udiseschCode"),
    )

    if exists and value is not None:
        return str(value).strip()

    # Defensive fallback.
    for section_name in ("report_card",):
        exists, value = get_path(
            record,
            (section_name, "udiseschCode"),
        )
        if exists and value is not None:
            return str(value).strip()

    return None


def standardized_udise(record):
    exists, value = get_path(
        record,
        ("school_identity", "udise_code"),
    )

    if exists and value is not None:
        return str(value).strip()

    return None


def find_raw_files(raw_dir):
    files = []

    for path in raw_dir.glob("*.json"):
        if path.name.startswith("_"):
            continue
        files.append(path)

    return sorted(files)


def audit_dataset(name, config):
    raw_dir = config["raw_dir"]
    std_path = config["standardized"]

    print()
    print("=" * 78)
    print(f"DATASET: {name.upper()}")
    print("=" * 78)

    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw directory not found: {raw_dir}")

    if not std_path.exists():
        raise FileNotFoundError(
            f"Standardized file not found: {std_path}"
        )

    std_records = load_json(std_path)

    if not isinstance(std_records, list):
        raise ValueError(
            f"{std_path} must contain a JSON array."
        )

    std_by_udise = {}
    duplicate_std = []

    for record in std_records:
        code = standardized_udise(record)

        if not code:
            continue

        if code in std_by_udise:
            duplicate_std.append(code)

        std_by_udise[code] = record

    raw_files = find_raw_files(raw_dir)

    summary = Counter()
    field_summary = {
        field: Counter()
        for field in FIELD_MAP
    }

    mapping_losses = []
    mismatches = []
    unmatched_raw = []
    matched_codes = set()

    print(f"Raw school files       : {len(raw_files)}")
    print(f"Standardized records   : {len(std_records)}")
    print(f"Indexed std UDISE codes: {len(std_by_udise)}")

    for raw_path in raw_files:
        try:
            raw = load_json(raw_path)
        except Exception as exc:
            summary["RAW_READ_ERROR"] += 1
            print(
                f"[RAW READ ERROR] {raw_path.name}: {exc}"
            )
            continue

        code = raw_udise(raw)

        if not code:
            summary["RAW_WITHOUT_UDISE"] += 1
            unmatched_raw.append({
                "raw_file": str(raw_path),
                "reason": "RAW_WITHOUT_UDISE",
            })
            continue

        std = std_by_udise.get(code)

        if std is None:
            summary["RAW_NOT_IN_STANDARDIZED"] += 1
            unmatched_raw.append({
                "udise_code": code,
                "raw_file": str(raw_path),
                "reason": "RAW_NOT_IN_STANDARDIZED",
            })
            continue

        matched_codes.add(code)
        summary["MATCHED_RECORDS"] += 1

        for field, (raw_path_keys, std_path_keys) in FIELD_MAP.items():
            raw_exists, raw_value = get_path(
                raw,
                raw_path_keys,
            )

            std_exists, std_value = get_path(
                std,
                std_path_keys,
            )

            status = classify(
                raw_exists,
                raw_value,
                std_exists,
                std_value,
            )

            field_summary[field][status] += 1
            summary[status] += 1

            if status == "MAPPING_LOSS":
                mapping_losses.append({
                    "dataset": name,
                    "udise_code": code,
                    "field": field,
                    "raw_file": str(raw_path),
                    "raw_path": ".".join(raw_path_keys),
                    "standardized_path": ".".join(std_path_keys),
                    "raw_value": raw_value,
                    "standardized_value": std_value,
                })

            elif status == "VALUE_MISMATCH":
                mismatches.append({
                    "dataset": name,
                    "udise_code": code,
                    "field": field,
                    "raw_file": str(raw_path),
                    "raw_path": ".".join(raw_path_keys),
                    "standardized_path": ".".join(std_path_keys),
                    "raw_value": raw_value,
                    "standardized_value": std_value,
                })

    standardized_without_raw = sorted(
        set(std_by_udise) - matched_codes
    )

    print(f"Matched records        : {summary['MATCHED_RECORDS']}")
    print(
        f"Raw not standardized   : "
        f"{summary['RAW_NOT_IN_STANDARDIZED']}"
    )
    print(
        f"Std without raw match  : "
        f"{len(standardized_without_raw)}"
    )
    print(f"Mapping losses         : {summary['MAPPING_LOSS']}")
    print(f"Value mismatches       : {summary['VALUE_MISMATCH']}")
    print(f"Source gaps            : {summary['SOURCE_GAP']}")
    print(
        f"Enriched/derived       : "
        f"{summary['ENRICHED_OR_DERIVED']}"
    )

    print()
    print("FIELD-LEVEL RESULTS")
    print("-" * 78)

    header = (
        f"{'Field':27}"
        f"{'PASS':>8}"
        f"{'LOSS':>8}"
        f"{'MISMATCH':>11}"
        f"{'SOURCE':>9}"
        f"{'ENRICH':>9}"
    )

    print(header)
    print("-" * len(header))

    for field, counts in field_summary.items():
        print(
            f"{field:27}"
            f"{counts['PASS']:>8}"
            f"{counts['MAPPING_LOSS']:>8}"
            f"{counts['VALUE_MISMATCH']:>11}"
            f"{counts['SOURCE_GAP']:>9}"
            f"{counts['ENRICHED_OR_DERIVED']:>9}"
        )

    return {
        "dataset": name,
        "raw_school_files": len(raw_files),
        "standardized_records": len(std_records),
        "indexed_standardized_udise_codes": len(std_by_udise),
        "duplicate_standardized_udise_codes": sorted(
            set(duplicate_std)
        ),
        "summary": dict(summary),
        "field_summary": {
            field: dict(counts)
            for field, counts in field_summary.items()
        },
        "mapping_losses": mapping_losses,
        "value_mismatches": mismatches,
        "unmatched_raw": unmatched_raw,
        "standardized_without_raw_match": standardized_without_raw,
    }


def main():
    print("=" * 78)
    print("UDISE+ RAW -> STANDARDIZED AUDIT")
    print("READ-ONLY AUDIT: RAW/STANDARDIZED DATA WILL NOT BE MODIFIED")
    print("=" * 78)

    results = {}

    for name, config in DATASETS.items():
        results[name] = audit_dataset(
            name,
            config,
        )

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    with OUTPUT.open("w", encoding="utf-8") as f:
        json.dump(
            results,
            f,
            indent=2,
            ensure_ascii=False,
        )

    print()
    print("=" * 78)
    print("AUDIT COMPLETED")
    print("=" * 78)
    print(f"Report saved to: {OUTPUT}")
    print("No raw files were modified.")
    print("No standardized files were modified.")


if __name__ == "__main__":
    main()