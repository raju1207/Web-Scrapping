import json
import re
import uuid
from pathlib import Path
from datetime import datetime


SCHEMA_VERSION = "4.0"

DATASETS = {
    "chandigarh": {
        "raw": Path("data/CBSE_SARAS/Chandigarh/schools"),
        "canonical": Path("data/canonical_v4/saras/chandigarh/schools"),
        "expected": 175,
    },
    "delhi": {
        "raw": Path("data/CBSE_SARAS/delhi/schools"),
        "canonical": Path("data/canonical_v4/saras/delhi/schools"),
        "expected": 2252,
    },
    "mumbai": {
        "raw": Path("data/CBSE_SARAS/mumbai/schools"),
        "canonical": Path("data/canonical_v4/saras/mumbai/schools"),
        "expected": 92,
    },
}

REPORT_PATH = Path(
    "data/audit/saras_canonical_v4_validation_report.json"
)

REQUIRED_SECTIONS = {
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
    "source_specific",
    "provenance",
    "entity_resolution",
}


# ============================================================
# HELPERS
# ============================================================

def load_json(path):
    with path.open(
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with path.open(
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )


def clean(value):
    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    value = " ".join(
        value.strip().split()
    )

    if not value:
        return None

    if value.casefold() in {
        "na",
        "n/a",
        "null",
        "none",
    }:
        return None

    return value


def clean_int(value):
    value = clean(value)

    if value is None:
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_opening_date(value):
    value = clean(value)

    if not value:
        return None

    for fmt in (
        "%d %b %Y",
        "%d %B %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(
                value,
                fmt,
            ).strftime("%Y-%m-%d")

        except ValueError:
            continue

    return None


AFFILIATION_PATTERN = re.compile(
    r"From\s*:\s*(.*?)\s+To\s*:\s*(.*)",
    re.IGNORECASE,
)


def parse_date(value):
    value = clean(value)

    if not value:
        return None

    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            return datetime.strptime(
                value,
                fmt,
            ).strftime("%Y-%m-%d")

        except ValueError:
            continue

    return None


def expected_affiliation_period(value):
    raw = clean(value)

    result = {
        "raw": raw,
        "period_type": None,
        "from": None,
        "to": None,
        "parse_status": None,
    }

    if not raw:
        result["parse_status"] = (
            "SOURCE_NOT_AVAILABLE"
        )
        return result

    match = AFFILIATION_PATTERN.match(
        raw
    )

    if match:
        result["period_type"] = (
            "fixed_period"
        )

        result["from"] = parse_date(
            match.group(1)
        )

        result["to"] = parse_date(
            match.group(2)
        )

        if (
            result["from"]
            and result["to"]
        ):
            result["parse_status"] = (
                "PARSED"
            )
        else:
            result["parse_status"] = (
                "UNPARSED_DATE_FORMAT"
            )

        return result

    if raw.casefold().startswith(
        "permanent"
    ):
        result["period_type"] = (
            "permanent"
        )

        result["parse_status"] = (
            "PERMANENT_NO_DATE_RANGE"
        )

        return result

    result["period_type"] = "other"
    result["parse_status"] = (
        "UNPARSED_SOURCE_TEXT"
    )

    return result


SCHOOL_CODE_PATTERN = re.compile(
    r"Sch\.\s*Code\s*:\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


def raw_school_code(listing):
    value = clean(
        listing.get(
            "affiliation_and_school_code"
        )
    )

    if not value:
        return None

    match = SCHOOL_CODE_PATTERN.search(
        value
    )

    if not match:
        return None

    return match.group(1).strip()


def expected_record_id(
    affiliation_number
):
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "schoolfinder:"
                f"saras:{affiliation_number}"
            ),
        )
    )


# ============================================================
# VALIDATE ONE RECORD
# ============================================================

