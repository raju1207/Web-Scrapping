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

# Request delay between API calls.
# Keep a small delay to avoid sending too many requests quickly.
REQUEST_DELAY = 0.4

TIMEOUT = 60


# ============================================================
# LOCATIONS
#
# IMPORTANT:
# Chandigarh is intentionally NOT included.
#
# The scraper processes ALL schools available inside:
#
# data/delhi_school_list.json
# data/mumbai_school_list.json
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
# CREATE REQUEST SESSION
# ============================================================

def create_session():

    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.5,
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

    # Remove characters not allowed in Windows file names
    name = re.sub(
        r'[<>:"/\\|?*]',
        "",
        name
    )

    # Replace spaces with _
    name = re.sub(
        r"\s+",
        "_",
        name
    )

    # Remove repeated _
    name = re.sub(
        r"_+",
        "_",
        name
    )

    # Avoid extremely long file names
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

        # Sometimes HTTP = 200 but API status = false
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
                    "error":
                        error_message
                        or "UDISE API returned status=false",
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

    except requests.exceptions.RequestException as error:

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
# EXTRACT DATA FROM API RESPONSE
# ============================================================

def extract_api_data(result):

    if not result:
        return None

    if not result.get("success"):
        return None

    response = result.get("response")

    if not isinstance(response, dict):
        return response

    if response.get("status") is True:

        return response.get("data")

    return None


# ============================================================
# GET VALUE FROM MULTIPLE POSSIBLE KEYS
# ============================================================

def get_value(data, *keys):

    if not isinstance(data, dict):
        return None

    for key in keys:

        value = data.get(key)

        if value not in [
            None,
            ""
        ]:

            return value

    return None


# ============================================================
# FIND SCHOOL LIST INSIDE SAVED JSON RESPONSE
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

        # Search most common API structures first

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
# REMOVE DUPLICATE SCHOOLS
# ============================================================

def remove_duplicate_schools(schools):

    unique_schools = []

    seen_codes = set()

    for school in schools:

        udise_code = get_value(
            school,
            "udiseschCode",
            "udiseSchCode",
            "udiseCode"
        )

        if not udise_code:
            continue

        udise_code = str(
            udise_code
        )

        if udise_code not in seen_codes:

            seen_codes.add(
                udise_code
            )

            unique_schools.append(
                school
            )

    return unique_schools


# ============================================================
# LOAD FULL SCHOOL LIST
# ============================================================

def load_school_list(location):

    list_file = location[
        "school_list_file"
    ]

    location_name = location[
        "name"
    ]


    print()
    print("=" * 75)

    print(
        f"LOADING {location_name.upper()} SCHOOL LIST"
    )

    print("=" * 75)


    if not list_file.exists():

        print()

        print(
            f"❌ File not found: {list_file}"
        )

        print()

        print(
            "Please save the full UDISE+ school-list "
            "response in this file."
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
            f"❌ Invalid JSON: {list_file}"
        )

        print(
            error
        )

        return []


    schools = find_school_list(
        raw_data
    )


    print(
        f"Schools found in source file : {len(schools)}"
    )


    # Remove duplicate UDISE codes
    schools = remove_duplicate_schools(
        schools
    )


    print(
        f"Unique schools              : {len(schools)}"
    )


    print(
        "School limit                : NONE / ALL"
    )


    # ========================================================
    # IMPORTANT
    #
    # There is NO:
    #
    # schools[:25]
    #
    # here.
    #
    # ALL schools are returned.
    # ========================================================

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
# STUDENT + TEACHER STATISTICS
# ============================================================

def fetch_student_teacher(udise_code):

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

def fetch_facilities(udise_code):

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

def fetch_report_card(udise_code):

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

def fetch_history(school_id):

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
# PRINT API STATUS
# ============================================================

