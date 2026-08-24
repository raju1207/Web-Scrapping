import json
import re
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://kys.udiseplus.gov.in/web-app/api"

ACADEMIC_YEAR = "2025-26"
YEAR_ID = 12

# Only 25 schools from each location
MAX_SCHOOLS = 25

# Faster than previous scraper, but still avoids hammering server
REQUEST_DELAY = 0.4
TIMEOUT = 60


# ============================================================
# LOCATIONS
#
# IMPORTANT:
# These school-list JSON files must be copied manually from
# the browser Network -> by-region -> Response.
#
# Chandigarh is intentionally NOT included.
# ============================================================

LOCATIONS = [
    {
        "name": "Delhi",
        "school_list_file": Path("data") / "delhi_school_list.json",
    },
    {
        "name": "Mumbai",
        "school_list_file": Path("data") / "mumbai_school_list.json",
    },
]


# ============================================================
# CREATE SESSION
# ============================================================

def create_session():

    session = requests.Session()

    retry = Retry(
        total=4,
        connect=4,
        read=4,
        backoff_factor=1.0,
        status_forcelist=[
            429,
            500,
            502,
            503,
            504
        ],
        allowed_methods=["GET"]
    )

    adapter = HTTPAdapter(
        max_retries=retry
    )

    session.mount(
        "https://",
        adapter
    )

    session.mount(
        "http://",
        adapter
    )

    session.headers.update({

        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/151.0.0.0 "
            "Safari/537.36"
        ),

        "Accept":
            "application/json, text/plain, */*",

        "Referer":
            "https://kys.udiseplus.gov.in/",

        "Origin":
            "https://kys.udiseplus.gov.in",

        "X-APP-SIGNATURE":
            "9f2c7a4b8e1d6c3f5a9b0e2d4f6a7c8b"
    })

    return session


session = create_session()


# ============================================================
# SAFE FILE NAME
# ============================================================

def safe_filename(name):

    name = str(name).strip()

    name = re.sub(
        r'[<>:"/\\|?*]',
        "",
        name
    )

    name = re.sub(
        r"\s+",
        "_",
        name
    )

    name = re.sub(
        r"_+",
        "_",
        name
    )

    return name[:160]


# ============================================================
# SAVE JSON
# ============================================================

def save_json(path, data):

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        path,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            indent=4,
            ensure_ascii=False
        )


# ============================================================
# API REQUEST
# ============================================================