def validate_record(
    raw,
    canonical,
    raw_path,
    canonical_path,
):
    errors = []

    metadata = (
        raw.get("metadata")
        if isinstance(
            raw.get("metadata"),
            dict,
        )
        else {}
    )

    listing = (
        raw.get("listing_data")
        if isinstance(
            raw.get("listing_data"),
            dict,
        )
        else {}
    )

    details = (
        raw.get("school_details")
        if isinstance(
            raw.get("school_details"),
            dict,
        )
        else {}
    )

    # --------------------------------------------------------
    # Required canonical sections
    # --------------------------------------------------------

    missing_sections = (
        REQUIRED_SECTIONS
        - set(canonical.keys())
    )

    if missing_sections:
        errors.append({
            "type":
                "MISSING_CANONICAL_SECTION",

            "sections":
                sorted(
                    missing_sections
                ),
        })

    cmeta = canonical.get(
        "metadata",
        {}
    )

    identity = canonical.get(
        "identity_location",
        {}
    )

    classification = canonical.get(
        "classification",
        {}
    )

    board = canonical.get(
        "board_affiliation",
        {}
    )

    principal = canonical.get(
        "principal",
        {}
    )

    provenance = canonical.get(
        "provenance",
        {}
    )

    source_specific = canonical.get(
        "source_specific",
        {}
    ).get(
        "saras",
        {}
    )

    # --------------------------------------------------------
    # Source affiliation
    # --------------------------------------------------------

    raw_affiliation = (
        clean(
            details.get(
                "Affiliation Number"
            )
        )
        or clean(
            listing.get(
                "affiliation_number"
            )
        )
        or clean(
            metadata.get(
                "affiliation_number"
            )
        )
    )

    canonical_affiliation = clean(
        board.get(
            "affiliation_number"
        )
    )

    if (
        raw_affiliation
        != canonical_affiliation
    ):
        errors.append({
            "type":
                "AFFILIATION_MISMATCH",

            "raw":
                raw_affiliation,

            "canonical":
                canonical_affiliation,
        })

    # --------------------------------------------------------
    # Deterministic record ID
    # --------------------------------------------------------

    if raw_affiliation:
        expected_id = (
            expected_record_id(
                raw_affiliation
            )
        )

        if (
            cmeta.get("record_id")
            != expected_id
        ):
            errors.append({
                "type":
                    "RECORD_ID_MISMATCH",

                "expected":
                    expected_id,

                "canonical":
                    cmeta.get(
                        "record_id"
                    ),
            })

    # --------------------------------------------------------
    # Schema
    # --------------------------------------------------------

    if (
        cmeta.get(
            "schema_version"
        )
        != SCHEMA_VERSION
    ):
        errors.append({
            "type":
                "SCHEMA_VERSION_MISMATCH",

            "canonical":
                cmeta.get(
                    "schema_version"
                ),
        })

    # SARAS must not invent UDISE.
    if (
        cmeta.get("udise_code")
        is not None
    ):
        errors.append({
            "type":
                "SARAS_UDISE_NOT_NULL",

            "value":
                cmeta.get(
                    "udise_code"
                ),
        })

    # --------------------------------------------------------
    # Identity/location
    # --------------------------------------------------------

    comparisons = {
        "school_name": (
            clean(
                details.get(
                    "Name of Institution"
                )
            ),
            clean(
                identity.get(
                    "school_name"
                )
            ),
        ),

        "state": (
            clean(
                details.get(
                    "State"
                )
            ),
            clean(
                identity.get(
                    "state"
                )
            ),
        ),

        "district": (
            clean(
                details.get(
                    "District"
                )
            ),
            clean(
                identity.get(
                    "district"
                )
            ),
        ),

        "address": (
            clean(
                details.get(
                    "Postal Address"
                )
            ),
            clean(
                identity.get(
                    "address"
                )
            ),
        ),

        "pincode": (
            clean(
                details.get(
                    "Pin Code"
                )
            ),
            clean(
                identity.get(
                    "pincode"
                )
            ),
        ),

        "website": (
            clean(
                details.get(
                    "Website"
                )
            ),
            clean(
                identity.get(
                    "website"
                )
            ),
        ),
    }

    for field, (
        raw_value,
        canonical_value,
    ) in comparisons.items():

        if raw_value != canonical_value:
            errors.append({
                "type":
                    "FIELD_MAPPING_MISMATCH",

                "field":
                    field,

                "raw":
                    raw_value,

                "canonical":
                    canonical_value,
            })

    # --------------------------------------------------------
    # Foundation/opening
    # --------------------------------------------------------

    expected_foundation = clean_int(
        details.get(
            "Year of Foundation"
        )
    )

    if (
        classification.get(
            "estd_year"
        )
        != expected_foundation
    ):
        errors.append({
            "type":
                "FOUNDATION_YEAR_MISMATCH",

            "raw":
                expected_foundation,

            "canonical":
                classification.get(
                    "estd_year"
                ),
        })

    raw_opening = clean(
        details.get(
            "Date of First Opening of School"
        )
    )

    expected_opening = (
        parse_opening_date(
            raw_opening
        )
    )

    if (
        classification.get(
            "first_opening_date"
        )
        != expected_opening
    ):
        errors.append({
            "type":
                "OPENING_DATE_MISMATCH",

            "raw":
                raw_opening,

            "expected":
                expected_opening,

            "canonical":
                classification.get(
                    "first_opening_date"
                ),
        })

    if (
        classification.get(
            "first_opening_date_raw"
        )
        != raw_opening
    ):
        errors.append({
            "type":
                "OPENING_DATE_RAW_NOT_PRESERVED",
        })

    # --------------------------------------------------------
    # Classification
    # --------------------------------------------------------

    classification_checks = {
        "school_level":
            clean(
                details.get(
                    "Status of The School"
                )
            ),

        "school_type":
            clean(
                details.get(
                    "School Type"
                )
            ),

        "trust_society_name":
            clean(
                details.get(
                    "Name of Trust/ Society/ Managing Committee"
                )
            ),
    }

    for field, expected in (
        classification_checks.items()
    ):
        actual = clean(
            classification.get(
                field
            )
        )

        if actual != expected:
            errors.append({
                "type":
                    "CLASSIFICATION_MISMATCH",

                "field":
                    field,

                "raw":
                    expected,

                "canonical":
                    actual,
            })

    # --------------------------------------------------------
    # CBSE board
    # --------------------------------------------------------

    if (
        board.get(
            "board_secondary"
        )
        != "CBSE"
    ):
        errors.append({
            "type":
                "BOARD_SECONDARY_INVALID",
        })

    # --------------------------------------------------------
    # CBSE school code
    # --------------------------------------------------------

    expected_school_code = (
        raw_school_code(
            listing
        )
    )

    if (
        clean(
            board.get(
                "cbse_school_code"
            )
        )
        != expected_school_code
    ):
        errors.append({
            "type":
                "CBSE_SCHOOL_CODE_MISMATCH",

            "raw":
                expected_school_code,

            "canonical":
                board.get(
                    "cbse_school_code"
                ),
        })

    # --------------------------------------------------------
    # Affiliation type
    # --------------------------------------------------------

    expected_type = clean(
        details.get(
            "Type of affiliation"
        )
    )

    if (
        clean(
            board.get(
                "affiliation_type"
            )
        )
        != expected_type
    ):
        errors.append({
            "type":
                "AFFILIATION_TYPE_MISMATCH",

            "raw":
                expected_type,

            "canonical":
                board.get(
                    "affiliation_type"
                ),
        })

    # --------------------------------------------------------
    # Affiliation period
    # --------------------------------------------------------

    expected_period = (
        expected_affiliation_period(
            details.get(
                "Affiliation Period"
            )
        )
    )

    period_checks = {
        "affiliation_period_raw":
            expected_period["raw"],

        "affiliation_status":
            expected_period[
                "period_type"
            ],

        "affiliation_from":
            expected_period["from"],

        "affiliation_to":
            expected_period["to"],

        "affiliation_period_parse_status":
            expected_period[
                "parse_status"
            ],
    }

    for field, expected in (
        period_checks.items()
    ):
        actual = board.get(
            field
        )

        if actual != expected:
            errors.append({
                "type":
                    "AFFILIATION_PERIOD_MISMATCH",

                "field":
                    field,

                "expected":
                    expected,

                "canonical":
                    actual,
            })

    # Permanent records must not receive
    # invented date ranges.
    if (
        expected_period[
            "period_type"
        ]
        == "permanent"
    ):
        if (
            board.get(
                "affiliation_from"
            )
            is not None
            or board.get(
                "affiliation_to"
            )
            is not None
        ):
            errors.append({
                "type":
                    "PERMANENT_DATES_INVENTED",
            })

    # --------------------------------------------------------
    # Principal
    # --------------------------------------------------------

    principal_checks = {
        "head_name":
            clean(
                details.get(
                    "Name of Principal/ Head of Institution"
                )
            ),

        "head_gender":
            clean(
                details.get(
                    "Gender"
                )
            ),

        "head_qualification":
            clean(
                details.get(
                    "Principal's Educational/Professional Qualifications:"
                )
            ),

        "head_total_experience_years":
            clean_int(
                details.get(
                    "No of Experience ( in Years ):"
                )
            ),

        "head_admin_experience_years":
            clean_int(
                details.get(
                    "Administrative:"
                )
            ),

        "head_teaching_experience_years":
            clean_int(
                details.get(
                    "Teaching:"
                )
            ),
    }

    for field, expected in (
        principal_checks.items()
    ):
        actual = principal.get(
            field
        )

        if actual != expected:
            errors.append({
                "type":
                    "PRINCIPAL_MAPPING_MISMATCH",

                "field":
                    field,

                "raw":
                    expected,

                "canonical":
                    actual,
            })

    # --------------------------------------------------------
    # Remarks
    # --------------------------------------------------------

    expected_remarks = clean(
        details.get(
            "Remarks, if any"
        )
    )

    if (
        clean(
            source_specific.get(
                "remarks"
            )
        )
        != expected_remarks
    ):
        errors.append({
            "type":
                "REMARKS_MISMATCH",
        })

    # --------------------------------------------------------
    # Provenance
    # --------------------------------------------------------

    if (
        provenance.get(
            "primary_source"
        )
        != "CBSE SARAS"
    ):
        errors.append({
            "type":
                "PROVENANCE_SOURCE_INVALID",
        })

    if (
        provenance.get(
            "source_priority"
        )
        != 2
    ):
        errors.append({
            "type":
                "PROVENANCE_PRIORITY_INVALID",
        })

    if (
        provenance.get(
            "source_legal_status"
        )
        != "pending_legal_review"
    ):
        errors.append({
            "type":
                "PROVENANCE_LEGAL_STATUS_INVALID",
        })

    # --------------------------------------------------------
    # No invented SARAS stats
    # --------------------------------------------------------

    stats = canonical.get(
        "student_teacher_statistics",
        {}
    )

    for field in (
        "total_boys",
        "total_girls",
        "total_students",
        "total_teachers",
        "student_teacher_ratio",
    ):
        if stats.get(field) is not None:
            errors.append({
                "type":
                    "UNSUPPORTED_STAT_VALUE",

                "field":
                    field,

                "value":
                    stats.get(field),
            })

    infrastructure = canonical.get(
        "infrastructure",
        {}
    )

    if (
        infrastructure.get(
            "infra_score"
        )
        is not None
    ):
        errors.append({
            "type":
                "UNSUPPORTED_INFRA_SCORE",
        })

    return errors


