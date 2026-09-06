import json
import re
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# ============================================================
# CONFIGURATION - DELHI ONLY
# ============================================================

BASE_URL = "https://saras.cbse.gov.in"
LIST_URL = (
    "https://saras.cbse.gov.in/"
    "SARAS/AffiliatedList/ListOfSchdirReport"
)

STATE_NAME = "DELHI"
DISTRICT_NAME = None

OUTPUT_DIR = Path("data/CBSE_SARAS/delhi")
SCHOOL_DIR = OUTPUT_DIR / "schools"
ERROR_DIR = OUTPUT_DIR / "_errors"

SUMMARY_FILE = OUTPUT_DIR / "_summary.json"
ALL_SCHOOLS_FILE = OUTPUT_DIR / "_all_schools.json"
FAILED_FILE = ERROR_DIR / "failed_schools.json"

REQUEST_DELAY = 1.0
PAGE_WAIT = 1.5
TIMEOUT = 40

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
SCHOOL_DIR.mkdir(parents=True, exist_ok=True)
ERROR_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value if value else None


def safe_filename(value):
    value = clean_text(value) or "unknown_school"
    value = re.sub(r'[<>:"/\\|?*]', "", value)
    value = re.sub(r"\s+", "_", value)
    return value[:150]


def save_json(filepath, data):
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def load_json(filepath, default):
    if not filepath.exists():
        return default
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def create_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    return driver


# ============================================================
# RESUME / SKIP EXISTING FILES
# ============================================================

def extract_affiliation_from_filename(filename):
    match = re.search(r"_(\d+)\.json$", filename)
    return match.group(1) if match else None


def load_existing_affiliation_numbers():
    existing = set()
    unreadable = 0

    for filepath in SCHOOL_DIR.glob("*.json"):
        affiliation_number = None

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            affiliation_number = (
                data.get("metadata", {})
                .get("affiliation_number")
            )
        except Exception:
            unreadable += 1

        if not affiliation_number:
            affiliation_number = extract_affiliation_from_filename(
                filepath.name
            )

        if affiliation_number:
            existing.add(str(affiliation_number).strip())

    print(f"Existing school JSON files detected : {len(existing)}")

    if unreadable:
        print(
            f"Warning: {unreadable} existing JSON file(s) "
            "could not be read completely."
        )

    return existing


# ============================================================
# DETAIL PAGE SCRAPER
# ============================================================

def scrape_detail_page(driver, detail_url):
    driver.get(detail_url)

    WebDriverWait(driver, TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    soup = BeautifulSoup(driver.page_source, "html.parser")
    details = {}

    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])

        if len(cells) < 2:
            continue

        key = clean_text(cells[0].get_text(" ", strip=True))
        value = clean_text(cells[1].get_text(" ", strip=True))

        if not key:
            continue

        if key.lower() in {"s no", "s.no", "s. no.", "details"}:
            continue

        if key in details:
            if isinstance(details[key], list):
                details[key].append(value)
            else:
                details[key] = [details[key], value]
        else:
            details[key] = value

    links = []
    seen_links = set()

    for anchor in soup.find_all("a", href=True):
        href = clean_text(anchor.get("href"))
        if not href:
            continue

        if href.startswith("/"):
            href = BASE_URL + href

        text = clean_text(anchor.get_text(" ", strip=True))
        link_key = (href, text)

        if link_key in seen_links:
            continue

        seen_links.add(link_key)
        links.append({"text": text, "url": href})

    return {
        "fields": details,
        "links": links,
        "source_url": detail_url
    }


# ============================================================
# LISTING PAGE PARSER
# ============================================================

def build_absolute_url(href):
    href = clean_text(href)

    if not href:
        return None

    if href.startswith(("http://", "https://")):
        return href

    if href.startswith("/"):
        return BASE_URL + href

    return BASE_URL + "/" + href


