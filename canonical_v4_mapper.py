import json
import hashlib
import uuid
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter


# ============================================================
# CONFIGURATION
# ============================================================

SCHEMA_VERSION = "4.0"
ACADEMIC_YEAR = "2025-26"

DATASETS = {
    "chandigarh": Path("data/Chandigarh/2025-26"),
    "delhi": Path("data/Delhi/2025-26"),
    "mumbai": Path("data/Mumbai/2025-26"),
}

OUTPUT_ROOT = Path("data/canonical_v4")

SOURCE_NAME = "UDISE+"
SOURCE_CODE = "UDISE"
SOURCE_PRIORITY = 1
SOURCE_LEGAL_STATUS = "cleared"
SOURCE_LICENSE = "GODL-India"


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).isoformat()


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
            ensure_ascii=False
        )


def clean_string(value):
    if value is None:
        return None

    if not isinstance(value, str):
        return value

    value = " ".join(value.strip().split())

    if value == "":
        return None

    return value


def clean_code(value):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    return value


def clean_int(value):
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def clean_float(value):
    if value is None or value == "":
        return None

    try:
        return float(value)
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


def safe_dict(value):
    return value if isinstance(value, dict) else {}


def first_meaningful(*values):
    for value in values:
        value = clean_string(value)

        if meaningful(value):
            return value

    return None


# ============================================================
# IDENTITY
# ============================================================

def make_record_id(udise_code):
    """
    Deterministic UUID for repeatable ETL output.

    Same UDISE code will generate the same canonical record_id
    every time the mapper is rerun.
    """
    if udise_code:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"schoolfinder:udise:{udise_code}"
            )
        )

    return str(uuid.uuid4())


def fallback_identity_hash(name, pincode, board):
    text = "|".join([
        (name or "").strip().casefold(),
        str(pincode or "").strip(),
        (board or "").strip().casefold(),
    ])

    if not text.replace("|", ""):
        return None

    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


# ============================================================
# SOURCE VALUE HELPERS
# ============================================================

def normalize_email(value):
    value = clean_string(value)

    if not value:
        return None

    # UDISE sometimes publishes:
    # name[at]domain[dot]com
    value = value.replace("[at]", "@")
    value = value.replace("[dot]", ".")

    return value


def normalize_website(value):
    return clean_string(value)


def normalize_phone(value):
    return clean_string(value)


def valid_medium(value):
    value = clean_string(value)

    if not value:
        return None

    if value.upper() in {"NA", "N/A", "0"}:
        return None

    # Example: 19-English -> English
    if "-" in value:
        prefix, remainder = value.split("-", 1)

        if prefix.strip().isdigit():
            value = remainder.strip()

    return value or None


def collect_mediums(profile):
    result = []

    for i in range(1, 5):
        value = valid_medium(
            profile.get(f"mediumOfInstrName{i}")
        )

        if value and value not in result:
            result.append(value)

    return result


# ============================================================
# ADDRESS CONFLICT HANDLING
# ============================================================

def build_address(summary, profile):
    summary_address = clean_string(
        summary.get("address")
    )

    profile_address = clean_string(
        profile.get("address")
    )

    # Canonical rule:
    # school_summary.address is primary for UD-RAW.
    primary = summary_address or profile_address

    alternatives = []

    if (
        summary_address
        and profile_address
        and summary_address.casefold()
        != profile_address.casefold()
    ):
        alternatives.append({
            "value": profile_address,
            "source": "UDISE+.school_profile.address",
        })

    conflict = bool(alternatives)

    return primary, alternatives, conflict


# ============================================================
# COMPUTED VALUES
# ============================================================

def student_teacher_ratio(total_students, total_teachers):
    if (
        total_students is None
        or total_teachers is None
        or total_teachers <= 0
    ):
        return None

    return round(
        total_students / total_teachers,
        2
    )


