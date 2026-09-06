import os
import re
import json
import time
from pathlib import Path

from bs4 import BeautifulSoup

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ============================================================
# CONFIGURATION
# ============================================================

BASE_URL = "https://saras.cbse.gov.in"

LIST_URL = (
    "https://saras.cbse.gov.in/"
    "SARAS/AffiliatedList/ListOfSchdirReport"
)

DETAIL_BASE_URL = (
    "https://saras.cbse.gov.in/"
    "SARAS/AffiliatedList/AfflicationDetails/"
)

STATE_NAME = "CHANDIGARH"
DISTRICT_NAME = "CHANDIGARH"

OUTPUT_DIR = Path("data/CBSE_SARAS/Chandigarh")
SCHOOL_DIR = OUTPUT_DIR / "schools"
ERROR_DIR = OUTPUT_DIR / "_errors"

SUMMARY_FILE = OUTPUT_DIR / "_summary.json"
ALL_SCHOOLS_FILE = OUTPUT_DIR / "_all_schools.json"

REQUEST_DELAY = 1.0

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SCHOOL_DIR.mkdir(parents=True, exist_ok=True)
ERROR_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return None

    value = str(value)
    value = re.sub(r"\s+", " ", value).strip()

    return value if value else None


def safe_filename(value):
    value = clean_text(value) or "unknown_school"

    value = re.sub(
        r'[<>:"/\\|?*]',
        "",
        value
    )

    value = re.sub(
        r"\s+",
        "_",
        value
    )

    return value[:150]


def save_json(filepath, data):
    with open(
        filepath,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )


def create_driver():

    options = webdriver.ChromeOptions()

    options.add_argument("--start-maximized")

    # Keep browser visible for first testing.
    # Later you can enable headless:
    #
    # options.add_argument("--headless=new")

    options.add_argument(
        "--disable-blink-features=AutomationControlled"
    )

    options.add_argument(
        "--disable-notifications"
    )

    driver = webdriver.Chrome(
        options=options
    )

    driver.set_page_load_timeout(60)

    return driver


# ============================================================
# DETAIL PAGE SCRAPER
# ============================================================

def scrape_detail_page(driver, detail_url):

    driver.get(detail_url)

    WebDriverWait(
        driver,
        30
    ).until(
        EC.presence_of_element_located(
            (By.TAG_NAME, "body")
        )
    )

    soup = BeautifulSoup(
        driver.page_source,
        "html.parser"
    )

    details = {}

    # --------------------------------------------------------
    # Most SARAS school detail pages use table rows:
    #
    # <tr>
    #    <td>Field</td>
    #    <td>Value</td>
    # </tr>
    #
    # We collect every field automatically.
    # --------------------------------------------------------

    rows = soup.find_all("tr")

    for row in rows:

        cells = row.find_all(
            ["td", "th"]
        )

        if len(cells) < 2:
            continue

        key = clean_text(
            cells[0].get_text(
                " ",
                strip=True
            )
        )

        value = clean_text(
            cells[1].get_text(
                " ",
                strip=True
            )
        )

        if not key:
            continue

        # Avoid useless repeated headings
        if key.lower() in [
            "s no",
            "details"
        ]:
            continue

        details[key] = value

    # --------------------------------------------------------
    # Capture website links separately if present
    # --------------------------------------------------------

    links = []

    for a in soup.find_all(
        "a",
        href=True
    ):

        href = a.get("href")

        if not href:
            continue

        links.append({
            "text": clean_text(
                a.get_text(
                    " ",
                    strip=True
                )
            ),
            "url": href
        })

    return {
        "fields": details,
        "links": links,
        "source_url": detail_url
    }


# ============================================================
# LISTING PAGE PARSER
# ============================================================

def parse_listing_rows(driver):

    soup = BeautifulSoup(
        driver.page_source,
        "html.parser"
    )

    schools = []

    tables = soup.find_all("table")

    if not tables:
        return schools

    # Look through every table because SARAS may change IDs
    for table in tables:

        rows = table.find_all("tr")

        for row in rows:

            cells = row.find_all("td")

            if len(cells) < 6:
                continue

            row_text = clean_text(
                row.get_text(
                    " ",
                    strip=True
                )
            )

            if not row_text:
                continue

            # Find AfflicationDetails link
            detail_link = None
            affiliation_number = None

            for a in row.find_all(
                "a",
                href=True
            ):

                href = a.get("href", "")

                if "AfflicationDetails" in href:

                    if href.startswith("http"):
                        detail_link = href

                    else:
                        detail_link = (
                            BASE_URL + href
                            if href.startswith("/")
                            else BASE_URL + "/" + href
                        )

                    match = re.search(
                        r"AfflicationDetails/(\d+)",
                        href
                    )

                    if match:
                        affiliation_number = (
                            match.group(1)
                        )

                    break

            if not detail_link:
                continue

            cell_values = [
                clean_text(
                    cell.get_text(
                        " ",
                        strip=True
                    )
                )
                for cell in cells
            ]

            school = {
                "affiliation_number":
                    affiliation_number,

                "listing_raw_columns":
                    cell_values,

                "detail_url":
                    detail_link
            }

            # ---------------------------------------------
            # Extract useful listing fields
            # based on the columns shown in your screenshot
            # ---------------------------------------------

            if len(cell_values) >= 7:

                school[
                    "serial_number"
                ] = cell_values[0]

                school[
                    "affiliation_and_school_code"
                ] = cell_values[1]

                school[
                    "state_and_district"
                ] = cell_values[2]

                school[
                    "status"
                ] = cell_values[3]

                school[
                    "school_and_head"
                ] = cell_values[4]

                school[
                    "address_and_website"
                ] = cell_values[5]

            schools.append(
                school
            )

    # Remove duplicate affiliation numbers
    unique = {}

    for school in schools:

        key = (
            school.get(
                "affiliation_number"
            )
            or school.get(
                "detail_url"
            )
        )

        unique[key] = school

    return list(
        unique.values()
    )