# ============================================================
# DATASET VALIDATION
# ============================================================

def validate_dataset(
    dataset_name,
    config,
):
    print()
    print("=" * 76)
    print(
        f"VALIDATING SARAS: "
        f"{dataset_name.upper()}"
    )
    print("=" * 76)

    raw_dir = config["raw"]
    canonical_dir = (
        config["canonical"]
    )

    expected_count = (
        config["expected"]
    )

    raw_files = sorted(
        raw_dir.glob("*.json")
    )

    canonical_files = sorted(
        canonical_dir.glob("*.json")
    )

    result = {
        "dataset":
            dataset_name,

        "expected_count":
            expected_count,

        "raw_count":
            len(raw_files),

        "canonical_count":
            len(canonical_files),

        "missing_canonical_records":
            [],

        "extra_canonical_records":
            [],

        "duplicate_affiliation_numbers":
            [],

        "duplicate_record_ids":
            [],

        "records_with_errors":
            [],

        "error_type_counts":
            {},

        "permanent_affiliation_records":
            0,

        "fixed_period_records":
            0,

        "other_period_records":
            0,

        "validation_status":
            "PASS",
    }

    raw_by_affiliation = {}

    for raw_path in raw_files:
        raw = load_json(
            raw_path
        )

        metadata = raw.get(
            "metadata",
            {}
        )

        listing = raw.get(
            "listing_data",
            {}
        )

        details = raw.get(
            "school_details",
            {}
        )

        affiliation = (
            clean(
                details.get(
                    "Affiliation Number"
                )
            )
            or clean(
                listing.get(
                    "affiliation_number"
                )
            )
            or clean(
                metadata.get(
                    "affiliation_number"
                )
            )
        )

        if affiliation:
            if affiliation in (
                raw_by_affiliation
            ):
                result[
                    "duplicate_affiliation_numbers"
                ].append(
                    affiliation
                )

            raw_by_affiliation[
                affiliation
            ] = raw_path

    canonical_by_affiliation = {}
    record_ids = {}

    for canonical_path in (
        canonical_files
    ):
        canonical = load_json(
            canonical_path
        )

        affiliation = clean(
            canonical.get(
                "board_affiliation",
                {},
            ).get(
                "affiliation_number"
            )
        )

        record_id = clean(
            canonical.get(
                "metadata",
                {},
            ).get(
                "record_id"
            )
        )

        if affiliation:
            if affiliation in (
                canonical_by_affiliation
            ):
                result[
                    "duplicate_affiliation_numbers"
                ].append(
                    affiliation
                )

            canonical_by_affiliation[
                affiliation
            ] = canonical_path

        if record_id:
            if record_id in record_ids:
                result[
                    "duplicate_record_ids"
                ].append(
                    record_id
                )

            record_ids[
                record_id
            ] = canonical_path

    raw_ids = set(
        raw_by_affiliation.keys()
    )

    canonical_ids = set(
        canonical_by_affiliation.keys()
    )

    result[
        "missing_canonical_records"
    ] = sorted(
        raw_ids
        - canonical_ids
    )

    result[
        "extra_canonical_records"
    ] = sorted(
        canonical_ids
        - raw_ids
    )

    error_type_counts = {}

    for affiliation in sorted(
        raw_ids
        & canonical_ids
    ):
        raw_path = (
            raw_by_affiliation[
                affiliation
            ]
        )

        canonical_path = (
            canonical_by_affiliation[
                affiliation
            ]
        )

        raw = load_json(
            raw_path
        )

        canonical = load_json(
            canonical_path
        )

        details = raw.get(
            "school_details",
            {}
        )

        period = (
            expected_affiliation_period(
                details.get(
                    "Affiliation Period"
                )
            )
        )

        if (
            period["period_type"]
            == "permanent"
        ):
            result[
                "permanent_affiliation_records"
            ] += 1

        elif (
            period["period_type"]
            == "fixed_period"
        ):
            result[
                "fixed_period_records"
            ] += 1

        else:
            result[
                "other_period_records"
            ] += 1

        errors = validate_record(
            raw,
            canonical,
            raw_path,
            canonical_path,
        )

        if errors:
            result[
                "records_with_errors"
            ].append({
                "affiliation_number":
                    affiliation,

                "raw_file":
                    str(raw_path),

                "canonical_file":
                    str(
                        canonical_path
                    ),

                "errors":
                    errors,
            })

            for error in errors:
                error_type = (
                    error["type"]
                )

                error_type_counts[
                    error_type
                ] = (
                    error_type_counts.get(
                        error_type,
                        0,
                    )
                    + 1
                )

    result[
        "error_type_counts"
    ] = error_type_counts

    structural_failure = any([
        len(raw_files)
        != expected_count,

        len(canonical_files)
        != expected_count,

        bool(
            result[
                "missing_canonical_records"
            ]
        ),

        bool(
            result[
                "extra_canonical_records"
            ]
        ),

        bool(
            result[
                "duplicate_affiliation_numbers"
            ]
        ),

        bool(
            result[
                "duplicate_record_ids"
            ]
        ),

        bool(
            result[
                "records_with_errors"
            ]
        ),
    ])

    if structural_failure:
        result[
            "validation_status"
        ] = "FAIL"

    print(
        f"Expected records             : "
        f"{expected_count}"
    )

    print(
        f"Raw records                  : "
        f"{len(raw_files)}"
    )

    print(
        f"Canonical records            : "
        f"{len(canonical_files)}"
    )

    print(
        f"Missing canonical            : "
        f"{len(result['missing_canonical_records'])}"
    )

    print(
        f"Extra canonical              : "
        f"{len(result['extra_canonical_records'])}"
    )

    print(
        f"Duplicate affiliation numbers: "
        f"{len(result['duplicate_affiliation_numbers'])}"
    )

    print(
        f"Duplicate record IDs         : "
        f"{len(result['duplicate_record_ids'])}"
    )

    print(
        f"Fixed affiliation periods    : "
        f"{result['fixed_period_records']}"
    )

    print(
        f"Permanent affiliations       : "
        f"{result['permanent_affiliation_records']}"
    )

    print(
        f"Other affiliation periods    : "
        f"{result['other_period_records']}"
    )

    print(
        f"Records with errors          : "
        f"{len(result['records_with_errors'])}"
    )

    print(
        f"STATUS                       : "
        f"{result['validation_status']}"
    )

    return result


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 76)
    print(
        "SARAS CANONICAL V4 VALIDATION"
    )
    print("=" * 76)

    print(
        "Read-only validation."
    )

    report = {
        "schema_version":
            SCHEMA_VERSION,

        "datasets": {},

        "overall_status":
            "PASS",
    }

    for (
        dataset_name,
        config,
    ) in DATASETS.items():

        result = validate_dataset(
            dataset_name,
            config,
        )

        report[
            "datasets"
        ][dataset_name] = result

        if (
            result[
                "validation_status"
            ]
            != "PASS"
        ):
            report[
                "overall_status"
            ] = "FAIL"

    save_json(
        REPORT_PATH,
        report,
    )

    print()
    print("=" * 76)
    print(
        "FINAL SARAS CANONICAL V4 "
        f"STATUS: "
        f"{report['overall_status']}"
    )
    print("=" * 76)

    print(
        f"Report: {REPORT_PATH}"
    )

    print()
    print(
        "No raw or canonical school "
        "records were modified."
    )


if __name__ == "__main__":
    main()