def yes_no_boolean(value):
    """
    UDISE frequently uses:
    1 = Yes
    2 = No

    Other codes such as 3/9 are not guessed.
    """
    if value == 1:
        return True

    if value == 2:
        return False

    return None


def accessibility_value(ramps, handrails):
    ramp_bool = yes_no_boolean(ramps)
    handrail_bool = yes_no_boolean(handrails)

    if ramp_bool is None or handrail_bool is None:
        return None

    return ramp_bool and handrail_bool


def derive_school_level(class_from, class_to):
    if class_from is None or class_to is None:
        return None

    if class_to <= 5:
        return "Primary"

    if class_to <= 8:
        return "Upper Primary"

    if class_to <= 10:
        return "Secondary"

    if class_to <= 12:
        return "Higher Secondary"

    return None


# ============================================================
# DATA QUALITY
# ============================================================

def build_quality_flags(
    summary,
    profile,
    student_stats,
    infrastructure,
    report_card,
    address_conflict,
):
    flags = []

    if not profile:
        flags.append("SOURCE_SECTION_NULL:SCHOOL_PROFILE")

    if not student_stats:
        flags.append(
            "SOURCE_SECTION_NULL:STUDENT_TEACHER_STATISTICS"
        )

    if not infrastructure:
        flags.append(
            "SOURCE_SECTION_NULL:INFRASTRUCTURE_FACILITIES"
        )

    if not report_card:
        flags.append(
            "SOURCE_SECTION_NULL:REPORT_CARD"
        )

    if address_conflict:
        flags.append(
            "SOURCE_FIELD_CONFLICT:ADDRESS"
        )

    return flags


# ============================================================
# COMPLETENESS
# ============================================================

def calculate_completeness(record):
    """
    Lightweight profile completeness score.

    This is not source accuracy/trust scoring.
    It only measures presence of selected useful public fields.
    """

    checks = [
        record["identity_location"]["school_name"],
        record["metadata"]["udise_code"],
        record["identity_location"]["state"],
        record["identity_location"]["district"],
        record["identity_location"]["address"],
        record["identity_location"]["pincode"],
        record["identity_location"]["phone"],
        record["identity_location"]["email"],
        record["classification"]["management_type_desc"],
        record["classification"]["school_category_desc"],
        record["classification"]["school_type"],
        record["classification"]["estd_year"],
        record["principal"]["head_name"],
        record["student_teacher_statistics"]["total_students"],
        record["student_teacher_statistics"]["total_teachers"],
        record["infrastructure"]["classrooms_total"],
        record["infrastructure"]["library_yn"],
        record["infrastructure"]["playground_yn"],
        record["infrastructure"]["internet_yn"],
    ]

    available = sum(
        1 for value in checks
        if meaningful(value)
    )

    return round(
        available / len(checks) * 100,
        2
    )


# ============================================================
# CANONICAL RECORD BUILDER
# ============================================================