# ============================================================
# PAGE NAVIGATION
# ============================================================

def configure_chandigarh_search(driver):

    driver.get(LIST_URL)

    WebDriverWait(
        driver,
        30
    ).until(
        EC.presence_of_element_located(
            (By.TAG_NAME, "body")
        )
    )

    time.sleep(2)

    # --------------------------------------------------------
    # Find State Wise radio button
    # --------------------------------------------------------

    radio_buttons = driver.find_elements(
        By.CSS_SELECTOR,
        "input[type='radio']"
    )

    for radio in radio_buttons:

        try:

            value = (
                radio.get_attribute("value")
                or ""
            ).lower()

            parent_text = (
                radio.find_element(
                    By.XPATH,
                    "./.."
                ).text
                or ""
            ).lower()

            if (
                "state" in value
                or "state wise" in parent_text
            ):

                driver.execute_script(
                    "arguments[0].click();",
                    radio
                )

                break

        except Exception:
            continue

    time.sleep(1)

    # --------------------------------------------------------
    # Find dropdowns
    # --------------------------------------------------------

    selects = driver.find_elements(
        By.TAG_NAME,
        "select"
    )

    state_select = None
    district_select = None

    for element in selects:

        try:

            select = Select(element)

            options = [
                clean_text(x.text)
                for x in select.options
            ]

            upper_options = [
                str(x).upper()
                for x in options
            ]

            if (
                STATE_NAME
                in upper_options
                and state_select is None
            ):
                state_select = select
                continue

        except Exception:
            continue

    if state_select is None:
        raise RuntimeError(
            "State dropdown not found."
        )

    state_select.select_by_visible_text(
        STATE_NAME
    )

    print(
        f"Selected State: {STATE_NAME}"
    )

    time.sleep(2)

    # Search again because district options may load dynamically

    selects = driver.find_elements(
        By.TAG_NAME,
        "select"
    )

    for element in selects:

        try:

            select = Select(element)

            options = [
                clean_text(x.text)
                for x in select.options
            ]

            upper_options = [
                str(x).upper()
                for x in options
            ]

            if DISTRICT_NAME in upper_options:

                if (
                    select._el
                    != state_select._el
                ):
                    district_select = select
                    break

        except Exception:
            continue

    if district_select:

        district_select.select_by_visible_text(
            DISTRICT_NAME
        )

        print(
            f"Selected District: {DISTRICT_NAME}"
        )

    else:

        print(
            "District dropdown was not detected automatically."
        )

    # --------------------------------------------------------
    # Click SEARCH
    # --------------------------------------------------------

    search_buttons = driver.find_elements(
        By.XPATH,
        "//button[contains("
        "translate(., 'abcdefghijklmnopqrstuvwxyz', "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), 'SEARCH')]"
    )

    if not search_buttons:

        search_buttons = driver.find_elements(
            By.XPATH,
            "//input[@type='submit']"
        )

    if not search_buttons:
        raise RuntimeError(
            "SEARCH button not found."
        )

    driver.execute_script(
        "arguments[0].click();",
        search_buttons[0]
    )

    time.sleep(4)


# ============================================================
# HANDLE TABLE PAGINATION
# ============================================================

