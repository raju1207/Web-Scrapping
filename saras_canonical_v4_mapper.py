import json
import re
import uuid
import hashlib
from pathlib import Path
from datetime import datetime, timezone


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "4.0"

DATASETS = {
    "chandigarh": Path(
        "data/CBSE_SARAS/Chandigarh/schools"
    ),
    "delhi": Path(
        "data/CBSE_SARAS/delhi/schools"
    ),
    "mumbai": Path(
        "data/CBSE_SARAS/mumbai/schools"
    ),
}

OUTPUT_ROOT = Path(
    "data/canonical_v4/saras"
)

SOURCE_NAME = "CBSE SARAS"
SOURCE_CODE = "SARAS"
SOURCE_TYPE = "Official CBSE Affiliation Directory"
SOURCE_PRIORITY = 2

# Kept conservative until legal/source-use review is finalized.
SOURCE_LEGAL_STATUS = "pending_legal_review"


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
    value = clean_string(value)

    if value is None:
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def meaningful(value):
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, dict)):
        return bool(value)

    return True


# ============================================================
# IDENTITY
# ============================================================

def make_record_id(
    affiliation_number
):
    """
    Stable SARAS source-layer UUID.

    Same affiliation number generates the
    same UUID on every rerun.
    """

    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "schoolfinder:"
                f"saras:{affiliation_number}"
            ),
        )
    )


def normalize_identity_text(value):
    value = clean_string(value)

    if not value:
        return ""

    value = value.casefold()

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return " ".join(
        value.split()
    )


def fallback_identity_hash(
    school_name,
    pincode,
    board="CBSE",
):
    """
    Useful later during entity resolution.

    This is NOT used as the SARAS primary key,
    because affiliation number is available
    for all audited SARAS records.
    """

    text = "|".join([
        normalize_identity_text(
            school_name
        ),
        str(pincode or "").strip(),
        board.casefold(),
    ])

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ============================================================
# URL NORMALIZATION
# ============================================================

def normalize_website(value):
    value = clean_string(value)

    if not value:
        return None

    return value


# ============================================================
# CBSE SCHOOL CODE
# ============================================================