def map_udise_record(raw, raw_file, dataset_name):
    summary = safe_dict(
        raw.get("school_summary")
    )

    profile = safe_dict(
        raw.get("school_profile")
    )

    student_stats = safe_dict(
        raw.get("student_teacher_statistics")
    )

    infrastructure = safe_dict(
        raw.get("infrastructure_facilities")
    )

    report_card = safe_dict(
        raw.get("report_card")
    )

    school_history = raw.get("school_history")

    if not isinstance(school_history, list):
        school_history = []

    udise_code = clean_code(
        summary.get("udiseschCode")
        or report_card.get("udiseschCode")
    )

    school_name = clean_string(
        summary.get("schoolName")
        or report_card.get("schoolName")
    )

    pincode = clean_int(
        summary.get("pincode")
        or report_card.get("pincode")
    )

    board_secondary = clean_string(
        profile.get("boardSecName")
    )

    board_higher = clean_string(
        profile.get("boardHighSecName")
    )

    if board_secondary in {"NA", "N/A"}:
        board_secondary = None

    if board_higher in {"NA", "N/A"}:
        board_higher = None

    board_for_hash = (
        board_secondary
        or board_higher
        or ""
    )

    record_id = make_record_id(
        udise_code
    )

    identity_hash = fallback_identity_hash(
        school_name,
        pincode,
        board_for_hash,
    )

    address, alternate_addresses, address_conflict = (
        build_address(
            summary,
            profile,
        )
    )

    # Contact preference:
    # profile first because it generally contains cleaner values,
    # summary is fallback.
    email = normalize_email(
        first_meaningful(
            profile.get("email"),
            summary.get("email"),
        )
    )

    phone = normalize_phone(
        profile.get("schPhone")
    )

    website = normalize_website(
        profile.get("website")
    )

    class_from = clean_int(
        summary.get("classFrm")
    )

    class_to = clean_int(
        summary.get("classTo")
    )

    total_boys = clean_int(
        student_stats.get("totalBoy")
    )

    total_girls = clean_int(
        student_stats.get("totalGirl")
    )

    total_students = clean_int(
        student_stats.get("totalCount")
    )

    total_teachers = clean_int(
        report_card.get("totalTeacher")
    )

    male_teachers = clean_int(
        student_stats.get("totalTeacherMale")
    )

    female_teachers = clean_int(
        student_stats.get("totalTeacherFemale")
    )

    regular_teachers = clean_int(
        report_card.get("tchReg")
    )

    contract_teachers = clean_int(
        report_card.get("tchCont")
    )

    part_time_teachers = clean_int(
        report_card.get("tchPart")
    )

    ramps_raw = clean_int(
        infrastructure.get("rampsYn")
    )

    handrails_raw = clean_int(
        infrastructure.get("handrailsYn")
    )

    quality_flags = build_quality_flags(
        summary,
        profile,
        student_stats,
        infrastructure,
        report_card,
        address_conflict,
    )

    record = {
        "metadata": {
            "record_id": record_id,
            "udise_code": udise_code,
            "fallback_identity_hash": identity_hash,
            "schema_version": SCHEMA_VERSION,

            "sources_merged": [
                {
                    "source": SOURCE_NAME,
                    "source_code": SOURCE_CODE,
                    "priority": SOURCE_PRIORITY,
                    "legal_status": SOURCE_LEGAL_STATUS,
                    "license": SOURCE_LICENSE,
                    "academic_year": ACADEMIC_YEAR,
                    "raw_file": str(raw_file),
                }
            ],

            "data_completeness_score": None,

            "last_verified_at": {
                "udise": utc_now()
            },

            "is_active": True,
            "deleted_at": None,
            "deleted_reason": None,

            "data_quality_flags": quality_flags,

            "search_index_synced_at": None,
            "needs_reindex": True,

            "claim_status": "unclaimed",
            "claimed_by_user_id": None,
            "claimed_at": None,
        },

        # ----------------------------------------------------
        # B.2 Identity & Location
        # ----------------------------------------------------

        "identity_location": {
            "school_name": school_name,
            "alternate_names": [],
            "short_name": None,
            "school_name_local": None,

            "state": clean_string(
                summary.get("stateName")
            ),

            "district": clean_string(
                summary.get("districtName")
            ),

            "block": clean_string(
                summary.get("blockName")
            ),

            "cluster": clean_string(
                summary.get("clusterName")
            ),

            "village_ward": clean_string(
                summary.get("villageName")
            ),

            "address": address,

            "alternate_source_addresses": (
                alternate_addresses
            ),

            "pincode": pincode,

            # UD-RAW does not contain coordinates.
            "latitude": None,
            "longitude": None,

            "phone": phone,
            "email": email,
            "website": website,

            "lgd_local_body_id": clean_code(
                summary.get(
                    "lgdurbanlocalbodyId"
                )
            ),

            "lgd_local_body_name": clean_string(
                summary.get(
                    "lgdurbanlocalbodyName"
                )
            ),

            "lgd_ward_id": clean_code(
                summary.get("lgdwardId")
            ),

            "lgd_ward_name": clean_string(
                summary.get("lgdwardName")
            ),

            "assembly_constituency": clean_string(
                report_card.get(
                    "assemblyCdDesc"
                )
            ),

            "parliamentary_constituency": clean_string(
                report_card.get(
                    "parlCdDesc"
                )
            ),

            "nearest_railway": None,
            "nearest_police": None,
            "nearest_bank": None,
        },

        # ----------------------------------------------------
        # B.3 Classification
        # ----------------------------------------------------

        "classification": {
            "management_type_code": clean_int(
                summary.get("schMgmtId")
            ),

            "management_type_desc": clean_string(
                summary.get("schMgmtDesc")
            ),

            "management_broad_category": clean_int(
                summary.get("schBroadMgmtId")
            ),

            "school_category_code": clean_int(
                summary.get("schCategoryId")
            ),

            "school_category_desc": clean_string(
                summary.get("schCatDesc")
            ),

            "school_type": clean_string(
                summary.get("schTypeDesc")
            ),

            "class_from": class_from,
            "class_to": class_to,

            "school_level": derive_school_level(
                class_from,
                class_to,
            ),

            "operational_status": clean_string(
                summary.get("schoolStatusName")
            ),

            "lifecycle_status": clean_string(
                summary.get("schoolStatusName")
            ),

            "merged_into_udise_code": clean_code(
                summary.get("schIdMerged")
            ),

            "merged_year": clean_string(
                summary.get("schMergedYear")
            ),

            "estd_year": clean_int(
                profile.get("estdYear")
            ),

            "trust_society_name": None,
            "curriculum_type": None,
            "ib_programme_types": [],

            "school_group_id": None,
            "brand_name": None,
            "is_independent_branch": None,
            "sibling_branches": [],
        },

        # ----------------------------------------------------
        # B.4 Board & Affiliation
        # ----------------------------------------------------

        "board_affiliation": {
            "board_secondary": board_secondary,
            "board_higher_secondary": board_higher,
            "board_primary_display": (
                board_secondary
                or board_higher
            ),

            "affiliation_number": None,
            "affiliation_type": None,
            "affiliation_status": None,
            "affiliation_from": None,
            "affiliation_to": None,

            "recognition_year_primary": clean_int(
                profile.get("recogYearPri")
            ),

            "recognition_year_upper_primary": clean_int(
                profile.get("recogYearUpr")
            ),

            "recognition_year_secondary": clean_int(
                profile.get("recogYearSec")
            ),

            "recognition_year_higher_secondary": clean_int(
                profile.get("recogYearHsec")
            ),

            "medium_of_instruction": collect_mediums(
                profile
            ),
        },

        # ----------------------------------------------------
        # B.5 Principal
        # ----------------------------------------------------

        "principal": {
            "head_name": clean_string(
                profile.get("headMasterName")
            ),

            "head_gender": None,
            "head_qualification": None,

            "head_admin_experience_years": None,
            "head_teaching_experience_years": None,

            "head_bio": None,
            "head_photo_url": None,
        },

        # ----------------------------------------------------
        # B.6 Inclusive / Residential
        # ----------------------------------------------------

        "inclusive_residential": {
            "minority_school_yn": yes_no_boolean(
                clean_int(
                    profile.get("minorityYn")
                )
            ),

            "minority_type": None,

            "cwsn_school_yn": yes_no_boolean(
                clean_int(
                    profile.get("cwsnSchYn")
                )
            ),

            "special_educators_count": None,
            "therapies_offered": [],

            "individualized_education_plan_available": None,
            "cwsn_facilities_description": None,

            "shift_school_yn": yes_no_boolean(
                clean_int(
                    profile.get("shiftSchYn")
                )
            ),

            "shift_timings": None,

            "residential_type": clean_string(
                profile.get("resiSchDesc")
            ),

            "hostel_available": None,
            "hostel_capacity_boys": None,
            "hostel_capacity_girls": None,
            "hostel_fee": None,
            "warden_student_ratio": None,
            "hostel_safety_measures_description": None,
        },

        # ----------------------------------------------------
        # B.7 Student / Teacher
        # ----------------------------------------------------

        "student_teacher_statistics": {
            "total_boys": total_boys,
            "total_girls": total_girls,
            "total_students": total_students,

            "students_with_furniture": clean_int(
                student_stats.get(
                    "studentHaveFurniture"
                )
            ),

            "total_teachers": total_teachers,
            "male_teachers": male_teachers,
            "female_teachers": female_teachers,

            "regular_teachers": regular_teachers,
            "contract_teachers": contract_teachers,
            "part_time_teachers": part_time_teachers,

            "teachers_below_graduate": clean_int(
                report_card.get(
                    "totTchBelowGraduate"
                )
            ),

            "teachers_graduate_above": clean_int(
                report_card.get(
                    "totTchGraduateAbove"
                )
            ),

            "teachers_pg_above": clean_int(
                report_card.get(
                    "totTchPgraduateAbove"
                )
            ),

            "teachers_above_55": clean_int(
                report_card.get("tchAbove55")
            ),

            "teachers_service_trained": clean_int(
                report_card.get(
                    "tchRecvdServiceTrng"
                )
            ),

            "teacher_category_breakdown": {
                f"tchCat{i}": clean_int(
                    report_card.get(f"tchCat{i}")
                )
                for i in [
                    1, 2, 3, 4, 5, 6,
                    7, 8, 10, 11
                ]
            },

            "teacher_qualification_breakdown": {
                f"profQual{i}": clean_int(
                    report_card.get(
                        f"profQual{i}"
                    )
                )
                for i in [
                    1, 2, 3, 4, 5, 6,
                    7, 8, 10, 11, 12
                ]
            },

            "faculty_seniority_breakdown": None,

            "student_teacher_ratio": (
                student_teacher_ratio(
                    total_students,
                    total_teachers,
                )
            ),

            "average_class_size": None,
        },

        # ----------------------------------------------------
        # B.8 Infrastructure
        # ----------------------------------------------------

        "infrastructure": {
            "building_status": clean_string(
                infrastructure.get(
                    "bldStatusDesc"
                )
            ),

            "building_blocks_total": clean_int(
                infrastructure.get(
                    "bldBlkTot"
                )
            ),

            "boundary_wall_type": clean_string(
                infrastructure.get(
                    "boundaryWallDesc"
                )
            ),

            "classrooms_total": clean_int(
                infrastructure.get(
                    "clsrmsInst"
                )
            ),

            "classrooms_good": clean_int(
                infrastructure.get(
                    "clsrmsGd"
                )
            ),

            "classrooms_minor_repair": clean_int(
                infrastructure.get(
                    "clsrmsMin"
                )
            ),

            "classrooms_major_repair": clean_int(
                infrastructure.get(
                    "clsrmsMaj"
                )
            ),

            "other_rooms": clean_int(
                infrastructure.get(
                    "othrRooms"
                )
            ),

            "campus_area_sqm": None,

            "toilets_boys_total": clean_int(
                infrastructure.get("toiletb")
            ),

            "toilets_boys_functional": clean_int(
                infrastructure.get(
                    "toiletbFun"
                )
            ),

            "toilets_girls_total": clean_int(
                infrastructure.get("toiletg")
            ),

            "toilets_girls_functional": clean_int(
                infrastructure.get(
                    "toiletgFun"
                )
            ),

            "toilets_cwsn_boys_functional": clean_int(
                infrastructure.get(
                    "toiletbCwsnFun"
                )
            ),

            "toilets_cwsn_girls_functional": clean_int(
                infrastructure.get(
                    "toiletgCwsnFun"
                )
            ),

            "electricity_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "electricityYn"
                    )
                )
            ),

            "solar_panel_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "solarPanelYn"
                    )
                )
            ),

            "drinking_water_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "drinkWaterYn"
                    )
                )
            ),

            "handwash_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "handWashYn"
                    )
                )
            ),

            "library_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "libraryYn"
                    )
                )
            ),

            "library_books_count": clean_int(
                infrastructure.get(
                    "libraryBooks"
                )
            ),

            "playground_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "playgroundYn"
                    )
                )
            ),

            "playground_description": None,

            "ramps_yn": yes_no_boolean(
                ramps_raw
            ),

            "handrails_yn": yes_no_boolean(
                handrails_raw
            ),

            "medical_checkup_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "medicalCheckupYn"
                    )
                )
            ),

            "labs": {
                "ict_lab_yn": yes_no_boolean(
                    clean_int(
                        infrastructure.get(
                            "ictLabYn"
                        )
                    )
                ),

                "integrated_lab_yn": yes_no_boolean(
                    clean_int(
                        infrastructure.get(
                            "integratedLabYn"
                        )
                    )
                ),

                "tinkering_lab_yn": yes_no_boolean(
                    clean_int(
                        infrastructure.get(
                            "tinkeringLabYn"
                        )
                    )
                ),

                "composite_count": None,
                "physics_count": None,
                "chemistry_count": None,
                "biology_count": None,
                "math_count": None,
                "computer_count": None,
            },

            "internet_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "internetYn"
                    )
                )
            ),

            "computers_desktop": clean_int(
                infrastructure.get(
                    "desktopFun"
                )
            ),

            "computers_laptop": clean_int(
                infrastructure.get(
                    "laptopTot"
                )
            ),

            "computers_tablet": clean_int(
                infrastructure.get(
                    "tabletsTot"
                )
            ),

            "digital_boards_total": clean_int(
                infrastructure.get(
                    "digiBoardTot"
                )
            ),

            "digital_boards_functional": clean_int(
                infrastructure.get(
                    "digiBoardFun"
                )
            ),

            "projectors": clean_int(
                infrastructure.get(
                    "projectorTot"
                )
            ),

            "printers": clean_int(
                infrastructure.get(
                    "printerTot"
                )
            ),

            "dth_access_yn": yes_no_boolean(
                clean_int(
                    infrastructure.get(
                        "dthYn"
                    )
                )
            ),

            # We will implement a documented scoring formula
            # after source-field validation.
            "infra_score": None,

            "is_accessible": accessibility_value(
                ramps_raw,
                handrails_raw,
            ),
        },

        # ----------------------------------------------------
        # B.9 Financial
        # ----------------------------------------------------

        "financial_scholarships": {
            "annual_total_grant": clean_float(
                report_card.get(
                    "totalGrant"
                )
            ),

            "annual_total_expenditure": clean_float(
                report_card.get(
                    "totalExpediture"
                )
            ),

            "fee_range": None,
            "fee_structure": [],
            "admission_fee": None,
            "security_deposit": None,
            "payment_modes": [],
            "scholarships_available": [],
            "fee_concession_policy_description": None,
        },

        # ----------------------------------------------------
        # B.10 Media
        # ----------------------------------------------------

        "media_gallery": {
            "gallery": [],
            "photos": [],
            "brochure_pdf_url": None,
            "promotional_video_url": None,
            "virtual_tour_url": None,
            "annual_day_highlights_url": None,
            "logo_url": None,
            "facilities": [],
            "campus_size_display": None,
            "school_format": None,
        },

        # ----------------------------------------------------
        # B.11 Academic Outcomes
        # ----------------------------------------------------

        "academic_outcomes": {
            "board_result_year": None,
            "pass_percentage_x": None,
            "pass_percentage_xii": None,
            "average_score_x": None,
            "average_score_xii": None,
            "toppers": [],
            "result_trend": [],
            "streams_offered_senior_secondary": [],
            "subject_combinations": [],
            "board_average_comparison": None,
        },

        # ----------------------------------------------------
        # B.12 Safety
        # ----------------------------------------------------

        "safety_verification": {
            "cctv_coverage": None,
            "transport_gps_tracked": None,
            "transport_attendant_onboard": None,
            "staff_background_verified": None,
            "fire_safety_noc_status": None,
            "fire_safety_noc_valid_until": None,
            "child_protection_policy_on_file": None,
            "pocso_staff_training_status": None,
            "safety_verified_badge": False,
        },

        # ----------------------------------------------------
        # B.13 Admissions
        # ----------------------------------------------------

        "admissions": {
            "admission_process_description": None,
            "admission_eligibility_by_grade": [],
            "entrance_test_required": None,
            "entrance_test_subjects": [],
            "application_deadline": None,
            "seat_vacancy_by_grade": [],
            "admission_quota_policy": None,
            "interview_process": None,
            "required_documents": [],
            "admission_open_dates": None,
            "academic_calendar_url": None,
        },

        # ----------------------------------------------------
        # B.14 Grievance / Logistics / Pedagogy
        # ----------------------------------------------------

        "school_profile_enrichment": {
            "grievance_committee_contact": None,
            "grievance_process_description": None,
            "ptm_frequency": None,
            "daily_school_timing": None,
            "canteen_available": None,
            "canteen_type": None,
            "canteen_menu_url": None,
            "uniform_vendor_info": None,
            "teaching_methodology_description": None,
            "homework_assessment_policy": None,
            "career_counseling_available": None,
            "psychological_counseling_available": None,
            "technology_integration_description": None,
            "achievements": [],
            "notable_alumni": [],
            "extracurricular_activities": [],
            "sports_offered": [],
            "transport_routes": [],
            "social_media_links": {},
            "contact_persons": [],
            "profile_description_local": [],
        },

        # ----------------------------------------------------
        # B.15 History
        # ----------------------------------------------------

        "history_seo_tags": {
            "school_history": school_history,
            "operational_since_year": None,
            "status_stable": None,
            "slug": None,
            "seo_title": None,
            "seo_description": None,
            "search_tags": [],
        },

        # ----------------------------------------------------
        # Provenance / Resolution
        # ----------------------------------------------------

        "provenance": {
            "primary_source": SOURCE_NAME,

            "source_priority": SOURCE_PRIORITY,

            "source_legal_status": (
                SOURCE_LEGAL_STATUS
            ),

            "raw_file": str(raw_file),

            "academic_year": ACADEMIC_YEAR,

            "address": {
                "selected_from": (
                    "school_summary.address"
                    if clean_string(
                        summary.get("address")
                    )
                    else "school_profile.address"
                ),

                "conflict_detected": (
                    address_conflict
                ),
            },

            "field_conflicts": (
                ["address"]
                if address_conflict
                else []
            ),
        },

        "entity_resolution": {
            "status": "UDISE_BASE_RECORD",
            "matched_sources": [],
            "candidate_matches": [],
            "manual_review_required": False,
        },
    }

    record["metadata"][
        "data_completeness_score"
    ] = calculate_completeness(record)

    return record


