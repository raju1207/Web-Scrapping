import json
from pathlib import Path
from collections import Counter

SCHEMA_VERSION = "4.0"
ACADEMIC_YEAR = "2025-26"

DATASETS = {
    "chandigarh": {
        "raw": Path("data/Chandigarh/2025-26"),
        "canonical": Path("data/canonical_v4/udise/chandigarh"),
        "expected": 236,
    },
    "delhi": {
        "raw": Path("data/Delhi/2025-26"),
        "canonical": Path("data/canonical_v4/udise/delhi"),
        "expected": 237,
    },
    "mumbai": {
        "raw": Path("data/Mumbai/2025-26"),
        "canonical": Path("data/canonical_v4/udise/mumbai"),
        "expected": 2787,
    },
}

REPORT_PATH = Path(
    "data/audit/canonical_v4_validation_report.json"
)

REQUIRED_TOP_LEVEL = {
    "metadata",
    "identity_location",
    "classification",
    "board_affiliation",
    "principal",
    "inclusive_residential",
    "student_teacher_statistics",
    "infrastructure",
    "financial_scholarships",
    "media_gallery",
    "academic_outcomes",
    "safety_verification",
    "admissions",
    "school_profile_enrichment",
    "history_seo_tags",
    "provenance",
    "entity_resolution",
}


# ============================================================
# HELPERS
# ============================================================

def load_json(path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )


def meaningful(value):
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, dict)):
        return bool(value)

    return True


def clean(value):
    if value is None:
        return None

    if isinstance(value, str):
        value = " ".join(value.strip().split())

        if not value:
            return None

        return value.casefold()

    return str(value).strip().casefold()


def get_raw_files(path):
    return sorted(
        p for p in path.glob("*.json")
        if not p.name.startswith("_")
    )


def get_canonical_files(path):
    school_dir = path / "schools"

    if not school_dir.exists():
        return []

    return sorted(
        school_dir.glob("*.json")
    )


def raw_udise(raw):
    summary = raw.get("school_summary")

    if isinstance(summary, dict):
        value = summary.get("udiseschCode")

        if value is not None:
            return str(value).strip()

    report = raw.get("report_card")

    if isinstance(report, dict):
        value = report.get("udiseschCode")

        if value is not None:
            return str(value).strip()

    return None


def canonical_udise(record):
    metadata = record.get("metadata", {})

    value = metadata.get("udise_code")

    if value is None:
        return None

    return str(value).strip()


# ============================================================
# RAW INDEX
# ============================================================

def build_raw_index(raw_dir):
    index = {}
    duplicates = []
    errors = []

    for path in get_raw_files(raw_dir):
        try:
            raw = load_json(path)
        except Exception as exc:
            errors.append({
                "file": str(path),
                "error": str(exc),
            })
            continue

        code = raw_udise(raw)

        if not code:
            errors.append({
                "file": str(path),
                "error": "MISSING_UDISE",
            })
            continue

        if code in index:
            duplicates.append(code)

        index[code] = {
            "record": raw,
            "file": str(path),
        }

    return index, duplicates, errors


# ============================================================
# CANONICAL INDEX
# ============================================================

def build_canonical_index(canonical_dir):
    index = {}
    udise_duplicates = []
    record_ids = Counter()
    errors = []

    for path in get_canonical_files(canonical_dir):
        try:
            record = load_json(path)
        except Exception as exc:
            errors.append({
                "file": str(path),
                "error": str(exc),
            })
            continue

        code = canonical_udise(record)

        if not code:
            errors.append({
                "file": str(path),
                "error": "MISSING_UDISE",
            })
            continue

        if code in index:
            udise_duplicates.append(code)

        index[code] = {
            "record": record,
            "file": str(path),
        }

        record_id = (
            record
            .get("metadata", {})
            .get("record_id")
        )

        if record_id:
            record_ids[record_id] += 1

    duplicate_record_ids = [
        key
        for key, count in record_ids.items()
        if count > 1
    ]

    return (
        index,
        udise_duplicates,
        duplicate_record_ids,
        errors,
    )


# ============================================================
# RECORD VALIDATION
# ============================================================