def parse_listing_rows(driver):
    soup = BeautifulSoup(driver.page_source, "html.parser")
    schools = []

    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")

            if len(cells) < 6:
                continue

            detail_link = None
            affiliation_number = None

            for anchor in row.find_all("a", href=True):
                href = anchor.get("href", "")

                if "AfflicationDetails" not in href:
                    continue

                detail_link = build_absolute_url(href)

                match = re.search(
                    r"AfflicationDetails/(\d+)",
                    href
                )
                if match:
                    affiliation_number = match.group(1)

                break

            if not detail_link:
                continue

            cell_values = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in cells
            ]

            school = {
                "affiliation_number": affiliation_number,
                "listing_raw_columns": cell_values,
                "detail_url": detail_link
            }

            if len(cell_values) >= 6:
                school["serial_number"] = cell_values[0]
                school["affiliation_and_school_code"] = cell_values[1]
                school["state_and_district"] = cell_values[2]
                school["status"] = cell_values[3]
                school["school_and_head"] = cell_values[4]
                school["address_and_website"] = cell_values[5]

            schools.append(school)

    unique = {}

    for school in schools:
        key = (
            school.get("affiliation_number")
            or school.get("detail_url")
        )
        unique[key] = school

    return list(unique.values())


# ============================================================
# DELHI SEARCH
# ============================================================

def click_state_wise_radio(driver):
    radios = driver.find_elements(
        By.CSS_SELECTOR,
        "input[type='radio']"
    )

    for radio in radios:
        try:
            value = (radio.get_attribute("value") or "").lower()

            nearby_text = ""
            try:
                nearby_text = (
                    radio.find_element(By.XPATH, "./..").text
                    or ""
                ).lower()
            except Exception:
                pass

            if (
                "state" in value
                or "state wise" in nearby_text
                or "statewise" in nearby_text
            ):
                driver.execute_script(
                    "arguments[0].click();",
                    radio
                )
                return True

        except Exception:
            continue

    return False


def find_select_containing_option(driver, option_text):
    target = option_text.upper()

    for element in driver.find_elements(By.TAG_NAME, "select"):
        try:
            select = Select(element)

            options_upper = [
                (clean_text(option.text) or "").upper()
                for option in select.options
            ]

            if target in options_upper:
                return select

        except Exception:
            continue

    return None


def click_search_button(driver):
    candidates = []

    candidates.extend(
        driver.find_elements(
            By.XPATH,
            "//button[contains("
            "translate(normalize-space(.), "
            "'abcdefghijklmnopqrstuvwxyz', "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
            "'SEARCH')]"
        )
    )

    candidates.extend(
        driver.find_elements(
            By.XPATH,
            "//input[@type='submit']"
        )
    )

    candidates.extend(
        driver.find_elements(
            By.XPATH,
            "//input[contains("
            "translate(@value, "
            "'abcdefghijklmnopqrstuvwxyz', "
            "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
            "'SEARCH')]"
        )
    )

    for button in candidates:
        try:
            if button.is_displayed() and button.is_enabled():
                driver.execute_script(
                    "arguments[0].click();",
                    button
                )
                return
        except Exception:
            continue

    raise RuntimeError("SARAS Search button was not found.")


def configure_delhi_search(driver):
    driver.get(LIST_URL)

    WebDriverWait(driver, TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )

    time.sleep(2)

    if not click_state_wise_radio(driver):
        print(
            "Warning: State Wise radio button was not "
            "identified automatically."
        )

    time.sleep(1)

    state_select = find_select_containing_option(
        driver,
        STATE_NAME
    )

    if state_select is None:
        raise RuntimeError(
            f"State dropdown containing {STATE_NAME} "
            "was not found."
        )

    state_select.select_by_visible_text(STATE_NAME)
    print(f"Selected State : {STATE_NAME}")

    time.sleep(2)

    if DISTRICT_NAME:
        district_select = find_select_containing_option(
            driver,
            DISTRICT_NAME
        )

        if district_select:
            district_select.select_by_visible_text(
                DISTRICT_NAME
            )
            print(f"Selected District : {DISTRICT_NAME}")
    else:
        print("District filter : NONE / ALL DELHI")

    click_search_button(driver)

    time.sleep(5)

    WebDriverWait(driver, TIMEOUT).until(
        EC.presence_of_element_located((By.TAG_NAME, "table"))
    )


# ============================================================
# PAGINATION
# ============================================================