def collect_all_school_links(driver):

    collected = {}

    page_number = 1

    while True:

        time.sleep(1)

        page_schools = parse_listing_rows(
            driver
        )

        print(
            f"Page {page_number}: "
            f"{len(page_schools)} schools found"
        )

        for school in page_schools:

            key = (
                school.get(
                    "affiliation_number"
                )
                or school.get(
                    "detail_url"
                )
            )

            collected[key] = school

        # ----------------------------------------------------
        # Find DataTables "Next" button
        # ----------------------------------------------------

        next_buttons = (
            driver.find_elements(
                By.CSS_SELECTOR,
                ".paginate_button.next"
            )
        )

        if not next_buttons:

            next_buttons = (
                driver.find_elements(
                    By.XPATH,
                    "//*[contains("
                    "translate(., "
                    "'abcdefghijklmnopqrstuvwxyz', "
                    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
                    "'NEXT')]"
                )
            )

        if not next_buttons:
            break

        next_button = next_buttons[-1]

        classes = (
            next_button.get_attribute(
                "class"
            )
            or ""
        )

        if "disabled" in classes.lower():
            break

        try:

            driver.execute_script(
                "arguments[0].click();",
                next_button
            )

            page_number += 1

            time.sleep(1.5)

        except Exception:
            break

    return list(
        collected.values()
    )


# ============================================================
# MAIN SCRAPER
# ============================================================

def main():

    driver = create_driver()

    scraped_records = []
    failed_records = []

    try:

        print(
            "\n=========================================="
        )

        print(
            "CBSE SARAS CHANDIGARH SCRAPER"
        )

        print(
            "==========================================\n"
        )

        configure_chandigarh_search(
            driver
        )

        print(
            "\nCollecting all school View links..."
        )

        schools = collect_all_school_links(
            driver
        )

        print(
            f"\nTotal schools discovered: "
            f"{len(schools)}"
        )

        save_json(
            ALL_SCHOOLS_FILE,
            schools
        )

        # ----------------------------------------------------
        # Visit every detail page
        # ----------------------------------------------------

        total = len(schools)

        for index, school in enumerate(
            schools,
            start=1
        ):

            affiliation_number = (
                school.get(
                    "affiliation_number"
                )
            )

            detail_url = (
                school.get(
                    "detail_url"
                )
            )

            print(
                f"\n[{index}/{total}] "
                f"Affiliation: "
                f"{affiliation_number}"
            )

            if not detail_url:
                continue

            # Filename initially based on affiliation number
            temp_filename = (
                f"{affiliation_number}.json"
            )

            filepath = (
                SCHOOL_DIR
                / temp_filename
            )

            # Resume support
            if filepath.exists():

                print(
                    "Already exists - SKIPPED"
                )

                continue

            try:

                detail = scrape_detail_page(
                    driver,
                    detail_url
                )

                fields = detail.get(
                    "fields",
                    {}
                )

                school_name = (
                    fields.get(
                        "Name of Institution"
                    )
                    or fields.get(
                        "Name Of Institution"
                    )
                    or fields.get(
                        "Name of School"
                    )
                    or affiliation_number
                )

                record = {
                    "metadata": {
                        "source":
                            "CBSE SARAS",

                        "source_type":
                            "Official CBSE "
                            "Affiliation Directory",

                        "state":
                            STATE_NAME,

                        "district":
                            DISTRICT_NAME,

                        "affiliation_number":
                            affiliation_number,

                        "detail_url":
                            detail_url
                    },

                    "listing_data":
                        school,

                    "school_details":
                        fields,

                    "links":
                        detail.get(
                            "links",
                            []
                        )
                }

                final_filename = (
                    safe_filename(
                        school_name
                    )
                    + "_"
                    + str(
                        affiliation_number
                    )
                    + ".json"
                )

                final_path = (
                    SCHOOL_DIR
                    / final_filename
                )

                save_json(
                    final_path,
                    record
                )

                scraped_records.append(
                    record
                )

                print(
                    f"Saved: {final_filename}"
                )

            except Exception as error:

                print(
                    f"FAILED: {error}"
                )

                failed_records.append({
                    "affiliation_number":
                        affiliation_number,

                    "detail_url":
                        detail_url,

                    "error":
                        str(error)
                })

            time.sleep(
                REQUEST_DELAY
            )

        # ----------------------------------------------------
        # Save summary
        # ----------------------------------------------------

        summary = {

            "source":
                "CBSE SARAS",

            "location":
                "Chandigarh",

            "total_schools_found":
                len(schools),

            "successfully_scraped":
                len(scraped_records),

            "failed":
                len(failed_records),

            "output_folder":
                str(SCHOOL_DIR)
        }

        save_json(
            SUMMARY_FILE,
            summary
        )

        if failed_records:

            save_json(
                ERROR_DIR
                / "failed_schools.json",

                failed_records
            )

        print(
            "\n=========================================="
        )

        print(
            "SCRAPING COMPLETED"
        )

        print(
            "=========================================="
        )

        print(
            f"Schools found : {len(schools)}"
        )

        print(
            f"Successful    : "
            f"{len(scraped_records)}"
        )

        print(
            f"Failed        : "
            f"{len(failed_records)}"
        )

        print(
            f"Output        : "
            f"{SCHOOL_DIR}"
        )

        print(
            "=========================================="
        )

    finally:

        driver.quit()


if __name__ == "__main__":
    main()