def validate_record_structure(record):
    issues = []

    missing_sections = (
        REQUIRED_TOP_LEVEL - set(record.keys())
    )

    for section in sorted(missing_sections):
        issues.append(
            f"MISSING_SECTION:{section}"
        )

    metadata = record.get("metadata", {})

    if metadata.get("schema_version") != SCHEMA_VERSION:
        issues.append("INVALID_SCHEMA_VERSION")

    if not metadata.get("record_id"):
        issues.append("MISSING_RECORD_ID")

    if not metadata.get("udise_code"):
        issues.append("MISSING_UDISE")

    sources = metadata.get("sources_merged")

    if not isinstance(sources, list) or not sources:
        issues.append("MISSING_SOURCE_PROVENANCE")

    provenance = record.get("provenance")

    if not isinstance(provenance, dict):
        issues.append("MISSING_PROVENANCE")

    return issues


# ============================================================
# CONFIRMED MAPPING CHECKS
# ============================================================

def validate_mapping(raw, canonical):
    issues = []

    summary = raw.get("school_summary")
    profile = raw.get("school_profile")

    if not isinstance(summary, dict):
        summary = {}

    if not isinstance(profile, dict):
        profile = {}

    classification = canonical.get(
        "classification",
        {}
    )

    checks = [
        (
            "management_type",
            summary.get("schMgmtDesc"),
            classification.get(
                "management_type_desc"
            ),
        ),
        (
            "school_category",
            summary.get("schCatDesc"),
            classification.get(
                "school_category_desc"
            ),
        ),
        (
            "school_type",
            summary.get("schTypeDesc"),
            classification.get(
                "school_type"
            ),
        ),
        (
            "foundation_year",
            profile.get("estdYear"),
            classification.get(
                "estd_year"
            ),
        ),
    ]

    for field, raw_value, canonical_value in checks:

        if (
            meaningful(raw_value)
            and not meaningful(canonical_value)
        ):
            issues.append({
                "field": field,
                "type": "MAPPING_LOSS",
                "raw_value": raw_value,
                "canonical_value": canonical_value,
            })

        elif (
            meaningful(raw_value)
            and meaningful(canonical_value)
            and clean(raw_value)
            != clean(canonical_value)
        ):
            issues.append({
                "field": field,
                "type": "VALUE_MISMATCH",
                "raw_value": raw_value,
                "canonical_value": canonical_value,
            })

    return issues


# ============================================================
# ADDRESS VALIDATION
# ============================================================

def validate_address(raw, canonical):
    issues = []

    summary = raw.get("school_summary")
    profile = raw.get("school_profile")

    if not isinstance(summary, dict):
        summary = {}

    if not isinstance(profile, dict):
        profile = {}

    summary_address = summary.get("address")
    profile_address = profile.get("address")

    canonical_location = canonical.get(
        "identity_location",
        {}
    )

    canonical_address = canonical_location.get(
        "address"
    )

    alternatives = canonical_location.get(
        "alternate_source_addresses",
        []
    )

    provenance = canonical.get(
        "provenance",
        {}
    )

    address_provenance = provenance.get(
        "address",
        {}
    )

    if meaningful(summary_address):

        if clean(summary_address) != clean(
            canonical_address
        ):
            issues.append({
                "type": "PRIMARY_ADDRESS_NOT_SUMMARY",
                "summary_address": summary_address,
                "canonical_address": canonical_address,
            })

    elif meaningful(profile_address):

        if clean(profile_address) != clean(
            canonical_address
        ):
            issues.append({
                "type": "PROFILE_ADDRESS_NOT_USED_AS_FALLBACK",
                "profile_address": profile_address,
                "canonical_address": canonical_address,
            })

    if (
        meaningful(summary_address)
        and meaningful(profile_address)
        and clean(summary_address)
        != clean(profile_address)
    ):
        alt_values = [
            clean(item.get("value"))
            for item in alternatives
            if isinstance(item, dict)
        ]

        if clean(profile_address) not in alt_values:
            issues.append({
                "type": "ADDRESS_CONFLICT_NOT_PRESERVED",
                "summary_address": summary_address,
                "profile_address": profile_address,
            })

        if not address_provenance.get(
            "conflict_detected"
        ):
            issues.append({
                "type": "ADDRESS_CONFLICT_FLAG_MISSING"
            })

    return issues


# ============================================================
# YEARLY SNAPSHOT VALIDATION
# ============================================================