def collect_all_school_links(driver):

    collected = {}
    page_number = 1

    while True:

        time.sleep(1.5)

        page_schools = parse_listing_rows(driver)

        print(
            f"Page {page_number}: "
            f"{len(page_schools)} school(s) found"
        )

        for school in page_schools:

            key = (
                school.get("affiliation_number")
                or school.get("detail_url")
            )

            collected[key] = school

        # ----------------------------------------------------
        # Try several possible Next button selectors
        # ----------------------------------------------------

        next_candidates = []

        selectors = [
            (By.ID, "example_next"),
            (By.ID, "DataTables_Table_0_next"),
            (By.CSS_SELECTOR, "li.paginate_button.next"),
            (By.CSS_SELECTOR, "a.paginate_button.next"),
            (By.CSS_SELECTOR, ".pagination .next"),
            (
                By.XPATH,
                "//a[contains("
                "translate(normalize-space(.), "
                "'abcdefghijklmnopqrstuvwxyz', "
                "'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), "
                "'NEXT')]"
            ),
            (
                By.XPATH,
                "//li[contains(@class,'next')]//a"
            )
        ]

        for by, selector in selectors:

            try:

                found = driver.find_elements(
                    by,
                    selector
                )

                for element in found:

                    if element not in next_candidates:
                        next_candidates.append(
                            element
                        )

            except Exception:
                continue

        if not next_candidates:

            print(
                "No Next button found. "
                "Pagination finished."
            )

            break

        next_button = None

        for candidate in next_candidates:

            try:

                classes = (
                    candidate.get_attribute(
                        "class"
                    )
                    or ""
                ).lower()

                parent_classes = ""

                try:

                    parent_classes = (
                        candidate.find_element(
                            By.XPATH,
                            ".."
                        ).get_attribute(
                            "class"
                        )
                        or ""
                    ).lower()

                except Exception:
                    pass

                aria_disabled = (
                    candidate.get_attribute(
                        "aria-disabled"
                    )
                    or ""
                ).lower()

                if (
                    "disabled" in classes
                    or "disabled" in parent_classes
                    or aria_disabled == "true"
                ):
                    continue

                if (
                    candidate.is_displayed()
                    and candidate.is_enabled()
                ):
                    next_button = candidate
                    break

            except Exception:
                continue

        if next_button is None:

            print(
                "Next button is disabled. "
                "Reached last page."
            )

            break

        # ----------------------------------------------------
        # Remember current page before clicking Next
        # ----------------------------------------------------

        old_first_affiliation = None

        if page_schools:

            old_first_affiliation = (
                page_schools[0].get(
                    "affiliation_number"
                )
            )

        try:

            driver.execute_script(
                "arguments[0].scrollIntoView("
                "{block: 'center'});",
                next_button
            )

            time.sleep(0.5)

            driver.execute_script(
                "arguments[0].click();",
                next_button
            )

        except Exception as error:

            print(
                f"Could not click Next: {error}"
            )

            break

        # ----------------------------------------------------
        # Wait until table actually changes
        # ----------------------------------------------------

        changed = False

        for _ in range(20):

            time.sleep(0.5)

            new_page_schools = (
                parse_listing_rows(
                    driver
                )
            )

            if not new_page_schools:
                continue

            new_first_affiliation = (
                new_page_schools[0].get(
                    "affiliation_number"
                )
            )

            if (
                new_first_affiliation
                != old_first_affiliation
            ):

                changed = True
                break

        if not changed:

            print(
                "Next button clicked, "
                "but table did not change."
            )

            break

        page_number += 1

    print(
        f"\nTotal unique school links collected: "
        f"{len(collected)}"
    )

    return list(
        collected.values()
    )

# ============================================================
# FAILURE MANAGEMENT
# ============================================================

def save_failed_records(failed_records):
    if failed_records:
        save_json(FAILED_FILE, failed_records)
    elif FAILED_FILE.exists():
        try:
            FAILED_FILE.unlink()
        except Exception:
            pass


# ============================================================
# MAIN
# ============================================================

