import json
import re
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime, timezone


# ============================================================
# CONFIGURATION
# ============================================================

DATASETS = {
    "chandigarh": Path("data/CBSE_SARAS/Chandigarh/schools"),
    "delhi": Path("data/CBSE_SARAS/delhi/schools"),
    "mumbai": Path("data/CBSE_SARAS/mumbai/schools"),
}

REPORT_PATH = Path(
    "data/audit/saras_raw_data_audit_report.json"
)


# ============================================================
# EXPECTED SARAS STRUCTURE
# ============================================================

EXPECTED_TOP_LEVEL = {
    "metadata",
    "listing_data",
    "school_details",
    "links",
}

IMPORTANT_FIELDS = {
    "school_name": "Name of Institution",
    "affiliation_number": "Affiliation Number",
    "state": "State",
    "district": "District",
    "address": "Postal Address",
    "pincode": "Pin Code",
    "website": "Website",
    "foundation_year": "Year of Foundation",
    "first_opening_date": "Date of First Opening of School",
    "principal_name": "Name of Principal/ Head of Institution",
    "principal_gender": "Gender",
    "principal_qualification":
        "Principal's Educational/Professional Qualifications:",
    "principal_experience_total":
        "No of Experience ( in Years ):",
    "principal_experience_admin":
        "Administrative:",
    "principal_experience_teaching":
        "Teaching:",
    "school_status": "Status of The School",
    "school_type": "School Type",
    "affiliation_type": "Type of affiliation",
    "affiliation_period": "Affiliation Period",
    "trust_society":
        "Name of Trust/ Society/ Managing Committee",
    "remarks": "Remarks, if any",
}


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(
        timezone.utc
    ).isoformat()


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


def safe_dict(value):
    return (
        value
        if isinstance(value, dict)
        else {}
    )


def clean_string(value):
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if not isinstance(value, str):
        return value

    value = " ".join(
        value.strip().split()
    )

    if not value:
        return None

    return value


def meaningful(value):
    value = clean_string(value)

    if value is None:
        return False

    if isinstance(value, str):
        return bool(value)

    if isinstance(value, (list, dict)):
        return bool(value)

    return True


def classify_value(value):
    """
    Distinguishes actual nulls, empty strings,
    booleans and populated values.
    """

    if value is None:
        return "NULL"

    if isinstance(value, bool):
        return "BOOLEAN"

    if isinstance(value, str):
        if not value.strip():
            return "EMPTY"

        return "VALID"

    if isinstance(value, (list, dict)):
        if not value:
            return "EMPTY"

        return "VALID"

    return "VALID"


def normalize_affiliation(value):
    value = clean_string(value)

    if value is None:
        return None

    return str(value).strip()


def normalize_name(value):
    value = clean_string(value)

    if not isinstance(value, str):
        return None

    value = value.upper()

    value = re.sub(
        r"[^A-Z0-9]+",
        " ",
        value,
    )

    return " ".join(
        value.split()
    )


# ============================================================
# AFFILIATION PERIOD CHECK
# ============================================================

AFFILIATION_PATTERN = re.compile(
    r"From\s*:\s*(.*?)\s+To\s*:\s*(.*)",
    re.IGNORECASE,
)


def inspect_affiliation_period(value):
    value = clean_string(value)

    if not isinstance(value, str):
        return {
            "present": False,
            "parseable": False,
            "from": None,
            "to": None,
        }

    match = AFFILIATION_PATTERN.match(
        value
    )

    if not match:
        return {
            "present": True,
            "parseable": False,
            "from": None,
            "to": None,
        }

    return {
        "present": True,
        "parseable": True,
        "from": clean_string(
            match.group(1)
        ),
        "to": clean_string(
            match.group(2)
        ),
    }


# ============================================================
# SCHOOL CODE CHECK
# ============================================================