def print_api_status(label, result):

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

        print()

        print(
            f"[{index}/{total}] "
            "⚠ UDISE code missing - SKIPPED"
        )

        return "failed"


    udise_code = str(
        udise_code
    )


    if not school_name:

        school_name = (
            f"SCHOOL_{udise_code}"
        )


    # ========================================================
    # OUTPUT FILE NAME
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
    # If school already exists:
    # don't request API again.
    # ========================================================

    if output_file.exists():

        print()

        print(
            f"[{index}/{total}] "
            f"{school_name}"
        )

        print(
            f"   UDISE: {udise_code}"
        )

        print(
            "   ✓ Already exists - SKIPPED"
        )

        return "existing"


    print()
    print("-" * 75)

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
        "   → Fetching School Profile..."
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
    # STUDENT + TEACHER
    # ========================================================

    print(
        "   → Fetching Student / Teacher Statistics..."
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
        "Student / Teacher",
        student_teacher_result
    )

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # FACILITIES
    # ========================================================

    print(
        "   → Fetching Infrastructure / Facilities..."
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
        "   → Fetching Report Card..."
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
    # HISTORY
    # ========================================================

    history = None

    history_result = None


    if school_id:

        print(
            "   → Fetching School History..."
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
    # FINAL SCHOOL DATA
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
                udise_code,

            "school_id":
                school_id
        },


        # School details from list response

        "school_summary":
            school,


        # School profile

        "school_profile":
            profile,


        # Student and teacher totals

        "student_teacher_statistics":
            student_teacher,


        # Building, toilet, library, internet etc.

        "infrastructure_facilities":
            facilities,


        # UDISE report-card information

        "report_card":
            report_card,


        # Previous academic years/history

        "school_history":
            history,


        # API information useful for debugging

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


    # ========================================================
    # SAVE SCHOOL JSON
    # ========================================================

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

def process_location(location):

    location_name = location[
        "name"
    ]


    # ========================================================
    # OUTPUT DIRECTORY
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
    # LOAD ALL SCHOOLS
    # ========================================================

    schools = load_school_list(
        location
    )


    if not schools:

        print()

        print(
            f"❌ No schools found for {location_name}"
        )

        return


    total = len(
        schools
    )


    # ========================================================
    # SAVE COMPLETE SCHOOL LIST
    # ========================================================

    save_json(

        output_dir /
        "_all_schools.json",

        schools

    )


    print()
    print("=" * 75)

    print(
        f"STARTING {location_name.upper()} FULL SCRAPING"
    )

    print("=" * 75)

    print(
        f"Academic Year : {ACADEMIC_YEAR}"
    )

    print(
        f"YEAR_ID       : {YEAR_ID}"
    )

    print(
        f"Total Schools : {total}"
    )

    print(
        "Limit         : NONE"
    )

    print("=" * 75)


    successful = 0

    existing = 0

    failed = []


    # ========================================================
    # PROCESS EVERY SCHOOL
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

            print("=" * 75)

            print(
                "⚠ SCRAPING STOPPED MANUALLY"
            )

            print("=" * 75)

            print(
                f"Location          : {location_name}"
            )

            print(
                f"Newly scraped     : {successful}"
            )

            print(
                f"Already existing  : {existing}"
            )

            print()

            print(
                "All completed JSON files are safe."
            )

            print(
                "Run 'python scraper.py' again "
                "to continue."
            )

            print("=" * 75)

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
                f"UDISE: {udise_code}"
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
    # SAVE ERROR LIST
    # ========================================================

    failed_file = (
        error_dir /
        "failed_schools.json"
    )


    if failed:

        save_json(
            failed_file,
            failed
        )

    elif failed_file.exists():

        # Delete old errors if latest run has no failures

        try:
            failed_file.unlink()

        except OSError:
            pass


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

        "school_limit":
            "ALL",

        "total_schools":
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


    # ========================================================
    # FINAL LOCATION RESULT
    # ========================================================

    print()
    print("=" * 75)

    print(
        f"{location_name.upper()} COMPLETED"
    )

    print("=" * 75)


    print(
        f"Total Schools     : {total}"
    )

    print(
        f"Newly Scraped     : {successful}"
    )

    print(
        f"Already Existing  : {existing}"
    )

    print(
        f"Failed            : {len(failed)}"
    )

    print(
        f"Completed Total   : {successful + existing}"
    )

    print(
        f"Output Folder     : {output_dir}"
    )

    print("=" * 75)


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 75)

    print(
        "UDISE+ DELHI + MUMBAI FULL DATA SCRAPER"
    )

    print("=" * 75)

    print(
        f"Academic Year : {ACADEMIC_YEAR}"
    )

    print(
        f"YEAR_ID       : {YEAR_ID}"
    )

    print(
        "School Limit  : ALL SCHOOLS"
    )

    print()

    print(
        "Delhi         : ALL"
    )

    print(
        "Mumbai        : ALL"
    )

    print(
        "Chandigarh    : NOT INCLUDED"
    )

    print("=" * 75)


    try:

        for location in LOCATIONS:

            process_location(
                location
            )


    except KeyboardInterrupt:

        print()
        print("=" * 75)

        print(
            "PROGRAM STOPPED"
        )

        print()

        print(
            "Completed school files are preserved."
        )

        print(
            "Run:"
        )

        print(
            "python scraper.py"
        )

        print()

        print(
            "to resume remaining schools."
        )

        print("=" * 75)

        return


    print()
    print("=" * 75)

    print(
        "ALL DELHI + MUMBAI SCHOOLS COMPLETED"
    )

    print("=" * 75)


if __name__ == "__main__":
    main()