def validate_snapshots(
    canonical_dir,
    canonical_index,
):
    snapshot_path = (
        canonical_dir
        / "_yearly_snapshots.json"
    )

    result = {
        "exists": snapshot_path.exists(),
        "count": 0,
        "duplicate_udise": [],
        "missing_school_records": [],
        "invalid_year": [],
        "invalid_schema_version": [],
    }

    if not snapshot_path.exists():
        return result

    snapshots = load_json(snapshot_path)

    if not isinstance(snapshots, list):
        result["invalid_format"] = True
        return result

    result["count"] = len(snapshots)

    codes = Counter()

    for snapshot in snapshots:

        code = snapshot.get("udise_code")

        if code:
            codes[str(code)] += 1

        if snapshot.get(
            "academic_year"
        ) != ACADEMIC_YEAR:
            result["invalid_year"].append(code)

        if snapshot.get(
            "schema_version"
        ) != SCHEMA_VERSION:
            result[
                "invalid_schema_version"
            ].append(code)

        if str(code) not in canonical_index:
            result[
                "missing_school_records"
            ].append(code)

    result["duplicate_udise"] = [
        code
        for code, count in codes.items()
        if count > 1
    ]

    return result


# ============================================================
# ST ROCK'S SPECIAL CONFLICT CHECK
# ============================================================

def validate_st_rocks(canonical_index):
    code = "27220400499"

    result = {
        "udise_code": code,
        "found": False,
        "passed": False,
        "issues": [],
    }

    item = canonical_index.get(code)

    if not item:
        result["issues"].append(
            "CANONICAL_RECORD_NOT_FOUND"
        )
        return result

    result["found"] = True

    record = item["record"]

    location = record.get(
        "identity_location",
        {}
    )

    provenance = record.get(
        "provenance",
        {}
    )

    primary = location.get("address")

    alternatives = location.get(
        "alternate_source_addresses",
        []
    )

    expected_primary = (
        "St. Rocks High School, Road no. 1 "
        "Chruch pakhadi Sahar Village "
        "Andheri (east) Mumbai 400 099."
    )

    expected_alternate = (
        "RD NO 2 CHURCH PAKADI SCHOOL"
    )

    if clean(primary) != clean(
        expected_primary
    ):
        result["issues"].append(
            "PRIMARY_ADDRESS_INCORRECT"
        )

    alt_values = [
        clean(item.get("value"))
        for item in alternatives
        if isinstance(item, dict)
    ]

    if clean(expected_alternate) not in alt_values:
        result["issues"].append(
            "ALTERNATE_ADDRESS_NOT_PRESERVED"
        )

    address_info = provenance.get(
        "address",
        {}
    )

    if not address_info.get(
        "conflict_detected"
    ):
        result["issues"].append(
            "CONFLICT_FLAG_NOT_SET"
        )

    result["passed"] = (
        len(result["issues"]) == 0
    )

    return result


# ============================================================
# DATASET VALIDATOR
# ============================================================

