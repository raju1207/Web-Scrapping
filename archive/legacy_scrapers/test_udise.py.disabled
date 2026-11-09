import json
import requests


BASE_URL = "https://kys.udiseplus.gov.in/web-app/api"

UDISE_CODE = "04010800107"

TARGET_YEAR = "2026-27"

YEAR_ID = 0


session = requests.Session()

session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://kys.udiseplus.gov.in/",
    "Origin": "https://kys.udiseplus.gov.in",

    # Required by UDISE+ frontend
    "X-APP-SIGNATURE": "9f2c7a4b8e1d6c3f5a9b0e2d4f6a7c8b"
})


# ============================================================
# GET AVAILABLE YEAR IDs
# ============================================================

def get_year_id():

    url = f"{BASE_URL}/master/year"

    params = {
        "year": 1
    }

    print("\n" + "=" * 60)
    print("CHECKING ACADEMIC YEARS")
    print("=" * 60)

    try:

        response = session.get(
            url,
            params=params,
            timeout=60
        )

        print("Status:", response.status_code)
        print("URL:", response.url)

        response.raise_for_status()

        data = response.json()

        print("\nRaw year response:")
        print(
            json.dumps(
                data,
                indent=2,
                ensure_ascii=False
            )
        )

        # Search recursively for year
        def search_year(obj):

            if isinstance(obj, list):

                for item in obj:

                    result = search_year(item)

                    if result is not None:
                        return result

            elif isinstance(obj, dict):

                year_value = (
                    obj.get("year")
                    or obj.get("academicYear")
                    or obj.get("yearName")
                    or obj.get("sessionYear")
                )

                year_id = (
                    obj.get("yearId")
                    or obj.get("id")
                )

                if str(year_value).strip() == TARGET_YEAR:
                    return year_id

                for value in obj.values():

                    result = search_year(value)

                    if result is not None:
                        return result

            return None

        year_id = search_year(data)

        if year_id is None:

            print(
                f"\nCould not automatically find "
                f"{TARGET_YEAR}"
            )

            return None

        print(
            f"\n✅ {TARGET_YEAR} YEAR_ID = {year_id}"
        )

        return year_id

    except Exception as error:

        print(
            "\nYEAR API ERROR:",
            error
        )

        return None


# ============================================================
# TEST ONE API
# ============================================================

def test_api(name, endpoint, year_id):

    url = f"{BASE_URL}/{endpoint}"

    params = {
        "udiseSchCode": UDISE_CODE,
        "yearId": year_id
    }

    print("\n" + "=" * 60)
    print(name)
    print("=" * 60)

    print("URL:", url)
    print("Params:", params)

    try:

        response = session.get(
            url,
            params=params,
            timeout=60
        )

        print("Final URL:", response.url)
        print("Status:", response.status_code)

        print("\nResponse:")

        try:

            data = response.json()

            print(
                json.dumps(
                    data,
                    indent=2,
                    ensure_ascii=False
                )[:10000]
            )

        except Exception:

            print(
                response.text[:3000]
            )

    except requests.exceptions.Timeout:

        print(
            "ERROR: Request timed out."
        )

    except Exception as error:

        print(
            "ERROR:",
            error
        )


# ============================================================
# MAIN
# ============================================================

def main():

    year_id = YEAR_ID

    print("\n" + "=" * 60)
    print(f"Academic Year: {TARGET_YEAR}")
    print(f"Using YEAR_ID: {year_id}")
    print("=" * 60)

    test_api(
        "SCHOOL PROFILE",
        "school/profile",
        year_id
    )

    test_api(
        "FACILITIES",
        "school/facility",
        year_id
    )

    test_api(
        "REPORT CARD",
        "school/report-card",
        year_id
    )

    test_api(
        "ENROLMENT + TEACHERS",
        "school-statistics/enrolment-teacher",
        year_id
    )


if __name__ == "__main__":
    main()