def main():
    driver = create_driver()

    newly_scraped = 0
    already_existing = 0
    failed_records = []

    try:
        print(
            "\n============================================================"
        )
        print("CBSE SARAS - DELHI SCHOOL SCRAPER")
        print(
            "============================================================"
        )

        existing_affiliations = (
            load_existing_affiliation_numbers()
        )

        configure_delhi_search(driver)

        print("\nCollecting all Delhi school View links...")

        schools = collect_all_school_links(driver)
        total = len(schools)

        print(
            "\n============================================================"
        )
        print(f"Total Delhi schools discovered : {total}")
        print(
            f"Already saved before this run  : "
            f"{len(existing_affiliations)}"
        )
        print(
            "============================================================\n"
        )

        save_json(ALL_SCHOOLS_FILE, schools)

        for index, school in enumerate(
            schools,
            start=1
        ):
            affiliation_number = clean_text(
                school.get("affiliation_number")
            )

            detail_url = school.get("detail_url")

            if not affiliation_number:
                print(
                    f"[{index}/{total}] "
                    "Missing affiliation number - FAILED"
                )

                failed_records.append({
                    "affiliation_number": None,
                    "detail_url": detail_url,
                    "error": "Missing affiliation number"
                })
                continue

            if affiliation_number in existing_affiliations:
                already_existing += 1

                print(
                    f"[{index}/{total}] "
                    f"{affiliation_number} "
                    "- Already exists - SKIPPED"
                )
                continue

            print(
                f"\n[{index}/{total}] "
                f"Scraping affiliation: "
                f"{affiliation_number}"
            )

            if not detail_url:
                failed_records.append({
                    "affiliation_number":
                        affiliation_number,
                    "detail_url":
                        None,
                    "error":
                        "Detail URL missing"
                })

                print("FAILED: Detail URL missing")
                continue

            try:
                detail = scrape_detail_page(
                    driver,
                    detail_url
                )

                fields = detail.get("fields", {})

                school_name = (
                    fields.get("Name of Institution")
                    or fields.get("Name Of Institution")
                    or fields.get("Name of School")
                    or affiliation_number
                )

                record = {
                    "metadata": {
                        "source":
                            "CBSE SARAS",
                        "source_type":
                            "Official CBSE Affiliation Directory",
                        "location":
                            "Delhi",
                        "state":
                            STATE_NAME,
                        "district_filter":
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
                        detail.get("links", [])
                }

                final_filename = (
                    safe_filename(school_name)
                    + "_"
                    + affiliation_number
                    + ".json"
                )

                final_path = SCHOOL_DIR / final_filename

                if final_path.exists():
                    already_existing += 1
                    existing_affiliations.add(
                        affiliation_number
                    )
                    print(
                        "Final JSON already exists - SKIPPED"
                    )
                    continue

                save_json(final_path, record)

                existing_affiliations.add(
                    affiliation_number
                )

                newly_scraped += 1

                print(f"Saved: {final_filename}")

            except (TimeoutException, WebDriverException) as error:
                failed_records.append({
                    "affiliation_number":
                        affiliation_number,
                    "detail_url":
                        detail_url,
                    "error":
                        str(error)
                })

                print(f"FAILED: {error}")

            except Exception as error:
                failed_records.append({
                    "affiliation_number":
                        affiliation_number,
                    "detail_url":
                        detail_url,
                    "error":
                        str(error)
                })

                print(f"FAILED: {error}")

            if failed_records:
                save_failed_records(failed_records)

            time.sleep(REQUEST_DELAY)

        final_existing = (
            load_existing_affiliation_numbers()
        )

        final_file_count = len(
            list(SCHOOL_DIR.glob("*.json"))
        )

        summary = {
            "source":
                "CBSE SARAS",
            "location":
                "Delhi",
            "state":
                STATE_NAME,
            "district_filter":
                "ALL / NONE",
            "total_schools_discovered":
                total,
            "already_existing_skipped":
                already_existing,
            "newly_scraped":
                newly_scraped,
            "failed_this_run":
                len(failed_records),
            "completed_unique_affiliations":
                len(final_existing),
            "school_json_files":
                final_file_count,
            "output_folder":
                str(SCHOOL_DIR)
        }

        save_json(SUMMARY_FILE, summary)
        save_failed_records(failed_records)

        print(
            "\n============================================================"
        )
        print("DELHI SARAS SCRAPING COMPLETED")
        print(
            "============================================================"
        )
        print(f"Schools discovered       : {total}")
        print(
            f"Already existed/skipped : "
            f"{already_existing}"
        )
        print(
            f"Newly scraped           : "
            f"{newly_scraped}"
        )
        print(
            f"Failed this run          : "
            f"{len(failed_records)}"
        )
        print(
            f"Final school JSON files : "
            f"{final_file_count}"
        )
        print(
            f"Output folder           : "
            f"{SCHOOL_DIR}"
        )
        print(
            "============================================================"
        )

    except KeyboardInterrupt:
        print("\n\nScraper stopped with Ctrl+C.")
        print("Completed JSON files are safe.")
        print(
            "Run the same command again. Existing "
            "affiliation numbers will be skipped."
        )
        save_failed_records(failed_records)

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