SCHOOL_CODE_PATTERN = re.compile(
    r"Sch\.\s*Code\s*:\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


def extract_cbse_school_code(
    listing
):
    value = clean_string(
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


# ============================================================
# AFFILIATION PERIOD
# ============================================================

AFFILIATION_PERIOD_PATTERN = re.compile(
    r"From\s*:\s*(.*?)\s+To\s*:\s*(.*)",
    re.IGNORECASE,
)


def normalize_date_string(value):
    """
    Source uses DD/MM/YYYY.

    We store ISO YYYY-MM-DD when safely
    parseable. Otherwise return None.
    """

    value = clean_string(value)

    if not value:
        return None

    for fmt in (
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b %Y",
        "%d %B %Y",
    ):
        try:
            dt = datetime.strptime(
                value,
                fmt,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            continue

    return None


def parse_affiliation_period(value):
    """
    Handles two observed cases:

    1. From : DD/MM/YYYY To : DD/MM/YYYY
    2. Permanent (...court case text...)

    No dates are invented for permanent
    or otherwise non-date source values.
    """

    raw = clean_string(value)

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

    match = (
        AFFILIATION_PERIOD_PATTERN.match(
            raw
        )
    )

    if match:
        source_from = clean_string(
            match.group(1)
        )

        source_to = clean_string(
            match.group(2)
        )

        parsed_from = (
            normalize_date_string(
                source_from
            )
        )

        parsed_to = (
            normalize_date_string(
                source_to
            )
        )

        result["period_type"] = (
            "fixed_period"
        )

        result["from"] = parsed_from
        result["to"] = parsed_to

        if parsed_from and parsed_to:
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

    result["period_type"] = (
        "other"
    )

    result["parse_status"] = (
        "UNPARSED_SOURCE_TEXT"
    )

    return result


# ============================================================
# FIRST OPENING DATE
# ============================================================

def parse_first_opening_date(
    value
):
    value = clean_string(value)

    if not value:
        return None

    for fmt in (
        "%d %b %Y",
        "%d %B %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            dt = datetime.strptime(
                value,
                fmt,
            )

            return dt.strftime(
                "%Y-%m-%d"
            )

        except ValueError:
            continue

    # Do not invent/alter unrecognized dates.
    return None


# ============================================================
# DATA QUALITY
# ============================================================

def build_quality_flags(
    details,
    listing,
    period_info,
):
    flags = []

    if not clean_string(
        details.get("Pin Code")
    ):
        flags.append(
            "SOURCE_FIELD_NULL:PINCODE"
        )

    if not clean_string(
        details.get("Website")
    ):
        flags.append(
            "SOURCE_FIELD_NULL:WEBSITE"
        )

    if not clean_string(
        details.get(
            "Year of Foundation"
        )
    ):
        flags.append(
            "SOURCE_FIELD_NULL:FOUNDATION_YEAR"
        )

    if not clean_string(
        details.get(
            "Name of Principal/ Head of Institution"
        )
    ):
        flags.append(
            "SOURCE_FIELD_NULL:PRINCIPAL_NAME"
        )

    if not extract_cbse_school_code(
        listing
    ):
        flags.append(
            "SOURCE_FIELD_NULL:CBSE_SCHOOL_CODE"
        )

    if period_info[
        "parse_status"
    ] == "UNPARSED_DATE_FORMAT":
        flags.append(
            "AFFILIATION_DATE_PARSE_FAILURE"
        )

    if period_info[
        "parse_status"
    ] == "UNPARSED_SOURCE_TEXT":
        flags.append(
            "AFFILIATION_PERIOD_UNCLASSIFIED"
        )

    if period_info[
        "parse_status"
    ] == "PERMANENT_NO_DATE_RANGE":
        flags.append(
            "AFFILIATION_PERMANENT_CONDITIONAL"
        )

    return flags


# ============================================================
# COMPLETENESS
# ============================================================

def calculate_completeness(
    record
):
    identity = record[
        "identity_location"
    ]

    classification = record[
        "classification"
    ]

    board = record[
        "board_affiliation"
    ]

    principal = record[
        "principal"
    ]

    checks = [
        identity["school_name"],
        board["affiliation_number"],
        identity["state"],
        identity["district"],
        identity["address"],
        identity["pincode"],
        identity["website"],
        classification["estd_year"],
        classification[
            "first_opening_date"
        ],
        classification[
            "school_level"
        ],
        classification[
            "school_type"
        ],
        classification[
            "trust_society_name"
        ],
        principal["head_name"],
        principal["head_gender"],
        principal[
            "head_qualification"
        ],
        principal[
            "head_admin_experience_years"
        ],
        principal[
            "head_teaching_experience_years"
        ],
        board[
            "affiliation_period_raw"
        ],
    ]

    available = sum(
        1
        for value in checks
        if meaningful(value)
    )

    return round(
        available
        / len(checks)
        * 100,
        2,
    )


# ============================================================
# MAPPER
# ============================================================

def map_saras_record(
    raw,
    raw_file,
    dataset_name,
):
    metadata = safe_dict(
        raw.get("metadata")
    )

    listing = safe_dict(
        raw.get("listing_data")
    )

    details = safe_dict(
        raw.get("school_details")
    )

    affiliation_number = (
        clean_string(
            details.get(
                "Affiliation Number"
            )
        )
        or clean_string(
            listing.get(
                "affiliation_number"
            )
        )
        or clean_string(
            metadata.get(
                "affiliation_number"
            )
        )
    )

    if not affiliation_number:
        raise ValueError(
            "Missing affiliation number"
        )

    school_name = clean_string(
        details.get(
            "Name of Institution"
        )
    )

    state = clean_string(
        details.get("State")
    )

    # Important:
    # school_details.District is authoritative
    # for our SARAS mapper because metadata.district
    # is malformed in many Delhi files.
    district = clean_string(
        details.get("District")
    )

    address = clean_string(
        details.get(
            "Postal Address"
        )
    )

    pincode = clean_string(
        details.get("Pin Code")
    )

    website = normalize_website(
        details.get("Website")
    )

    foundation_year = clean_int(
        details.get(
            "Year of Foundation"
        )
    )

    opening_date_raw = clean_string(
        details.get(
            "Date of First Opening of School"
        )
    )

    opening_date = (
        parse_first_opening_date(
            opening_date_raw
        )
    )

    cbse_school_code = (
        extract_cbse_school_code(
            listing
        )
    )

    affiliation_period_raw = (
        details.get(
            "Affiliation Period"
        )
    )

    period_info = (
        parse_affiliation_period(
            affiliation_period_raw
        )
    )

    affiliation_type = clean_string(
        details.get(
            "Type of affiliation"
        )
    )

    remarks = clean_string(
        details.get(
            "Remarks, if any"
        )
    )

    record_id = make_record_id(
        affiliation_number
    )

    quality_flags = (
        build_quality_flags(
            details,
            listing,
            period_info,
        )
    )

    fallback_hash = (
        fallback_identity_hash(
            school_name,
            pincode,
            "CBSE",
        )
    )

    record = {
        # ====================================================
        # METADATA
        # ====================================================

        "metadata": {
            "record_id": record_id,

            # SARAS itself does not provide UDISE.
            "udise_code": None,

            "fallback_identity_hash":
                fallback_hash,

            "schema_version":
                SCHEMA_VERSION,

            "sources_merged": [
                {
                    "source":
                        SOURCE_NAME,

                    "source_code":
                        SOURCE_CODE,

                    "source_type":
                        SOURCE_TYPE,

                    "priority":
                        SOURCE_PRIORITY,

                    "legal_status":
                        SOURCE_LEGAL_STATUS,

                    "affiliation_number":
                        affiliation_number,

                    "detail_url":
                        clean_string(
                            metadata.get(
                                "detail_url"
                            )
                        ),

                    "raw_file":
                        str(raw_file),
                }
            ],

            "data_completeness_score":
                None,

            "last_verified_at": {
                "saras": None
            },

            "is_active": True,
            "deleted_at": None,
            "deleted_reason": None,

            "data_quality_flags":
                quality_flags,

            "search_index_synced_at":
                None,

            "needs_reindex": True,

            "claim_status":
                "unclaimed",

            "claimed_by_user_id":
                None,

            "claimed_at":
                None,
        },

        # ====================================================
        # IDENTITY + LOCATION
        # ====================================================

        "identity_location": {
            "school_name":
                school_name,

            "alternate_names": [],
            "short_name": None,
            "school_name_local": None,

            "state": state,
            "district": district,

            "block": None,
            "cluster": None,
            "village_ward": None,

            "address": address,

            "alternate_source_addresses":
                [],

            "pincode": pincode,

            "latitude": None,
            "longitude": None,

            # Current audited SARAS files do not
            # provide these directly.
            "phone": None,
            "email": None,

            "website": website,

            "lgd_local_body_id": None,
            "lgd_local_body_name": None,
            "lgd_ward_id": None,
            "lgd_ward_name": None,

            "assembly_constituency":
                None,

            "parliamentary_constituency":
                None,

            "nearest_railway": None,
            "nearest_police": None,
            "nearest_bank": None,
        },

        # ====================================================
        # CLASSIFICATION
        # ====================================================

        "classification": {
            "management_type_code":
                None,

            "management_type_desc":
                None,

            "management_broad_category":
                None,

            "school_category_code":
                None,

            "school_category_desc":
                None,

            "school_type":
                clean_string(
                    details.get(
                        "School Type"
                    )
                ),

            "class_from": None,
            "class_to": None,

            "school_level":
                clean_string(
                    details.get(
                        "Status of The School"
                    )
                ),

            "operational_status":
                None,

            "lifecycle_status":
                None,

            "merged_into_udise_code":
                None,

            "merged_year":
                None,

            "estd_year":
                foundation_year,

            "first_opening_date":
                opening_date,

            "first_opening_date_raw":
                opening_date_raw,

            "trust_society_name":
                clean_string(
                    details.get(
                        "Name of Trust/ Society/ Managing Committee"
                    )
                ),

            "curriculum_type":
                "CBSE",

            "ib_programme_types": [],

            "school_group_id": None,
            "brand_name": None,

            "is_independent_branch":
                None,

            "sibling_branches": [],
        },

        # ====================================================
        # BOARD / AFFILIATION
        # ====================================================

        "board_affiliation": {
            "board_secondary":
                "CBSE",

            "board_higher_secondary":
                "CBSE",

            "board_primary_display":
                "CBSE",

            "affiliation_number":
                affiliation_number,

            "cbse_school_code":
                cbse_school_code,

            "affiliation_type":
                affiliation_type,

            "affiliation_status":
                period_info[
                    "period_type"
                ],

            "affiliation_from":
                period_info["from"],

            "affiliation_to":
                period_info["to"],

            "affiliation_period_raw":
                period_info["raw"],

            "affiliation_period_parse_status":
                period_info[
                    "parse_status"
                ],

            "recognition_year_primary":
                None,

            "recognition_year_upper_primary":
                None,

            "recognition_year_secondary":
                None,

            "recognition_year_higher_secondary":
                None,

            "medium_of_instruction":
                [],
        },

        # ====================================================
        # PRINCIPAL
        # ====================================================

        "principal": {
            "head_name":
                clean_string(
                    details.get(
                        "Name of Principal/ Head of Institution"
                    )
                ),

            "head_gender":
                clean_string(
                    details.get(
                        "Gender"
                    )
                ),

            "head_qualification":
                clean_string(
                    details.get(
                        "Principal's Educational/Professional Qualifications:"
                    )
                ),

            # Source total is null in audited records.
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

            "head_bio": None,
            "head_photo_url": None,
        },

        # ====================================================
        # INCLUSIVE / RESIDENTIAL
        # ====================================================

        "inclusive_residential": {
            "minority_school_yn": None,
            "minority_type": None,

            "cwsn_school_yn": None,
            "special_educators_count":
                None,

            "therapies_offered": [],

            "individualized_education_plan_available":
                None,

            "cwsn_facilities_description":
                None,

            "shift_school_yn": None,
            "shift_timings": None,

            "residential_type": None,

            "hostel_available": None,
            "hostel_capacity_boys":
                None,

            "hostel_capacity_girls":
                None,

            "hostel_fee": None,
            "warden_student_ratio":
                None,

            "hostel_safety_measures_description":
                None,
        },

        # ====================================================
        # STUDENT / TEACHER
        # ====================================================

        "student_teacher_statistics": {
            "total_boys": None,
            "total_girls": None,
            "total_students": None,

            "students_with_furniture":
                None,

            "total_teachers": None,
            "male_teachers": None,
            "female_teachers": None,

            "regular_teachers": None,
            "contract_teachers": None,
            "part_time_teachers": None,

            "teachers_below_graduate":
                None,

            "teachers_graduate_above":
                None,

            "teachers_pg_above": None,
            "teachers_above_55": None,

            "teachers_service_trained":
                None,

            "teacher_category_breakdown":
                {},

            "teacher_qualification_breakdown":
                {},

            "faculty_seniority_breakdown":
                None,

            "student_teacher_ratio":
                None,

            "average_class_size":
                None,
        },

        # ====================================================
        # INFRASTRUCTURE
        # ====================================================

        "infrastructure": {
            "building_status": None,
            "building_blocks_total": None,
            "boundary_wall_type": None,

            "classrooms_total": None,
            "classrooms_good": None,

            "classrooms_minor_repair":
                None,

            "classrooms_major_repair":
                None,

            "other_rooms": None,
            "campus_area_sqm": None,

            "toilets_boys_total": None,

            "toilets_boys_functional":
                None,

            "toilets_girls_total": None,

            "toilets_girls_functional":
                None,

            "toilets_cwsn_boys_functional":
                None,

            "toilets_cwsn_girls_functional":
                None,

            "electricity_yn": None,
            "solar_panel_yn": None,
            "drinking_water_yn": None,
            "handwash_yn": None,

            "library_yn": None,
            "library_books_count": None,

            "playground_yn": None,
            "playground_description":
                None,

            "ramps_yn": None,
            "handrails_yn": None,

            "medical_checkup_yn": None,

            "labs": {
                "ict_lab_yn": None,
                "integrated_lab_yn":
                    None,
                "tinkering_lab_yn":
                    None,

                "composite_count": None,
                "physics_count": None,
                "chemistry_count": None,
                "biology_count": None,
                "math_count": None,
                "computer_count": None,
            },

            "internet_yn": None,

            "computers_desktop": None,
            "computers_laptop": None,
            "computers_tablet": None,

            "digital_boards_total":
                None,

            "digital_boards_functional":
                None,

            "projectors": None,
            "printers": None,
            "dth_access_yn": None,

            "infra_score": None,
            "is_accessible": None,
        },

        # ====================================================
        # FINANCIAL
        # ====================================================

        "financial_scholarships": {
            "annual_total_grant": None,

            "annual_total_expenditure":
                None,

            "fee_range": None,
            "fee_structure": [],

            "admission_fee": None,
            "security_deposit": None,

            "payment_modes": [],

            "scholarships_available":
                [],

            "fee_concession_policy_description":
                None,
        },

        # ====================================================
        # MEDIA
        # ====================================================

        "media_gallery": {
            "gallery": [],
            "photos": [],

            "brochure_pdf_url": None,

            "promotional_video_url":
                None,

            "virtual_tour_url": None,

            "annual_day_highlights_url":
                None,

            "logo_url": None,

            "facilities": [],

            "campus_size_display": None,
            "school_format": None,
        },

        # ====================================================
        # ACADEMIC OUTCOMES
        # ====================================================

        "academic_outcomes": {
            "board_result_year": None,

            "pass_percentage_x": None,
            "pass_percentage_xii": None,

            "average_score_x": None,
            "average_score_xii": None,

            "toppers": [],
            "result_trend": [],

            "streams_offered_senior_secondary":
                [],

            "subject_combinations": [],

            "board_average_comparison":
                None,
        },

        # ====================================================
        # SAFETY
        # ====================================================

        "safety_verification": {
            "cctv_coverage": None,

            "transport_gps_tracked":
                None,

            "transport_attendant_onboard":
                None,

            "staff_background_verified":
                None,

            "fire_safety_noc_status":
                None,

            "fire_safety_noc_valid_until":
                None,

            "child_protection_policy_on_file":
                None,

            "pocso_staff_training_status":
                None,

            "safety_verified_badge":
                False,
        },

        # ====================================================
        # ADMISSIONS
        # ====================================================

        "admissions": {
            "admission_process_description":
                None,

            "admission_eligibility_by_grade":
                [],

            "entrance_test_required":
                None,

            "entrance_test_subjects": [],

            "application_deadline": None,

            "seat_vacancy_by_grade": [],

            "admission_quota_policy":
                None,

            "interview_process": None,

            "required_documents": [],

            "admission_open_dates": None,

            "academic_calendar_url":
                None,
        },

        # ====================================================
        # PROFILE ENRICHMENT
        # ====================================================

        "school_profile_enrichment": {
            "grievance_committee_contact":
                None,

            "grievance_process_description":
                None,

            "ptm_frequency": None,

            "daily_school_timing": None,

            "canteen_available": None,
            "canteen_type": None,
            "canteen_menu_url": None,

            "uniform_vendor_info": None,

            "teaching_methodology_description":
                None,

            "homework_assessment_policy":
                None,

            "career_counseling_available":
                None,

            "psychological_counseling_available":
                None,

            "technology_integration_description":
                None,

            "achievements": [],
            "notable_alumni": [],

            "extracurricular_activities":
                [],

            "sports_offered": [],
            "transport_routes": [],

            "social_media_links": {},
            "contact_persons": [],

            "profile_description_local":
                [],
        },

        # ====================================================
        # HISTORY / SEO
        # ====================================================

        "history_seo_tags": {
            "school_history": [],

            "operational_since_year":
                foundation_year,

            "status_stable": None,

            "slug": None,
            "seo_title": None,
            "seo_description": None,

            "search_tags": [],
        },

        # ====================================================
        # SARAS-SPECIFIC SOURCE INFORMATION
        # ====================================================

        "source_specific": {
            "saras": {
                "serial_number":
                    clean_string(
                        listing.get(
                            "serial_number"
                        )
                    ),

                "cbse_school_code":
                    cbse_school_code,

                "listing_status":
                    clean_string(
                        listing.get(
                            "status"
                        )
                    ),

                "selected_district":
                    clean_string(
                        metadata.get(
                            "selected_district"
                        )
                    ),

                "remarks":
                    remarks,

                "detail_url":
                    clean_string(
                        metadata.get(
                            "detail_url"
                        )
                    ),
            }
        },

        # ====================================================
        # PROVENANCE
        # ====================================================

        "provenance": {
            "primary_source":
                SOURCE_NAME,

            "source_priority":
                SOURCE_PRIORITY,

            "source_legal_status":
                SOURCE_LEGAL_STATUS,

            "raw_file":
                str(raw_file),

            "source_field_selection": {
                "school_name":
                    "school_details.Name of Institution",

                "affiliation_number":
                    "school_details.Affiliation Number",

                "state":
                    "school_details.State",

                "district":
                    "school_details.District",

                "address":
                    "school_details.Postal Address",

                "pincode":
                    "school_details.Pin Code",

                "website":
                    "school_details.Website",

                "foundation_year":
                    "school_details.Year of Foundation",

                "principal":
                    "school_details",

                "affiliation_period":
                    "school_details.Affiliation Period",
            },

            "field_conflicts": [],
        },

        # ====================================================
        # ENTITY RESOLUTION
        # ====================================================

        "entity_resolution": {
            "status":
                "SARAS_BASE_RECORD",

            "matched_sources": [],

            "candidate_matches": [],

            "manual_review_required":
                False,
        },
    }

    record["metadata"][
        "data_completeness_score"
    ] = calculate_completeness(
        record
    )

    return record


# ============================================================
# PROCESS DATASET
# ============================================================

def get_raw_files(directory):
    return sorted(
        directory.glob("*.json")
    )


def process_dataset(
    dataset_name,
    raw_dir,
):
    print()
    print("=" * 74)
    print(
        f"PROCESSING SARAS: "
        f"{dataset_name.upper()}"
    )
    print("=" * 74)

    raw_files = get_raw_files(
        raw_dir
    )

    output_dir = (
        OUTPUT_ROOT
        / dataset_name
    )

    school_dir = (
        output_dir
        / "schools"
    )

    all_records = []
    errors = []

    affiliation_numbers = set()
    record_ids = set()

    success = 0

    for raw_file in raw_files:

        try:
            raw = load_json(
                raw_file
            )

            record = map_saras_record(
                raw,
                raw_file,
                dataset_name,
            )

            affiliation_number = (
                record[
                    "board_affiliation"
                ][
                    "affiliation_number"
                ]
            )

            record_id = (
                record[
                    "metadata"
                ][
                    "record_id"
                ]
            )

            if (
                affiliation_number
                in affiliation_numbers
            ):
                raise ValueError(
                    "Duplicate affiliation "
                    f"number: "
                    f"{affiliation_number}"
                )

            if record_id in record_ids:
                raise ValueError(
                    "Duplicate record_id: "
                    f"{record_id}"
                )

            affiliation_numbers.add(
                affiliation_number
            )

            record_ids.add(
                record_id
            )

            output_file = (
                school_dir
                / f"{affiliation_number}.json"
            )

            save_json(
                output_file,
                record,
            )

            all_records.append(
                record
            )

            success += 1

        except Exception as exc:

            errors.append({
                "file":
                    str(raw_file),

                "error":
                    str(exc),
            })

            print(
                f"[ERROR] "
                f"{raw_file.name}: "
                f"{exc}"
            )

    save_json(
        output_dir
        / "_all_schools.json",
        all_records,
    )

    summary = {
        "dataset":
            dataset_name,

        "schema_version":
            SCHEMA_VERSION,

        "source":
            SOURCE_NAME,

        "raw_files":
            len(raw_files),

        "success":
            success,

        "failed":
            len(errors),

        "unique_affiliation_numbers":
            len(
                affiliation_numbers
            ),

        "unique_record_ids":
            len(record_ids),

        "generated_at":
            utc_now(),

        "output_directory":
            str(output_dir),
    }

    save_json(
        output_dir
        / "_summary.json",
        summary,
    )

    if errors:
        save_json(
            output_dir
            / "_errors.json",
            errors,
        )

    print(
        f"Raw files                   : "
        f"{len(raw_files)}"
    )

    print(
        f"Success                     : "
        f"{success}"
    )

    print(
        f"Failed                      : "
        f"{len(errors)}"
    )

    print(
        f"Unique affiliation numbers  : "
        f"{len(affiliation_numbers)}"
    )

    print(
        f"Unique record IDs           : "
        f"{len(record_ids)}"
    )

    return summary


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 74)
    print(
        "SCHOOLFINDER SARAS "
        "CANONICAL V4 MAPPER"
    )
    print("=" * 74)

    print(
        "Raw SARAS files will NOT "
        "be modified."
    )

    print(
        "UDISE Canonical v4 records "
        "will NOT be modified."
    )

    print(
        f"New SARAS output: "
        f"{OUTPUT_ROOT}"
    )

    overall = {
        "schema_version":
            SCHEMA_VERSION,

        "source":
            SOURCE_NAME,

        "datasets": {},

        "generated_at":
            utc_now(),
    }

    total_raw = 0
    total_success = 0
    total_failed = 0

    for (
        dataset_name,
        raw_dir,
    ) in DATASETS.items():

        if not raw_dir.exists():
            print(
                "[WARNING] "
                f"Directory missing: "
                f"{raw_dir}"
            )

            continue

        summary = process_dataset(
            dataset_name,
            raw_dir,
        )

        overall[
            "datasets"
        ][dataset_name] = summary

        total_raw += (
            summary["raw_files"]
        )

        total_success += (
            summary["success"]
        )

        total_failed += (
            summary["failed"]
        )

    overall["totals"] = {
        "raw_files":
            total_raw,

        "success":
            total_success,

        "failed":
            total_failed,
    }

    save_json(
        OUTPUT_ROOT
        / "_summary.json",
        overall,
    )

    print()
    print("=" * 74)
    print(
        "SARAS CANONICAL V4 "
        "GENERATION COMPLETED"
    )
    print("=" * 74)

    print(
        f"Total raw files : "
        f"{total_raw}"
    )

    print(
        f"Total generated : "
        f"{total_success}"
    )

    print(
        f"Total failed    : "
        f"{total_failed}"
    )

    print()
    print(
        f"Output: "
        f"{OUTPUT_ROOT}"
    )

    print()
    print(
        "IMPORTANT: Validate SARAS "
        "Canonical v4 before entity "
        "resolution."
    )


if __name__ == "__main__":
    main()