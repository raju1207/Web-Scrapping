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

STATE_NAME = "Chandigarh"

ACADEMIC_YEAR = "2025-26"

# Confirmed from UDISE+:
# 12 = 2025-26
YEAR_ID = 12

SCHOOL_LIST_FILE = Path("data") / "chandigarh_school_list.json"

OUTPUT_DIR = (
    Path("data")
    / STATE_NAME
    / ACADEMIC_YEAR
)

ERROR_DIR = OUTPUT_DIR / "_errors"

REQUEST_DELAY = 1.0
TIMEOUT = 60


# ============================================================
# SESSION
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
# HELPERS
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
# EXTRACT API DATA
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
# FIND SCHOOL LIST RECURSIVELY
# ============================================================

def find_school_list(obj):

    if isinstance(obj, list):

        if len(obj) == 0:

            return []

        # Check if this looks like school records
        first = obj[0]

        if isinstance(first, dict):

            keys = set(first.keys())

            school_keys = {
                "udiseschCode",
                "udiseSchCode",
                "udiseCode",
                "schoolName",
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

        # Most likely keys first
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

        # Search remaining values
        for value in obj.values():

            result = find_school_list(
                value
            )

            if result:

                return result

    return []


# ============================================================
# LOAD SCHOOL LIST FROM BROWSER RESPONSE
# ============================================================

def load_school_list():

    print()
    print("=" * 65)
    print("LOADING CHANDIGARH SCHOOL LIST")
    print("=" * 65)

    if not SCHOOL_LIST_FILE.exists():

        print()
        print(
            "❌ School list file not found:"
        )

        print(
            SCHOOL_LIST_FILE
        )

        print()
        print(
            "Save the UDISE+ by-region "
            "Response as:"
        )

        print(
            "data/chandigarh_school_list.json"
        )

        return []

    try:

        with open(
            SCHOOL_LIST_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            raw_data = json.load(
                file
            )

    except json.JSONDecodeError as error:

        print()
        print(
            "❌ chandigarh_school_list.json "
            "is not valid JSON."
        )

        print(
            error
        )

        return []

    schools = find_school_list(
        raw_data
    )

    print()
    print(
        f"Schools found: {len(schools)}"
    )

    if schools:

        save_json(
            OUTPUT_DIR /
            "_all_schools.json",
            schools
        )

    return schools


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
# FACILITIES / INFRASTRUCTURE
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
# SCRAPE ONE SCHOOL
# ============================================================

def scrape_school(
    school,
    index,
    total
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
            "UDISE code missing - skipped."
        )

        return False


    if not school_name:

        school_name = (
            f"SCHOOL_{udise_code}"
        )


    print()
    print("-" * 65)

    print(
        f"[{index}/{total}] "
        f"{school_name}"
    )

    print(
        f"UDISE: {udise_code}"
    )


    # ========================================================
    # PROFILE
    # ========================================================

    print(
        "   → Profile"
    )

    profile_result = fetch_profile(
        udise_code
    )

    profile = extract_api_data(
        profile_result
    )

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # STUDENTS + TEACHERS
    # ========================================================

    print(
        "   → Students / Teachers"
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

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # FACILITIES
    # ========================================================

    print(
        "   → Infrastructure / Facilities"
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

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # REPORT CARD
    # ========================================================

    print(
        "   → Report Card"
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

    time.sleep(
        REQUEST_DELAY
    )


    # ========================================================
    # HISTORY
    # ========================================================

    history = None

    if school_id:

        print(
            "   → School History"
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

            "academic_year":
                ACADEMIC_YEAR,

            "year_id":
                YEAR_ID,

            "state":
                STATE_NAME,

            "school_name":
                school_name,

            "udise_code":
                str(udise_code),

            "school_id":
                school_id
        },


        "school_summary":
            school,


        "school_profile":
            profile,


        "student_teacher_statistics":
            student_teacher,


        "infrastructure_facilities":
            facilities,


        "report_card":
            report_card,


        "school_history":
            history
    }


    filename = (

        f"{safe_filename(school_name)}"
        f"_{udise_code}.json"

    )


    save_json(

        OUTPUT_DIR /
        filename,

        final_data

    )


    print(
        f"   ✓ Saved: {filename}"
    )


    return True


# ============================================================
# MAIN
# ============================================================

def main():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    ERROR_DIR.mkdir(
        parents=True,
        exist_ok=True
    )


    print()
    print("=" * 65)

    print(
        "UDISE+ CHANDIGARH SCHOOL SCRAPER"
    )

    print(
        f"Academic Year : {ACADEMIC_YEAR}"
    )

    print(
        f"YEAR_ID       : {YEAR_ID}"
    )

    print("=" * 65)


    schools = load_school_list()


    if not schools:

        print()
        print(
            "❌ No schools loaded."
        )

        print(
            "Check data/chandigarh_school_list.json"
        )

        return


    total = len(
        schools
    )

    successful = 0

    failed = []


    print()
    print("=" * 65)

    print(
        f"STARTING {total} SCHOOLS"
    )

    print("=" * 65)


    for index, school in enumerate(
        schools,
        start=1
    ):

        try:

            success = scrape_school(

                school,
                index,
                total

            )

            if success:

                successful += 1


        except KeyboardInterrupt:

            print()
            print(
                "Stopped manually."
            )

            break


        except Exception as error:

            school_name = get_value(
                school,
                "schoolName",
                "schName"
            )

            udise_code = get_value(
                school,
                "udiseschCode",
                "udiseSchCode",
                "udiseCode"
            )


            print()
            print(
                f"❌ ERROR: "
                f"{school_name}"
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
    # SUMMARY
    # ========================================================

    summary = {

        "state":
            STATE_NAME,

        "academic_year":
            ACADEMIC_YEAR,

        "year_id":
            YEAR_ID,

        "total_schools":
            total,

        "successful":
            successful,

        "failed":
            len(failed)
    }


    save_json(

        OUTPUT_DIR /
        "_summary.json",

        summary

    )


    if failed:

        save_json(

            ERROR_DIR /
            "failed_schools.json",

            failed

        )


    print()
    print("=" * 65)

    print(
        "SCRAPING COMPLETED"
    )

    print("=" * 65)

    print(
        f"Total Schools : {total}"
    )

    print(
        f"Successful    : {successful}"
    )

    print(
        f"Failed        : {len(failed)}"
    )

    print(
        f"Output Folder : {OUTPUT_DIR}"
    )

    print("=" * 65)


if __name__ == "__main__":
    main()