# ============================================================
# YEARLY SNAPSHOT
# ============================================================

def build_yearly_snapshot(record):
    stats = record[
        "student_teacher_statistics"
    ]

    infra = record["infrastructure"]

    outcomes = record[
        "academic_outcomes"
    ]

    return {
        "record_id": record[
            "metadata"
        ]["record_id"],

        "udise_code": record[
            "metadata"
        ]["udise_code"],

        "academic_year": ACADEMIC_YEAR,

        "schema_version": SCHEMA_VERSION,

        "total_students": stats[
            "total_students"
        ],

        "total_teachers": stats[
            "total_teachers"
        ],

        "student_teacher_ratio": stats[
            "student_teacher_ratio"
        ],

        "infra_score": infra[
            "infra_score"
        ],

        "pass_percentage_x": outcomes[
            "pass_percentage_x"
        ],

        "pass_percentage_xii": outcomes[
            "pass_percentage_xii"
        ],

        "source": SOURCE_NAME,
    }


# ============================================================
# DATASET PROCESSING
# ============================================================

def get_raw_files(raw_dir):
    return sorted(
        path
        for path in raw_dir.glob("*.json")
        if not path.name.startswith("_")
    )


def process_dataset(dataset_name, raw_dir):
    print()
    print("=" * 72)
    print(
        f"PROCESSING: {dataset_name.upper()}"
    )
    print("=" * 72)

    raw_files = get_raw_files(raw_dir)

    output_dir = (
        OUTPUT_ROOT
        / "udise"
        / dataset_name
    )

    school_dir = output_dir / "schools"

    all_records = []
    snapshots = []

    counters = Counter()

    for raw_file in raw_files:
        try:
            raw = load_json(raw_file)

            record = map_udise_record(
                raw,
                raw_file,
                dataset_name,
            )

            udise = record[
                "metadata"
            ]["udise_code"]

            if not udise:
                counters["missing_udise"] += 1

            output_file = (
                school_dir
                / f"{udise or record['metadata']['record_id']}.json"
            )

            save_json(
                output_file,
                record,
            )

            all_records.append(record)

            snapshots.append(
                build_yearly_snapshot(
                    record
                )
            )

            counters["success"] += 1

        except Exception as exc:
            counters["failed"] += 1

            print(
                f"[ERROR] {raw_file.name}: {exc}"
            )

    save_json(
        output_dir / "_all_schools.json",
        all_records,
    )

    save_json(
        output_dir / "_yearly_snapshots.json",
        snapshots,
    )

    summary = {
        "dataset": dataset_name,
        "schema_version": SCHEMA_VERSION,
        "academic_year": ACADEMIC_YEAR,

        "raw_files": len(raw_files),

        "success": counters["success"],
        "failed": counters["failed"],
        "missing_udise": counters[
            "missing_udise"
        ],

        "generated_at": utc_now(),

        "output_directory": str(
            output_dir
        ),
    }

    save_json(
        output_dir / "_summary.json",
        summary,
    )

    print(
        f"Raw files       : {len(raw_files)}"
    )

    print(
        f"Success         : {counters['success']}"
    )

    print(
        f"Failed          : {counters['failed']}"
    )

    print(
        f"Missing UDISE   : {counters['missing_udise']}"
    )

    return summary


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 72)
    print("SCHOOLFINDER CANONICAL V4 UDISE MAPPER")
    print("=" * 72)

    print(
        "Raw UDISE files will NOT be modified."
    )

    print(
        "Old data/standardized files will NOT be modified."
    )

    print(
        f"New output: {OUTPUT_ROOT}"
    )

    overall = {
        "schema_version": SCHEMA_VERSION,
        "academic_year": ACADEMIC_YEAR,
        "datasets": {},
        "generated_at": utc_now(),
    }

    total_raw = 0
    total_success = 0
    total_failed = 0

    for dataset_name, raw_dir in DATASETS.items():

        if not raw_dir.exists():
            print(
                f"[WARNING] Missing directory: {raw_dir}"
            )
            continue

        summary = process_dataset(
            dataset_name,
            raw_dir,
        )

        overall["datasets"][
            dataset_name
        ] = summary

        total_raw += summary["raw_files"]
        total_success += summary["success"]
        total_failed += summary["failed"]

    overall["totals"] = {
        "raw_files": total_raw,
        "success": total_success,
        "failed": total_failed,
    }

    save_json(
        OUTPUT_ROOT / "_summary.json",
        overall,
    )

    print()
    print("=" * 72)
    print("CANONICAL V4 GENERATION COMPLETED")
    print("=" * 72)

    print(
        f"Total raw files : {total_raw}"
    )

    print(
        f"Total generated : {total_success}"
    )

    print(
        f"Total failed    : {total_failed}"
    )

    print()
    print(
        f"Output: {OUTPUT_ROOT}"
    )

    print()
    print(
        "IMPORTANT: Run validation before Git commit/push."
    )


if __name__ == "__main__":
    main()