def get_json(endpoint, params=None):

    url = f"{BASE_URL}/{endpoint}"

    try:

        response = session.get(
            url,
            params=params,
            timeout=TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        # HTTP 200 does not always mean UDISE returned data.
        if isinstance(data, dict):

            if data.get("status") is False:

                error_message = (
                    data.get("error", {})
                    .get("errorDetails", {})
                    .get("details")
                )

                return {
                    "success": False,
                    "url": response.url,
                    "error": error_message or "UDISE API returned status=false",
                    "response": data
                }

        return {
            "success": True,
            "url": response.url,
            "response": data
        }

    except requests.exceptions.Timeout:

        return {
            "success": False,
            "url": url,
            "error": "Request timed out"
        }

    except requests.RequestException as error:

        return {
            "success": False,
            "url": url,
            "error": str(error)
        }

    except ValueError:

        return {
            "success": False,
            "url": url,
            "error": "Invalid JSON response"
        }


# ============================================================
# EXTRACT DATA
# ============================================================

def extract_api_data(result):

    if not result:

        return None

    if not result.get("success"):

        return None

    response = result.get(
        "response"
    )

    if not isinstance(
        response,
        dict
    ):

        return response

    if response.get("status") is True:

        return response.get(
            "data"
        )

    return None


# ============================================================
# GET FIELD
# ============================================================

def get_value(data, *keys):

    if not isinstance(
        data,
        dict
    ):

        return None

    for key in keys:

        value = data.get(
            key
        )

        if value not in [
            None,
            ""
        ]:

            return value

    return None


# ============================================================
# FIND SCHOOL LIST INSIDE SAVED RESPONSE
# ============================================================

def find_school_list(obj):

    if isinstance(obj, list):

        if not obj:

            return []

        first = obj[0]

        if isinstance(first, dict):

            keys = set(
                first.keys()
            )

            school_keys = {
                "udiseschCode",
                "udiseSchCode",
                "udiseCode",
                "schoolName",
                "schName",
                "schoolId"
            }

            if keys.intersection(
                school_keys
            ):

                return obj

        for item in obj:

            result = find_school_list(
                item
            )

            if result:

                return result


    elif isinstance(obj, dict):

        # Search likely locations first
        for key in [
            "content",
            "schools",
            "schoolList",
            "results",
            "data"
        ]:

            if key in obj:

                result = find_school_list(
                    obj[key]
                )

                if result:

                    return result

        # Recursive fallback
        for value in obj.values():

            result = find_school_list(
                value
            )

            if result:

                return result


    return []


# ============================================================
# LOAD SCHOOL LIST
# ============================================================

def load_school_list(location):

    list_file = location[
        "school_list_file"
    ]

    location_name = location[
        "name"
    ]

    print()
    print("=" * 70)

    print(
        f"LOADING {location_name.upper()} SCHOOL LIST"
    )

    print("=" * 70)


    if not list_file.exists():

        print()
        print(
            f"❌ File not found: {list_file}"
        )

        print(
            "Copy the UDISE+ by-region browser response "
            "and save it at this location."
        )

        return []


    try:

        with open(
            list_file,
            "r",
            encoding="utf-8"
        ) as file:

            raw_data = json.load(
                file
            )

    except json.JSONDecodeError as error:

        print()
        print(
            f"❌ Invalid JSON in {list_file}"
        )

        print(
            error
        )

        return []


    schools = find_school_list(
        raw_data
    )


    print(
        f"Total schools available: {len(schools)}"
    )


    # ========================================================
    # ONLY FIRST 25
    # ========================================================

    schools = schools[
        :MAX_SCHOOLS
    ]


    print(
        f"Schools selected for scraping: {len(schools)}"
    )


    return schools


# ============================================================
# SCHOOL PROFILE
# ============================================================

def fetch_profile(udise_code):

    return get_json(

        "school/profile",

        {
            "udiseSchCode":
                udise_code,

            "yearId":
                YEAR_ID
        }
    )


# ============================================================
# STUDENT + TEACHER
# ============================================================

def fetch_student_teacher(
    udise_code
):

    return get_json(

        "school-statistics/enrolment-teacher",

        {
            "udiseSchCode":
                udise_code,

            "yearId":
                YEAR_ID
        }
    )


# ============================================================
# INFRASTRUCTURE / FACILITIES
# ============================================================

def fetch_facilities(
    udise_code
):

    return get_json(

        "school/facility",

        {
            "udiseSchCode":
                udise_code,

            "yearId":
                YEAR_ID
        }
    )


# ============================================================
# REPORT CARD
# ============================================================

def fetch_report_card(
    udise_code
):

    return get_json(

        "school/report-card",

        {
            "udiseSchCode":
                udise_code,

            "yearId":
                YEAR_ID
        }
    )


# ============================================================
# SCHOOL HISTORY
# ============================================================

def fetch_history(
    school_id
):

    if not school_id:

        return None

    return get_json(

        "school/track",

        {
            "schoolId":
                school_id
        }
    )


# ============================================================
# CHECK API STATUS
# ============================================================

def print_api_status(
    label,
    result
):

    if result is None:

        print(
            f"      ⚠ {label}: Not available"
        )

        return


    if result.get("success"):

        print(
            f"      ✓ {label}"
        )

    else:

        print(
            f"      ⚠ {label}: "
            f"{result.get('error')}"
        )


# ============================================================
# SCRAPE ONE SCHOOL
# ============================================================

def scrape_school(
    school,
    index,
    total,
    location_name,
    output_dir
):

    udise_code = get_value(

        school,

        "udiseschCode",
        "udiseSchCode",
        "udiseCode"

    )


    school_name = get_value(

        school,

        "schoolName",
        "schName",
        "name"

    )


    school_id = get_value(

        school,

        "schoolId",
        "schoolid"

    )


    if not udise_code:

        print(
            f"[{index}/{total}] "
            "⚠ UDISE code missing - skipped."
        )

        return "failed"


    if not school_name:

        school_name = (
            f"SCHOOL_{udise_code}"
        )


    # ========================================================
    # FILE NAME
    # ========================================================

    filename = (

        f"{safe_filename(school_name)}"
        f"_{udise_code}.json"

    )


    output_file = (

        output_dir /
        filename

    )


    # ========================================================
    # RESUME SUPPORT
    #
    # If this school already exists, don't scrape it again.
    # ========================================================

    if output_file.exists():

        print()
        print(
            f"[{index}/{total}] "
            f"{school_name}"
        )

        print(
            f"   ✓ Already exists - SKIPPED"
        )

        return "existing"


    print()
    print("-" * 70)

    print(
        f"[{index}/{total}] "
        f"{school_name}"
    )

    print(
        f"UDISE Code: {udise_code}"
    )


    # ========================================================
    # PROFILE
    # ========================================================

    print(
        "   → Fetching profile..."
    )

    profile_result = fetch_profile(
        udise_code
    )

    profile = extract_api_data(
        profile_result
    )

    print_api_status(
        "Profile",
        profile_result
    )

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # STUDENT / TEACHER
    # ========================================================

    print(
        "   → Fetching student / teacher data..."
    )

    student_teacher_result = (
        fetch_student_teacher(
            udise_code
        )
    )

    student_teacher = (
        extract_api_data(
            student_teacher_result
        )
    )

    print_api_status(
        "Student/Teacher",
        student_teacher_result
    )

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # FACILITIES
    # ========================================================

    print(
        "   → Fetching facilities..."
    )

    facilities_result = (
        fetch_facilities(
            udise_code
        )
    )

    facilities = (
        extract_api_data(
            facilities_result
        )
    )

    print_api_status(
        "Facilities",
        facilities_result
    )

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # REPORT CARD
    # ========================================================

    print(
        "   → Fetching report card..."
    )

    report_result = (
        fetch_report_card(
            udise_code
        )
    )

    report_card = (
        extract_api_data(
            report_result
        )
    )

    print_api_status(
        "Report Card",
        report_result
    )

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # SCHOOL HISTORY
    # ========================================================

    history = None
    history_result = None


    if school_id:

        print(
            "   → Fetching school history..."
        )

        history_result = (
            fetch_history(
                school_id
            )
        )

        history = (
            extract_api_data(
                history_result
            )
        )

        print_api_status(
            "School History",
            history_result
        )

        time.sleep(
            REQUEST_DELAY
        )


    # ========================================================
    # FINAL JSON
    # ========================================================

    final_data = {

        "metadata": {

            "source":
                "UDISE+ Know Your School",

            "location":
                location_name,

            "academic_year":
                ACADEMIC_YEAR,

            "year_id":
                YEAR_ID,

            "school_name":
                school_name,

            "udise_code":
                str(udise_code),

            "school_id":
                school_id
        },


        # Basic information from school-list response

        "school_summary":
            school,


        # School profile information

        "school_profile":
            profile,


        # Student / teacher statistics

        "student_teacher_statistics":
            student_teacher,


        # Infrastructure

        "infrastructure_facilities":
            facilities,


        # School report card

        "report_card":
            report_card,


        # School tracking / historical information

        "school_history":
            history,


        # Useful when an endpoint returned no data

        "_api_status": {

            "profile":
                profile_result,

            "student_teacher":
                student_teacher_result,

            "facilities":
                facilities_result,

            "report_card":
                report_result,

            "history":
                history_result
        }
    }


    save_json(
        output_file,
        final_data
    )


    print(
        f"   ✓ SAVED: {filename}"
    )


    return "success"


# ============================================================
# PROCESS ONE LOCATION
# ============================================================

def process_location(
    location
):

    location_name = location[
        "name"
    ]


    # ========================================================
    # OUTPUT FOLDER
    # ========================================================

    output_dir = (

        Path("data")
        / location_name
        / ACADEMIC_YEAR

    )


    error_dir = (

        output_dir /
        "_errors"

    )


    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )


    error_dir.mkdir(
        parents=True,
        exist_ok=True
    )


    # ========================================================
    # LOAD SCHOOLS
    # ========================================================

    schools = load_school_list(
        location
    )


    if not schools:

        print()
        print(
            f"❌ No schools loaded for {location_name}."
        )

        return


    # ========================================================
    # SAVE SELECTED 25 SCHOOLS
    # ========================================================

    save_json(

        output_dir /
        "_all_schools.json",

        schools

    )


    total = len(
        schools
    )


    print()
    print("=" * 70)

    print(
        f"STARTING {location_name.upper()}"
    )

    print(
        f"Academic Year : {ACADEMIC_YEAR}"
    )

    print(
        f"Schools       : {total}"
    )

    print("=" * 70)


    successful = 0
    existing = 0
    failed = []


    # ========================================================
    # SCRAPE EACH SCHOOL
    # ========================================================

    for index, school in enumerate(
        schools,
        start=1
    ):

        try:

            result = scrape_school(

                school,
                index,
                total,
                location_name,
                output_dir

            )


            if result == "success":

                successful += 1


            elif result == "existing":

                existing += 1


            else:

                failed.append(
                    {
                        "school":
                            school,

                        "error":
                            "School could not be processed"
                    }
                )


        except KeyboardInterrupt:

            print()
            print()
            print(
                "⚠ SCRAPING STOPPED MANUALLY"
            )

            print(
                "Existing JSON files are safe."
            )

            print(
                "Run 'python scraper.py' later "
                "to resume."
            )

            raise


        except Exception as error:

            school_name = get_value(
                school,
                "schoolName",
                "schName",
                "name"
            )


            udise_code = get_value(
                school,
                "udiseschCode",
                "udiseSchCode",
                "udiseCode"
            )


            print()
            print(
                f"❌ ERROR: {school_name}"
            )

            print(
                error
            )


            failed.append({

                "school_name":
                    school_name,

                "udise_code":
                    udise_code,

                "error":
                    str(error)

            })


    # ========================================================
    # SAVE FAILED SCHOOLS
    # ========================================================

    if failed:

        save_json(

            error_dir /
            "failed_schools.json",

            failed

        )


    # ========================================================
    # SUMMARY
    # ========================================================

    summary = {

        "location":
            location_name,

        "academic_year":
            ACADEMIC_YEAR,

        "year_id":
            YEAR_ID,

        "maximum_requested":
            MAX_SCHOOLS,

        "schools_selected":
            total,

        "newly_scraped":
            successful,

        "already_existing":
            existing,

        "failed":
            len(failed),

        "completed_total":
            successful + existing

    }


    save_json(

        output_dir /
        "_summary.json",

        summary

    )


    print()
    print("=" * 70)

    print(
        f"{location_name.upper()} COMPLETED"
    )

    print("=" * 70)

    print(
        f"Schools Selected : {total}"
    )

    print(
        f"Newly Scraped    : {successful}"
    )

    print(
        f"Already Existing : {existing}"
    )

    print(
        f"Failed           : {len(failed)}"
    )

    print(
        f"Output           : {output_dir}"
    )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)

    print(
        "UDISE+ MULTI-LOCATION SCHOOL SCRAPER"
    )

    print("=" * 70)

    print(
        f"Academic Year : {ACADEMIC_YEAR}"
    )

    print(
        f"YEAR_ID       : {YEAR_ID}"
    )

    print(
        f"Maximum       : {MAX_SCHOOLS} schools per location"
    )

    print()

    print(
        "Locations:"
    )

    print(
        "   1. Delhi"
    )

    print(
        "   2. Mumbai"
    )

    print()

    print(
        "Chandigarh: NOT INCLUDED / WILL NOT BE SCRAPED"
    )

    print("=" * 70)


    try:

        for location in LOCATIONS:

            process_location(
                location
            )


    except KeyboardInterrupt:

        print()
        print("=" * 70)

        print(
            "PROGRAM STOPPED"
        )

        print(
            "Your completed JSON files have been preserved."
        )

        print(
            "Run python scraper.py again to resume."
        )

        print("=" * 70)

        return


    print()
    print("=" * 70)

    print(
        "ALL REQUESTED LOCATIONS COMPLETED"
    )

    print("=" * 70)

    print(
        "Delhi  : Maximum 25 schools"
    )

    print(
        "Mumbai : Maximum 25 schools"
    )

    print(
        "Chandigarh : Skipped"
    )

    print("=" * 70)


if __name__ == "__main__":
    main()