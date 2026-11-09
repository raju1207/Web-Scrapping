"""Dynamic SARAS directory collector, reusing the legacy HTML record format.

State and district identifiers are read from the official form, never guessed.
Pincodes are compared only with the detail page's dedicated Pin Code field.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from school_scraping.common import (
    ManualIntervention, RawStore, SourceLock, check_access, parse_values,
    pin, safe_filename, write_report,
)
from school_scraping.config import add_arguments, resolve_source_args, ConfigurationError
from school_scraping.incremental import IncrementalRuntime, sync_pending

PROJECT_DIR = Path(__file__).resolve().parent
BASE_URL = "https://saras.cbse.gov.in"
LIST_URL = BASE_URL + "/SARAS/AffiliatedList/ListOfSchdirReport"


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    return re.sub(r"\s+", " ", str(value)).strip() or None


def geography_key(value: str) -> str:
    value = re.sub(r"\band\b", "", value.casefold().replace("&", " and "))
    key = re.sub(r"[^a-z0-9]", "", value)
    # These are spelling aliases only; identifiers always come from live options.
    return {"chhattisgarh": "chattisgarh", "tamilnadu": "tamilnadu",
            "andamannicobarislands": "andamannicobar",
            "andamannicobarisls": "andamannicobar",
            "dadarnagarhaveli": "dadranagarhaveli"}.get(key, key)


def parse_listing_rows(html: str) -> list[dict[str, Any]]:
    """Preserve the columns and field names used by both legacy scrapers."""
    soup = BeautifulSoup(html, "html.parser")
    schools = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 6:
            continue
        anchor = row.find("a", href=re.compile(r"AfflicationDetails/\d+", re.I))
        if anchor is None:
            continue
        affiliation = re.search(r"AfflicationDetails/(\d+)", anchor["href"], re.I)
        values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        school = {"affiliation_number": affiliation.group(1),
                  "listing_raw_columns": values,
                  "detail_url": urljoin(BASE_URL, anchor["href"])}
        for field, value in zip(
            ("serial_number", "affiliation_and_school_code", "state_and_district",
             "status", "school_and_head", "address_and_website"), values
        ):
            school[field] = value
        schools.append(school)
    return schools


def parse_detail_page(html: str, detail_url: str) -> dict[str, Any]:
    """Keep every source table label and its value, including repeated labels."""
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, Any] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue
        key = clean_text(cells[0].get_text(" ", strip=True))
        value = clean_text(cells[1].get_text(" ", strip=True))
        if not key or key.lower() in {"s no", "s.no", "s. no.", "details"}:
            continue
        if key in fields:
            fields[key] = fields[key] + [value] if isinstance(fields[key], list) else [fields[key], value]
        else:
            fields[key] = value
    links, seen = [], set()
    for anchor in soup.find_all("a", href=True):
        href = clean_text(anchor.get("href"))
        if not href:
            continue
        href = urljoin(detail_url, href)
        text = clean_text(anchor.get_text(" ", strip=True))
        if (href, text) not in seen:
            seen.add((href, text))
            links.append({"text": text, "url": href})
    return {"fields": fields, "links": links, "source_url": detail_url}


def detail_value(fields: dict[str, Any], *labels: str) -> Any:
    wanted = {re.sub(r"[^a-z0-9]", "", label.casefold()) for label in labels}
    for label, value in fields.items():
        if re.sub(r"[^a-z0-9]", "", label.casefold()) in wanted:
            return value
    return None


def verified_pincode(fields: dict[str, Any]) -> str | None:
    value = detail_value(fields, "Pin Code", "Pincode", "Postal Code")
    if isinstance(value, list):
        values = {pin(item) for item in value if pin(item)}
        return values.pop() if len(values) == 1 else None
    return pin(value)


def validate_detail(fields: dict[str, Any], affiliation: str) -> None:
    actual = clean_text(detail_value(fields, "Affiliation Number", "Affiliation No"))
    if actual and actual != affiliation:
        raise ValueError("Detail affiliation does not match the requested school")
    name = detail_value(fields, "Name of Institution", "Name of School")
    if not actual or not name:
        raise ValueError("Response lacks a verified affiliation and institution name; it was not saved")


def build_record(school: dict[str, Any], detail: dict[str, Any], state: str,
                 district: str | None = None) -> dict[str, Any]:
    metadata = {
        "source": "CBSE SARAS", "source_type": "Official CBSE Affiliation Directory",
        "location": state.title(), "state": state, "district_filter": district,
        "selected_district": district, "affiliation_number": school["affiliation_number"],
        "detail_url": school["detail_url"],
    }
    return {"metadata": metadata, "listing_data": dict(school),
            "school_details": detail["fields"], "links": detail.get("links", [])}


class TransientFailure(RuntimeError):
    pass


class SarasBrowser:
    """Use the existing browser workflow with live dropdown and page discovery."""

    def __init__(self, page: Any, args: argparse.Namespace):
        self.page, self.args = page, args

    def guard(self, status: int = 200) -> None:
        def assert_access(response_status=200):
            text = self.page.locator("body").inner_text(timeout=self.args.timeout * 1000)
            check_access(text, response_status)
            widgets = self.page.locator(
                "input[id*='captcha' i], input[name*='captcha' i], "
                "iframe[src*='recaptcha' i], iframe[src*='hcaptcha' i], "
                "iframe[title*='captcha' i], img[src*='captcha' i], img[alt*='captcha' i]"
            )
            if any(widgets.nth(index).is_visible() for index in range(widgets.count())):
                raise ManualIntervention("A visible CAPTCHA control requires manual completion in the official website")
        try:
            assert_access(status)
        except ManualIntervention:
            if not self.args.manual_captcha or self.args.headless:
                raise
            print("SARAS requires manual access verification. Complete it in the open browser; "
                  f"waiting at most {self.args.manual_captcha:g} seconds. No automated solving is used.", flush=True)
            deadline = time.monotonic() + self.args.manual_captcha
            while time.monotonic() < deadline:
                time.sleep(min(2, max(0, deadline - time.monotonic())))
                try:
                    assert_access()
                    return
                except ManualIntervention:
                    continue
            raise ManualIntervention("Manual verification was not completed in time; rerun the same command after access is restored")

    def retry(self, operation: Callable[[], Any]) -> Any:
        for attempt in range(self.args.retries + 1):
            try:
                return operation()
            except ManualIntervention:
                raise
            except Exception as error:
                runtime = getattr(self.args, "_runtime", None)
                if runtime:
                    runtime.event("browser_request_failure", error_type=type(error).__name__, retry_count=attempt)
                transient = isinstance(error, (TimeoutError, TransientFailure)) or any(
                    marker in str(error).lower() for marker in ("timeout", "net::err", "connection reset")
                )
                if not transient or attempt == self.args.retries:
                    raise
                time.sleep(max(self.args.delay, min(30, 2 ** attempt)))

    def navigate(self, url: str) -> None:
        def attempt():
            time.sleep(self.args.delay)
            response = self.page.goto(url, wait_until="domcontentloaded", timeout=self.args.timeout * 1000)
            status = response.status if response else 200
            self.guard(status)
            if status >= 500 or status == 429:
                raise TransientFailure(f"SARAS returned HTTP {status}")
            if status >= 400:
                raise RuntimeError(f"SARAS returned HTTP {status}")
        self.retry(attempt)

    def options(self, selector: str) -> list[dict[str, str]]:
        return self.page.locator(selector + " option").evaluate_all(
            "options => options.filter(o => o.value && !/^[-\\s]*(select|all)/i.test(o.textContent))"
            ".map(o => ({value:o.value,text:o.textContent.trim()}))"
        )

    def open_form(self) -> None:
        self.navigate(LIST_URL)
        self.page.locator("input[type=radio][value=State_wise]").check()
        self.page.locator("select#State").wait_for(state="visible", timeout=self.args.timeout * 1000)

    def states(self) -> list[dict[str, str]]:
        self.open_form()
        states = self.options("select#State")
        if not states:
            raise RuntimeError("No states were supplied by the official SARAS form")
        return [state for state in states if "foreign" not in state["text"].casefold()]

    def configure_state(self, state: dict[str, str]) -> list[dict[str, str]]:
        return self.retry(lambda: self._configure_state(state))

    def _configure_state(self, state: dict[str, str]) -> list[dict[str, str]]:
        self.open_form()
        # Wait for the exact source request, not a placeholder left in the dropdown.
        def district_response(response):
            parsed = urlsplit(response.url)
            return (parsed.path.rstrip("/").endswith("/AffiliatedList/Dist_Bind")
                    and parse_qs(parsed.query).get("state_id") == [state["value"]])
        with self.page.expect_response(district_response, timeout=self.args.timeout * 1000) as pending:
            self.page.locator("select#State").select_option(value=state["value"])
        response = pending.value
        self.guard(response.status)
        if response.status >= 500 or response.status == 429:
            raise TransientFailure(f"SARAS district request returned HTTP {response.status}")
        if response.status >= 400:
            raise RuntimeError(f"SARAS district request returned HTTP {response.status}")
        expected = response.json()
        if not isinstance(expected, list) or not expected:
            raise RuntimeError("SARAS returned no district choices for the selected state")
        values = [str(item["value"]) for item in expected if isinstance(item, dict) and "value" in item]
        if not values:
            raise RuntimeError("SARAS district response has an unsupported structure")
        self.page.wait_for_function(
            "values => {const options = Array.from(document.querySelector('#District')?.options || []); "
            "return values.every(value => options.some(option => option.value === value));}",
            arg=values,
            timeout=self.args.timeout * 1000,
        )
        self.guard()
        return self.options("select#District")

    def search(self, state: dict[str, str], district: dict[str, str] | None) -> None:
        self.retry(lambda: self._search(state, district))

    def _search(self, state: dict[str, str], district: dict[str, str] | None) -> None:
        self.configure_state(state)
        if district:
            self.page.locator("select#District").select_option(value=district["value"])
        else:
            options = self.page.locator("select#District option").evaluate_all(
                "items => items.map(o => ({value:o.value,text:o.textContent.trim()}))"
            )
            placeholder = next((item for item in options if not item["value"] or
                                re.search(r"^[-\s]*(?:select|all)\b", item["text"], re.I)), None)
            if placeholder:
                self.page.locator("select#District").select_option(value=placeholder["value"])
            elif options:
                raise RuntimeError("The live district control has no all-district choice; select a district explicitly")
        with self.page.expect_navigation(wait_until="domcontentloaded", timeout=self.args.timeout * 1000) as pending:
            self.page.locator("input[type=submit][value=Search], button:has-text('Search')").first.click()
        response = pending.value
        self.guard(response.status if response else 200)
        if response and (response.status >= 500 or response.status == 429):
            raise TransientFailure(f"SARAS search returned HTTP {response.status}")
        if response and response.status >= 400:
            raise RuntimeError(f"SARAS search returned HTTP {response.status}")
        self.page.locator("table#myTable").wait_for(timeout=self.args.timeout * 1000)

    def collect(self, limit: int | None = None) -> list[dict[str, Any]]:
        schools, signatures, unique = [], set(), set()
        while True:
            self.guard()
            rows = parse_listing_rows(self.page.content())
            signature = tuple(row["affiliation_number"] for row in rows)
            if signature in signatures and rows:
                raise RuntimeError("SARAS pagination repeated a page; collection is incomplete")
            signatures.add(signature)
            for row in rows:
                schools.append(row)
                unique.add(row["affiliation_number"])
                if limit and len(unique) >= limit:
                    return schools
            candidates = self.page.locator(
                "#myTable_next, #example_next, #DataTables_Table_0_next, "
                ".paginate_button.next, .dt-paging-button.next, [aria-label='Next']"
            )
            next_button = None
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                disabled = candidate.evaluate(
                    "e => e.disabled || e.getAttribute('aria-disabled') === 'true' || "
                    "e.classList.contains('disabled') || e.parentElement?.classList.contains('disabled')"
                )
                if candidate.is_visible() and not disabled:
                    next_button = candidate
                    break
            if next_button is None:
                return schools
            if not rows:
                raise RuntimeError("SARAS has an active Next control but no readable school rows")
            time.sleep(self.args.delay)
            next_button.click()
            deadline = time.monotonic() + self.args.timeout
            while time.monotonic() < deadline:
                self.guard()
                current = parse_listing_rows(self.page.content())
                if current and tuple(row["affiliation_number"] for row in current) != signature:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("SARAS Next did not change the table; collection is incomplete")

    def detail(self, school: dict[str, Any]) -> dict[str, Any]:
        self.navigate(school["detail_url"])
        detail = parse_detail_page(self.page.content(), school["detail_url"])
        validate_detail(detail["fields"], school["affiliation_number"])
        return detail


def resolve_options(requested: list[str], options: list[dict[str, str]], kind: str) -> list[dict[str, str]]:
    resolved = []
    for name in requested:
        matches = [option for option in options if geography_key(option["text"]) == geography_key(name)]
        if len(matches) != 1:
            raise ValueError(f"{kind} '{name}' is not an unambiguous choice in the live SARAS dropdown")
        if matches[0] not in resolved:
            resolved.append(matches[0])
    return resolved


def requested_districts(values: list[str], state: str) -> list[str]:
    selected = []
    for value in values:
        if ":" in value:
            owner, name = value.split(":", 1)
            if geography_key(owner) != geography_key(state):
                continue
            selected.append(name.strip())
        else:
            selected.append(value)
    return selected


def output_path(data_dir: Path, state: str, affiliation: str, fields: dict[str, Any]) -> Path:
    folder = "Chandigarh" if geography_key(state) == "chandigarh" else safe_filename(state.lower())
    name = detail_value(fields, "Name of Institution", "Name of School") or affiliation
    if isinstance(name, list):
        name = next((value for value in name if value), affiliation)
    return data_dir / "CBSE_SARAS" / folder / "schools" / f"{safe_filename(str(name))}_{affiliation}.json"


def run(args: argparse.Namespace, browser: SarasBrowser, runtime=None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "source": "CBSE SARAS", "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": {"states": args.state, "all_states": args.all_states, "districts": args.district,
                  "pincodes": args.pincode, "limit": args.limit},
        "counts": dict.fromkeys(("discovered", "success", "updated", "unchanged", "skipped", "duplicate",
                                "filtered", "missing_pincode", "failed"), 0),
        "failures": [], "listing_data": [], "status": "running",
    }
    counts, seen = report["counts"], set()
    runtime = runtime or IncrementalRuntime(args, "saras")
    runtime.start()
    args._runtime = runtime
    store = RawStore("saras", args.data_dir, refresh=True)

    def collect_details(schools, state, district_name=None):
        counts["discovered"] += len(schools)
        report["listing_data"].extend(schools)
        for school in schools:
            identifier = school["affiliation_number"]
            if identifier in seen:
                counts["duplicate"] += 1
                runtime.event("duplicate_detected", identifier=identifier)
                continue
            if args.limit and len(seen) >= args.limit:
                break
            seen.add(identifier)
            report["active_task"] = {"state": state, "district": district_name,
                                     "affiliation_number": identifier,
                                     "detail_url": school["detail_url"], "phase": "detail"}
            try:
                runtime.discovered(identifier, school, state, district_name)
                cached = runtime.filtered_record(identifier) if args.pincode else None
                if cached:
                    code = verified_pincode(cached.get("school_details", {}))
                    if code not in args.pincode:
                        counts["filtered"] += 1
                        counts["missing_pincode"] += int(code is None)
                        runtime.completed(identifier, "filtered")
                        continue
                existing = store.existing(identifier)
                if not runtime.detail_due(identifier, existing, store.find(identifier)):
                    if not existing:
                        counts["deferred"] = counts.get("deferred", 0) + 1
                        continue
                    if args.pincode:
                        code = verified_pincode(existing.get("school_details", {}))
                        if code not in args.pincode:
                            counts["filtered"] += 1
                            counts["missing_pincode"] += int(code is None)
                            runtime.completed(identifier, "filtered")
                            continue
                    runtime.persist(identifier, existing, store.find(identifier), fetched=False)
                    counts["skipped"] += 1
                    runtime.event("detail_not_due", identifier=identifier)
                    continue
                runtime.attempting(identifier, school, state, district_name)
                detail = browser.detail(school)
                if args.pincode:
                    code = verified_pincode(detail["fields"])
                    if code not in args.pincode:
                        counts["filtered"] += 1
                        counts["missing_pincode"] += int(code is None)
                        runtime.filtered(identifier, build_record(school, detail, state, district_name), code)
                        continue
                if district_name:
                    school["saras_selected_district"] = district_name
                record = build_record(school, detail, state, district_name)
                result = store.save(identifier, record, output_path(args.data_dir, state, identifier, detail["fields"]))
                counts[result] += 1
                runtime.persist(identifier, record, store.find(identifier))
                runtime.event("record_" + result, identifier=identifier)
            except ManualIntervention as error:
                runtime.failed(identifier, error, school, state, district_name)
                raise
            except Exception as error:
                from school_scraping.memory import safe_error
                counts["failed"] += 1
                report["failures"].append({"affiliation_number": identifier,
                                           "detail_url": school["detail_url"], "error": safe_error(error)})
                runtime.failed(identifier, error, school, state, district_name)
    try:
        tasks = runtime.retry_rows()
        if tasks or getattr(args, "retry_failed", False):
            for task in tasks:
                if args.limit and len(seen) >= args.limit:
                    break
                runtime.event("failed_task_resumed", identifier=task["school_identifier"])
                collect_details([task["task_input"]], task.get("state") or (args.state[0] if args.state else "Unknown"), task.get("district"))
            if tasks or not any(not task.get("school_identifier") for task in runtime.memory.tasks("saras", scope_key=runtime.scope)):
                report["partial_recovery"] = True
                report["status"] = "partial_failure" if counts["failed"] else "completed"
                return report
        states = browser.states()
        selected = states if args.all_states else resolve_options(args.state, states, "State")
        report["resolved_states"] = selected
        selected_keys = {geography_key(item["text"]) for item in selected}
        for value in args.district:
            if ":" in value:
                owner, district_name = value.split(":", 1)
                if not district_name.strip() or geography_key(owner) not in selected_keys:
                    raise ValueError(f"District qualifier '{value}' does not identify a selected state and district")
        for state in selected:
            if args.limit and len(seen) >= args.limit:
                break
            try:
                report["active_task"] = {"state": state["text"], "phase": "district_discovery"}
                district_options = browser.configure_state(state)
                names = requested_districts(args.district, state["text"])
                if args.district and not names:
                    continue
                districts = resolve_options(names, district_options, "District") if names else [None]
                for district in districts:
                    if args.limit and len(seen) >= args.limit:
                        break
                    report["active_task"] = {"state": state["text"], "district": district, "phase": "listing"}
                    try:
                        browser.search(state, district)
                        remaining = args.limit - len(seen) if args.limit else None
                        schools = browser.collect(remaining)
                    except ManualIntervention:
                        raise
                    except Exception as error:
                        from school_scraping.memory import safe_error
                        counts["failed"] += 1
                        report["failures"].append({"state": state["text"], "district": district,
                                                   "error": safe_error(error)})
                        runtime.failed("", error, state=state["text"],
                                       district=district["text"] if district else None, phase="discovery")
                        continue
                    collect_details(schools, state["text"], district["text"] if district else None)
            except ManualIntervention:
                raise
            except Exception as error:
                from school_scraping.memory import safe_error
                counts["failed"] += 1
                report["failures"].append({"state": state["text"], "error": safe_error(error)})
                runtime.failed("", error, state=state["text"], phase="discovery")
        report["status"] = "partial_failure" if counts["failed"] else "completed"
        report.pop("active_task", None)
    except ManualIntervention as error:
        report["status"] = "manual_intervention"
        report["failures"].append({"task": report.get("active_task"), "error": str(error),
                                   "resume": "Restore authorized access and rerun the same command"})
        counts["failed"] += 1
        if report.get("active_task", {}).get("phase") != "detail":
            runtime.failed("", error, report.get("active_task"), phase="discovery")
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as error:
        from school_scraping.memory import safe_error
        report["status"] = "failed"
        counts["failed"] += 1
        report["failures"].append({"error": safe_error(error)})
        runtime.failed("", error, phase="discovery")
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        runtime.finish(report, scope_complete=(report["status"] == "completed" and args.limit is None
                       and not getattr(args, "retry_failed", False) and not counts.get("deferred")
                       and not report.get("partial_recovery")))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect CBSE SARAS schools using live state/district choices. Pincode is an exact post-detail filter.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--state", action="append", default=[], help="State name; repeat or comma-separate for multiple states")
    scope.add_argument("--all-states", action="store_true", help="Use every Indian state/UT choice from the official directory")
    parser.add_argument("--district", action="append", default=[], help="Official district name; repeat, optionally STATE:DISTRICT")
    parser.add_argument("--pincode", action="append", default=[], help="Six-digit Pin Code filter; repeat or comma-separate")
    parser.add_argument("--dry-run", action="store_true", help="Show scope offline without a browser, requests or file writes")
    parser.add_argument("--limit", type=int, help="Maximum unique listing candidates across the entire run")
    parser.add_argument("--delay", type=float, default=1.5, help="Minimum delay between navigations/pages in seconds (default 1.5)")
    parser.add_argument("--retries", type=int, default=2, help="Bounded retries for transient navigation failures (default 2)")
    parser.add_argument("--timeout", type=float, default=40, help="Browser operation timeout in seconds")
    parser.add_argument("--refresh", action="store_true", help="Fetch existing schools; preserve original raw records and version changed records")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_DIR / "data", help="Existing raw data root")
    parser.add_argument("--report-dir", type=Path, default=PROJECT_DIR / "reports" / "scraping", help="Versioned run report directory")
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser; manual verification then requires rerunning visibly")
    parser.add_argument("--storage-state", type=Path, help="Authorized Playwright browser-session JSON; never printed")
    parser.add_argument("--manual-captcha", type=float, default=0, metavar="SECONDS", help="Wait this many seconds for manual verification in the visible browser (default: stop immediately)")
    add_arguments(parser)
    return parser


def run_job(args: argparse.Namespace) -> int:
    parser = build_parser()
    args.state, args.district, args.pincode = (parse_values(values) for values in (args.state, args.district, args.pincode))
    if not args.all_states and not args.state:
        parser.error("At least one nonempty --state is required")
    if any(pin(value) != value for value in args.pincode):
        parser.error("Every --pincode must contain exactly six digits")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.delay < 0 or args.retries < 0 or args.timeout <= 0 or args.manual_captcha < 0:
        parser.error("Delay/retries/manual wait must be nonnegative and timeout must be positive")
    if args.dry_run:
        print(json.dumps({"source": "CBSE SARAS", "states": args.state, "all_states": args.all_states,
                          "districts": args.district, "pincodes": args.pincode, "limit": args.limit,
                          "pincode_mode": "Exact dedicated Pin Code detail-field postfilter; no direct pincode search",
                          "validation": "Offline scope only; state/district availability is checked against live dropdowns during collection"}, indent=2))
        return 0
    runtime = None
    try:
        from playwright.sync_api import sync_playwright
        with SourceLock(args.data_dir, "saras"):
            runtime = IncrementalRuntime(args, "saras")
            runtime.start()
            if not runtime.scope_due():
                report = {"source": "CBSE SARAS", "status": "not_due", "counts": {},
                          "scope_key": runtime.scope,
                          "reason": "Location checkpoint is not due; pending database writes were checked separately"}
                runtime.finish(report)
                path = write_report(args.report_dir, "saras", report)
                print(json.dumps({"status": report["status"], "report": str(path)}))
                return 0
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=args.headless)
                try:
                    context_args = {"storage_state": str(args.storage_state)} if args.storage_state else {}
                    context = browser.new_context(**context_args)
                    page = context.new_page()
                    report = run(args, SarasBrowser(page, args), runtime)
                finally:
                    browser.close()
            path = write_report(args.report_dir, "saras", report)
        print(json.dumps({"status": report["status"], "counts": report["counts"], "report": str(path)}, indent=2))
        if report["status"] == "manual_intervention":
            print("Manual intervention required. Restore authorized access and rerun the same command; completed files will be skipped.")
        return 0 if report["status"] == "completed" else 130 if report["status"] == "interrupted" else 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        failure = {"source": "CBSE SARAS", "status": "startup_failure",
                   "started_at": datetime.now(timezone.utc).isoformat(),
                   "scope": {"states": args.state, "all_states": args.all_states,
                             "districts": args.district, "pincodes": args.pincode},
                   "failures": [{"error_type": type(error).__name__,
                                 "error": "Browser/session initialization or source lock failed; inspect local installation and configuration"}]}
        try:
            if runtime:
                runtime.failed("", RuntimeError("Source startup failed; inspect browser/session configuration"), phase="discovery")
                runtime.finish(failure)
            path = write_report(args.report_dir, "saras", failure)
            print(f"SARAS could not start: {type(error).__name__}. Failure report: {path}")
        except OSError:
            print(f"SARAS could not start: {type(error).__name__}; the failure report directory is also unavailable.")
        print("Check browser/session configuration. Install the browser if needed with: python -m playwright install chromium")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        jobs = resolve_source_args(args, "saras", argv)
    except ConfigurationError as error:
        parser.error(str(error))
    if not jobs:
        print(json.dumps({"source": "CBSE SARAS", "status": "idle", "dry_run": args.dry_run,
                          "reason": "No enabled SARAS locations; enable locations in config or supply --state"}))
        return 0
    if args.sync_pending:
        if args.dry_run:
            print(json.dumps({"mode": "sync_pending", "dry_run": True, "database_enabled": jobs[0].database}))
            return 0
        return sync_pending(jobs[0])
    return max((run_job(job) for job in jobs), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