def validate_dataset(name, config):
    print()
    print("=" * 78)
    print(f"VALIDATING: {name.upper()}")
    print("=" * 78)

    raw_index, raw_duplicates, raw_errors = (
        build_raw_index(
            config["raw"]
        )
    )

    (
        canonical_index,
        canonical_duplicates,
        duplicate_record_ids,
        canonical_errors,
    ) = build_canonical_index(
        config["canonical"]
    )

    raw_codes = set(raw_index)
    canonical_codes = set(canonical_index)

    missing_canonical = sorted(
        raw_codes - canonical_codes
    )

    canonical_without_raw = sorted(
        canonical_codes - raw_codes
    )

    structural_issues = []
    mapping_issues = []
    address_issues = []

    for code in sorted(
        raw_codes & canonical_codes
    ):
        raw = raw_index[code]["record"]
        canonical = canonical_index[code][
            "record"
        ]

        structure = validate_record_structure(
            canonical
        )

        if structure:
            structural_issues.append({
                "udise_code": code,
                "issues": structure,
            })

        mappings = validate_mapping(
            raw,
            canonical,
        )

        if mappings:
            mapping_issues.append({
                "udise_code": code,
                "issues": mappings,
            })

        addresses = validate_address(
            raw,
            canonical,
        )

        if addresses:
            address_issues.append({
                "udise_code": code,
                "issues": addresses,
            })

    snapshots = validate_snapshots(
        config["canonical"],
        canonical_index,
    )

    expected = config["expected"]

    count_pass = (
        len(raw_index) == expected
        and len(canonical_index) == expected
    )

    passed = all([
        count_pass,
        not raw_duplicates,
        not canonical_duplicates,
        not duplicate_record_ids,
        not raw_errors,
        not canonical_errors,
        not missing_canonical,
        not canonical_without_raw,
        not structural_issues,
        not mapping_issues,
        not address_issues,
        snapshots["exists"],
        snapshots["count"] == expected,
        not snapshots["duplicate_udise"],
        not snapshots["missing_school_records"],
        not snapshots["invalid_year"],
        not snapshots[
            "invalid_schema_version"
        ],
    ])

    print(
        f"Expected records          : {expected}"
    )
    print(
        f"Raw indexed               : {len(raw_index)}"
    )
    print(
        f"Canonical indexed         : {len(canonical_index)}"
    )
    print(
        f"Missing canonical         : {len(missing_canonical)}"
    )
    print(
        f"Canonical without raw     : {len(canonical_without_raw)}"
    )
    print(
        f"Duplicate UDISE           : {len(canonical_duplicates)}"
    )
    print(
        f"Duplicate record_id       : {len(duplicate_record_ids)}"
    )
    print(
        f"Structural issues         : {len(structural_issues)}"
    )
    print(
        f"Confirmed mapping issues  : {len(mapping_issues)}"
    )
    print(
        f"Address issues            : {len(address_issues)}"
    )
    print(
        f"Yearly snapshots          : {snapshots['count']}"
    )
    print(
        f"RESULT                    : "
        f"{'PASS' if passed else 'FAIL'}"
    )

    return {
        "passed": passed,
        "expected_records": expected,
        "raw_records": len(raw_index),
        "canonical_records": len(
            canonical_index
        ),
        "raw_duplicate_udise": raw_duplicates,
        "canonical_duplicate_udise": (
            canonical_duplicates
        ),
        "duplicate_record_ids": (
            duplicate_record_ids
        ),
        "raw_errors": raw_errors,
        "canonical_errors": canonical_errors,
        "missing_canonical": missing_canonical,
        "canonical_without_raw": (
            canonical_without_raw
        ),
        "structural_issues": (
            structural_issues
        ),
        "mapping_issues": mapping_issues,
        "address_issues": address_issues,
        "yearly_snapshots": snapshots,
        "_canonical_index": canonical_index,
    }


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 78)
    print("SCHOOLFINDER CANONICAL V4 VALIDATION")
    print("=" * 78)

    print(
        "Validation only - existing data will not be modified."
    )

    results = {}
    overall_pass = True

    mumbai_index = None

    for name, config in DATASETS.items():

        result = validate_dataset(
            name,
            config,
        )

        if name == "mumbai":
            mumbai_index = result.pop(
                "_canonical_index"
            )
        else:
            result.pop(
                "_canonical_index"
            )

        results[name] = result

        if not result["passed"]:
            overall_pass = False

    st_rocks = validate_st_rocks(
        mumbai_index or {}
    )

    if not st_rocks["passed"]:
        overall_pass = False

    results["special_checks"] = {
        "st_rocks_address_conflict": (
            st_rocks
        )
    }

    results["overall"] = {
        "passed": overall_pass,
        "schema_version": SCHEMA_VERSION,
        "academic_year": ACADEMIC_YEAR,
        "expected_total_records": 3253,
        "validated_total_records": sum(
            results[name][
                "canonical_records"
            ]
            for name in DATASETS
        ),
    }

    save_json(
        REPORT_PATH,
        results,
    )

    print()
    print("=" * 78)
    print("SPECIAL CHECK")
    print("=" * 78)

    print(
        "St. Rock's address conflict : "
        + (
            "PASS"
            if st_rocks["passed"]
            else "FAIL"
        )
    )

    print()
    print("=" * 78)

    if overall_pass:
        print(
            "FINAL RESULT: PASS - CANONICAL V4 VALIDATION SUCCESSFUL"
        )
    else:
        print(
            "FINAL RESULT: FAIL - REVIEW REPORT BEFORE GIT COMMIT"
        )

    print("=" * 78)

    print(
        f"Report: {REPORT_PATH}"
    )

    print()
    print(
        "No raw data was modified."
    )

    print(
        "No canonical data was modified."
    )


if __name__ == "__main__":
    main()