SCHOOL_CODE_PATTERN = re.compile(
    r"Sch\.\s*Code\s*:\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


def extract_listing_school_code(
    listing_data
):
    value = listing_data.get(
        "affiliation_and_school_code"
    )

    if not isinstance(value, str):
        return None

    match = SCHOOL_CODE_PATTERN.search(
        value
    )

    if not match:
        return None

    return match.group(1).strip()


# ============================================================
# DATASET AUDIT
# ============================================================

def audit_dataset(
    dataset_name,
    school_dir,
):
    print()
    print("=" * 76)
    print(
        f"AUDITING SARAS: "
        f"{dataset_name.upper()}"
    )
    print("=" * 76)

    result = {
        "dataset": dataset_name,
        "directory": str(school_dir),

        "total_files": 0,
        "valid_json": 0,
        "invalid_json": 0,

        "invalid_json_files": [],

        "top_level_structure": {
            "complete": 0,
            "missing_keys": Counter(),
            "unexpected_keys": Counter(),
        },

        "section_types": {
            "metadata_not_object": 0,
            "listing_data_not_object": 0,
            "school_details_not_object": 0,
            "links_not_list": 0,
        },

        "field_status": {
            field: Counter()
            for field in IMPORTANT_FIELDS
        },

        "affiliation_numbers": {
            "present": 0,
            "missing": 0,
            "duplicates": [],
        },

        "listing_school_code": {
            "present": 0,
            "missing": 0,
            "unique_count": 0,
            "duplicates": [],
        },

        "affiliation_period": {
            "present": 0,
            "missing": 0,
            "parseable": 0,
            "unparseable": 0,
            "unparseable_examples": [],
        },

        "metadata_consistency": {
            "affiliation_mismatch": [],
            "state_mismatch": [],
            "district_mismatch": [],
            "invalid_metadata_district_type": [],
        },

        "school_name_duplicates": [],
        "school_name_pincode_duplicates": [],

        "records_with_any_missing_important_field": 0,

        "school_details_keys": Counter(),

        "examples": {
            "missing_affiliation": [],
            "missing_school_name": [],
            "missing_district": [],
            "missing_pincode": [],
        },
    }

    if not school_dir.exists():
        result["directory_missing"] = True

        print(
            f"[ERROR] Directory not found: "
            f"{school_dir}"
        )

        return result

    files = sorted(
        school_dir.glob("*.json")
    )

    result["total_files"] = len(files)

    affiliation_counter = Counter()
    school_code_counter = Counter()

    normalized_name_counter = Counter()
    name_pincode_counter = Counter()

    for path in files:

        try:
            raw = load_json(path)

        except Exception as exc:
            result["invalid_json"] += 1

            result[
                "invalid_json_files"
            ].append({
                "file": str(path),
                "error": str(exc),
            })

            continue

        result["valid_json"] += 1

        if not isinstance(raw, dict):
            result["invalid_json"] += 1
            result["valid_json"] -= 1

            result[
                "invalid_json_files"
            ].append({
                "file": str(path),
                "error":
                    "ROOT_JSON_NOT_OBJECT",
            })

            continue

        # ----------------------------------------------------
        # Top-level structure
        # ----------------------------------------------------

        actual_keys = set(raw.keys())

        missing_keys = (
            EXPECTED_TOP_LEVEL
            - actual_keys
        )

        unexpected_keys = (
            actual_keys
            - EXPECTED_TOP_LEVEL
        )

        if not missing_keys:
            result[
                "top_level_structure"
            ]["complete"] += 1

        for key in missing_keys:
            result[
                "top_level_structure"
            ]["missing_keys"][key] += 1

        for key in unexpected_keys:
            result[
                "top_level_structure"
            ]["unexpected_keys"][key] += 1

        metadata_raw = raw.get(
            "metadata"
        )

        listing_raw = raw.get(
            "listing_data"
        )

        details_raw = raw.get(
            "school_details"
        )

        links_raw = raw.get(
            "links"
        )

        if not isinstance(
            metadata_raw,
            dict
        ):
            result[
                "section_types"
            ]["metadata_not_object"] += 1

        if not isinstance(
            listing_raw,
            dict
        ):
            result[
                "section_types"
            ]["listing_data_not_object"] += 1

        if not isinstance(
            details_raw,
            dict
        ):
            result[
                "section_types"
            ]["school_details_not_object"] += 1

        if not isinstance(
            links_raw,
            list
        ):
            result[
                "section_types"
            ]["links_not_list"] += 1

        metadata = safe_dict(
            metadata_raw
        )

        listing = safe_dict(
            listing_raw
        )

        details = safe_dict(
            details_raw
        )

        # ----------------------------------------------------
        # Discover all school_details keys
        # ----------------------------------------------------

        for key in details.keys():
            result[
                "school_details_keys"
            ][key] += 1

        # ----------------------------------------------------
        # Important field completeness
        # ----------------------------------------------------

        any_missing = False

        for canonical_field, raw_key in (
            IMPORTANT_FIELDS.items()
        ):
            if raw_key not in details:
                status = "FIELD_NOT_PRESENT"
            else:
                status = classify_value(
                    details.get(raw_key)
                )

            result[
                "field_status"
            ][canonical_field][status] += 1

            if status != "VALID":
                any_missing = True

        if any_missing:
            result[
                "records_with_any_missing_important_field"
            ] += 1

        # ----------------------------------------------------
        # Important identifiers
        # ----------------------------------------------------

        affiliation = normalize_affiliation(
            details.get(
                "Affiliation Number"
            )
        )

        if not affiliation:
            affiliation = (
                normalize_affiliation(
                    listing.get(
                        "affiliation_number"
                    )
                )
            )

        if not affiliation:
            affiliation = (
                normalize_affiliation(
                    metadata.get(
                        "affiliation_number"
                    )
                )
            )

        if affiliation:
            result[
                "affiliation_numbers"
            ]["present"] += 1

            affiliation_counter[
                affiliation
            ] += 1

        else:
            result[
                "affiliation_numbers"
            ]["missing"] += 1

            if len(
                result["examples"][
                    "missing_affiliation"
                ]
            ) < 10:
                result["examples"][
                    "missing_affiliation"
                ].append(
                    str(path)
                )

        # ----------------------------------------------------
        # Listing school code
        # ----------------------------------------------------

        school_code = (
            extract_listing_school_code(
                listing
            )
        )

        if school_code:
            result[
                "listing_school_code"
            ]["present"] += 1

            school_code_counter[
                school_code
            ] += 1

        else:
            result[
                "listing_school_code"
            ]["missing"] += 1

        # ----------------------------------------------------
        # School name duplicate checks
        # ----------------------------------------------------

        school_name = clean_string(
            details.get(
                "Name of Institution"
            )
        )

        normalized_name = normalize_name(
            school_name
        )

        pincode = clean_string(
            details.get("Pin Code")
        )

        if normalized_name:
            normalized_name_counter[
                normalized_name
            ] += 1

            if pincode:
                name_pincode_counter[
                    (
                        normalized_name,
                        str(pincode).strip(),
                    )
                ] += 1

        elif len(
            result["examples"][
                "missing_school_name"
            ]
        ) < 10:
            result["examples"][
                "missing_school_name"
            ].append(
                str(path)
            )

        # ----------------------------------------------------
        # Missing location examples
        # ----------------------------------------------------

        district = clean_string(
            details.get("District")
        )

        if not meaningful(district):
            if len(
                result["examples"][
                    "missing_district"
                ]
            ) < 10:
                result["examples"][
                    "missing_district"
                ].append(
                    str(path)
                )

        if not meaningful(pincode):
            if len(
                result["examples"][
                    "missing_pincode"
                ]
            ) < 10:
                result["examples"][
                    "missing_pincode"
                ].append(
                    str(path)
                )

        # ----------------------------------------------------
        # Affiliation period
        # ----------------------------------------------------

        period = (
            inspect_affiliation_period(
                details.get(
                    "Affiliation Period"
                )
            )
        )

        if period["present"]:
            result[
                "affiliation_period"
            ]["present"] += 1

            if period["parseable"]:
                result[
                    "affiliation_period"
                ]["parseable"] += 1

            else:
                result[
                    "affiliation_period"
                ]["unparseable"] += 1

                examples = result[
                    "affiliation_period"
                ][
                    "unparseable_examples"
                ]

                if len(examples) < 20:
                    examples.append({
                        "file": str(path),
                        "value": details.get(
                            "Affiliation Period"
                        ),
                    })

        else:
            result[
                "affiliation_period"
            ]["missing"] += 1

        # ----------------------------------------------------
        # Metadata consistency
        # ----------------------------------------------------

        metadata_affiliation = (
            normalize_affiliation(
                metadata.get(
                    "affiliation_number"
                )
            )
        )

        if (
            affiliation
            and metadata_affiliation
            and affiliation
            != metadata_affiliation
        ):
            result[
                "metadata_consistency"
            ][
                "affiliation_mismatch"
            ].append({
                "file": str(path),
                "school_details":
                    affiliation,
                "metadata":
                    metadata_affiliation,
            })

        details_state = clean_string(
            details.get("State")
        )

        metadata_state = clean_string(
            metadata.get("state")
        )

        if (
            meaningful(details_state)
            and meaningful(metadata_state)
            and isinstance(
                metadata_state,
                str
            )
            and str(
                details_state
            ).casefold()
            != metadata_state.casefold()
        ):
            result[
                "metadata_consistency"
            ][
                "state_mismatch"
            ].append({
                "file": str(path),
                "school_details":
                    details_state,
                "metadata":
                    metadata_state,
            })

        metadata_district = (
            metadata.get("district")
        )

        # Delhi sample already showed a boolean district.
        if (
            metadata_district is not None
            and not isinstance(
                metadata_district,
                str
            )
        ):
            result[
                "metadata_consistency"
            ][
                "invalid_metadata_district_type"
            ].append({
                "file": str(path),
                "value":
                    metadata_district,
                "type":
                    type(
                        metadata_district
                    ).__name__,
            })

        elif (
            isinstance(
                metadata_district,
                str
            )
            and meaningful(district)
            and metadata_district.strip()
            .casefold()
            != str(
                district
            ).strip().casefold()
        ):
            result[
                "metadata_consistency"
            ][
                "district_mismatch"
            ].append({
                "file": str(path),
                "school_details":
                    district,
                "metadata":
                    metadata_district,
            })

    # --------------------------------------------------------
    # Duplicate summaries
    # --------------------------------------------------------

    result[
        "affiliation_numbers"
    ]["duplicates"] = [
        {
            "affiliation_number": key,
            "count": count,
        }
        for key, count
        in affiliation_counter.items()
        if count > 1
    ]

    result[
        "listing_school_code"
    ]["unique_count"] = len(
        school_code_counter
    )

    result[
        "listing_school_code"
    ]["duplicates"] = [
        {
            "school_code": key,
            "count": count,
        }
        for key, count
        in school_code_counter.items()
        if count > 1
    ]

    result[
        "school_name_duplicates"
    ] = [
        {
            "normalized_name": key,
            "count": count,
        }
        for key, count
        in normalized_name_counter.items()
        if count > 1
    ]

    result[
        "school_name_pincode_duplicates"
    ] = [
        {
            "normalized_name": key[0],
            "pincode": key[1],
            "count": count,
        }
        for key, count
        in name_pincode_counter.items()
        if count > 1
    ]

    # Convert Counters before JSON serialization.
    result[
        "top_level_structure"
    ]["missing_keys"] = dict(
        result[
            "top_level_structure"
        ]["missing_keys"]
    )

    result[
        "top_level_structure"
    ]["unexpected_keys"] = dict(
        result[
            "top_level_structure"
        ]["unexpected_keys"]
    )

    result["field_status"] = {
        key: dict(value)
        for key, value
        in result["field_status"].items()
    }

    result[
        "school_details_keys"
    ] = dict(
        sorted(
            result[
                "school_details_keys"
            ].items()
        )
    )

    # --------------------------------------------------------
    # Console summary
    # --------------------------------------------------------

    print(
        f"Total JSON files              : "
        f"{result['total_files']}"
    )

    print(
        f"Valid JSON                    : "
        f"{result['valid_json']}"
    )

    print(
        f"Invalid JSON                  : "
        f"{result['invalid_json']}"
    )

    print(
        f"Complete top-level structure  : "
        f"{result['top_level_structure']['complete']}"
    )

    print(
        f"Affiliation present           : "
        f"{result['affiliation_numbers']['present']}"
    )

    print(
        f"Affiliation missing           : "
        f"{result['affiliation_numbers']['missing']}"
    )

    print(
        f"Duplicate affiliation numbers : "
        f"{len(result['affiliation_numbers']['duplicates'])}"
    )

    print(
        f"Listing school code present   : "
        f"{result['listing_school_code']['present']}"
    )

    print(
        f"Unique listing school codes   : "
        f"{result['listing_school_code']['unique_count']}"
    )

    print(
        f"Affiliation period parseable  : "
        f"{result['affiliation_period']['parseable']}"
    )

    print(
        f"Affiliation period missing    : "
        f"{result['affiliation_period']['missing']}"
    )

    print(
        f"Affiliation period unparseable: "
        f"{result['affiliation_period']['unparseable']}"
    )

    print(
        f"Invalid metadata district type: "
        f"{len(result['metadata_consistency']['invalid_metadata_district_type'])}"
    )

    return result


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 76)
    print("CBSE SARAS RAW DATA AUDIT")
    print("=" * 76)

    print(
        "Read-only audit. Raw SARAS files "
        "will NOT be modified."
    )

    report = {
        "generated_at": utc_now(),
        "source": "CBSE SARAS",
        "datasets": {},
        "totals": {},
    }

    total_files = 0
    valid_json = 0
    invalid_json = 0
    total_affiliations = 0

    for dataset_name, directory in (
        DATASETS.items()
    ):
        result = audit_dataset(
            dataset_name,
            directory,
        )

        report["datasets"][
            dataset_name
        ] = result

        total_files += result.get(
            "total_files",
            0,
        )

        valid_json += result.get(
            "valid_json",
            0,
        )

        invalid_json += result.get(
            "invalid_json",
            0,
        )

        total_affiliations += (
            result
            .get(
                "affiliation_numbers",
                {}
            )
            .get(
                "present",
                0,
            )
        )

    report["totals"] = {
        "total_files": total_files,
        "valid_json": valid_json,
        "invalid_json": invalid_json,
        "affiliation_numbers_present":
            total_affiliations,
    }

    save_json(
        REPORT_PATH,
        report,
    )

    print()
    print("=" * 76)
    print("SARAS RAW AUDIT COMPLETED")
    print("=" * 76)

    print(
        f"Total files : {total_files}"
    )

    print(
        f"Valid JSON  : {valid_json}"
    )

    print(
        f"Invalid JSON: {invalid_json}"
    )

    print(
        f"Report      : {REPORT_PATH}"
    )

    print()
    print(
        "No SARAS raw data was modified."
    )


if __name__ == "__main__":
    main()