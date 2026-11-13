"""Dynamic UDISE+ collection using existing raw contracts and official UI searches.

Discovery never submits a search without the website's manual CAPTCHA process.
Saved official search responses can be supplied for unattended/resumable runs.
"""
import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from school_scraping.common import (
    ManualIntervention, RawStore, SourceLock, check_access, normalize,
    parse_values, pin, read_json, safe_filename, source_pin, write_report,
)
from school_scraping.config import add_arguments, resolve_source_args, ConfigurationError
from school_scraping.incremental import IncrementalRuntime, sync_pending

ROOT = Path(__file__).resolve().parent
BASE_URL = "https://kys.udiseplus.gov.in/web-app/api"
ACADEMIC_YEAR = "2025-26"  # Existing project default; verified against master/year.
YEAR_ID = 12
REQUEST_DELAY = 0.5
DETAILS = {
    "school_profile": ("profile", "school/profile"),
    "student_teacher_statistics": ("student_teacher", "school-statistics/enrolment-teacher"),
    "infrastructure_facilities": ("facilities", "school/facility"),
    "report_card": ("report_card", "school/report-card"),
}


def get_value(data, *keys):
    return next((data[key] for key in keys if isinstance(data, dict)
                 and data.get(key) not in (None, "")), None)


def find_school_list(obj):
    """Reuse the legacy recursive response parser, including content wrappers."""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and any(
                key in obj[0] for key in ("udiseschCode", "udiseSchCode", "udiseCode")):
            return obj
        for item in obj:
            found = find_school_list(item)
            if found:
                return found
    elif isinstance(obj, dict):
        for key in ("content", "schools", "schoolList", "results", "data"):
            if key in obj:
                found = find_school_list(obj[key])
                if found:
                    return found
        for value in obj.values():
            found = find_school_list(value)
            if found:
                return found
    return []


def extract_api_data(result):
    if not isinstance(result, dict) or not result.get("success"):
        return None
    response = result.get("response")
    return response.get("data") if isinstance(response, dict) and response.get("status") is True else None


class UdiseClient:
    def __init__(self, base_url=BASE_URL, headers=None, delay=REQUEST_DELAY,
                 retries=3, timeout=60, on_event=None):
        self.base_url = base_url.rstrip("/")
        self.delay, self.retries, self.timeout = delay, retries, timeout
        self.on_event = on_event
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "SchoolDataCollector/1.0", "Accept": "application/json",
            "Referer": "https://kys.udiseplus.gov.in/", "Origin": "https://kys.udiseplus.gov.in",
        })
        if headers:
            self.session.headers.update(headers)

    def get_json(self, endpoint, params=None):
        url = f"{self.base_url}/{endpoint}"
        def event(name, **fields):
            if self.on_event:
                self.on_event(name, identifier=str((params or {}).get("udiseSchCode", "")), **fields)
        for attempt in range(self.retries + 1):
            time.sleep(self.delay * (2 ** attempt))
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    event("rate_limit_encountered", http_status=429)
                    raise ManualIntervention(
                        "UDISE+ returned HTTP 429 (rate limited). Collection stopped; "
                        "resume only after permitted access is restored. No automatic retry.")
                try:
                    check_access(response.text, response.status_code)
                except ManualIntervention:
                    event("authentication_failure" if response.status_code in (401, 403) else "captcha_encountered")
                    raise
                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    event("transient_http_failure", http_status=response.status_code, retry_count=attempt)
                    continue
                response.raise_for_status()
                data = response.json()
                # API error content is checked separately; no credentials/URLs in errors.
                if not isinstance(data, dict) or data.get("status") is not True:
                    check_access(json.dumps(data))
                    return {"success": False, "url": url, "error": "UDISE API returned an unsuccessful response", "response": data}
                # Keep original API JSON; credentials, CAPTCHA and query tokens are never logged.
                safe_params = {k: v for k, v in (params or {}).items()
                               if k in ("udiseSchCode", "schoolId", "yearId", "year", "stateId", "districtId")}
                prepared = requests.Request("GET", url, params=safe_params).prepare().url
                return {"success": True, "url": prepared, "response": data}
            except (requests.RequestException, ValueError) as error:
                event("network_timeout" if isinstance(error, requests.Timeout) else
                      "invalid_json_response" if isinstance(error, ValueError) else "request_failure",
                      error_type=type(error).__name__, retry_count=attempt)
                if isinstance(error, requests.HTTPError) or attempt == self.retries:
                    return {"success": False, "url": url,
                            "error": f"Request failed ({type(error).__name__})"}
        raise RuntimeError("Unreachable request state")

    def catalog(self, endpoint, params):
        result = self.get_json(endpoint, params)
        value = extract_api_data(result)
        if not isinstance(value, list):
            raise RuntimeError(f"Official catalog unavailable: {endpoint}; no identifiers assumed")
        return value

    def verify_year(self, academic_year, year_id=None):
        years = self.catalog("master/year", {"year": 1})
        matched = [year for year in years if str(get_value(year, "yearDesc", "year", "academicYear", "yearName")) == academic_year]
        if len(matched) != 1:
            raise ValueError(f"Academic year {academic_year!r} is not in the official catalog")
        verified = matched[0].get("yearId")
        if verified is None or (year_id is not None and str(year_id) != str(verified)):
            raise ValueError("Academic year and year ID do not match the official catalog")
        return verified

    def close(self):
        self.session.close()


def selected_rows(rows, states, districts, pincodes):
    for row in rows:
        if states and normalize(get_value(row, "stateName", "state")) not in {normalize(s) for s in states}:
            continue
        if districts and normalize(get_value(row, "districtName", "district")) not in {normalize(d) for d in districts}:
            continue
        # Search summaries can be current while details describe the selected
        # year. Evaluate PINs after details rather than dropping candidates here.
        yield row


def source_pincode(record, academic_year=None):
    """Keep source fields as evidence; never extract digits from an address."""
    fields = {}
    for section in ("report_card", "school_summary"):
        values = record.get(section) or {}
        for key in ("pincode", "pinCode", "pin_code"):
            if values.get(key) not in (None, ""):
                fields[f"{section}.{key}"] = values[key]
        # The detailed report takes precedence over the current search summary.
        if fields:
            break
    values = [value for value in fields.values() if value is not None and str(value).strip()]
    codes = [source_pin(value) for value in values]
    status = ("missing" if not values else "invalid" if None in codes else
              "conflicting" if len(set(codes)) > 1 else "valid")
    requested_year = academic_year or (record.get("metadata") or {}).get("academic_year")
    source = section if fields else None
    source_year = get_value(record.get(source) or {}, "academicYear", "sessionYear", "yearDesc") if source else None
    # Annual detail endpoints are requested with the verified year ID. A current
    # summary has its own independent year and cannot inherit that detail year.
    if source == "report_card" and source_year is None:
        source_year = (record.get("metadata") or {}).get("academic_year")
    return {"actual_pincode": codes[0] if status == "valid" else None,
            "pincode_status": status, "pincode_raw": fields, "pincode_field": list(fields),
            "pincode_source": source, "pincode_academic_year": source_year,
            "pincode_requested_academic_year": requested_year,
            "scope_year_verified": bool(requested_year and source_year and str(source_year) == str(requested_year))}


def validate_record_context(record, row, args):
    """Reject contradictory source identity/geography/year without altering JSON."""
    code = str(get_value(row, "udiseschCode", "udiseSchCode", "udiseCode") or "")
    state, district = get_value(row, "stateName", "state"), get_value(row, "districtName", "district")
    if not state or not district:
        raise ValueError("Source listing is missing state or district")
    metadata = record.get("metadata") or {}
    if metadata.get("udise_code") is not None and str(metadata["udise_code"]) != code:
        raise ValueError("Source identity mismatch in metadata")
    if metadata.get("academic_year") is not None and metadata["academic_year"] != args.academic_year:
        raise ValueError("Source academic-year mismatch in metadata")
    if metadata.get("year_id") is not None and str(metadata["year_id"]) != str(args.year_id):
        raise ValueError("Source year ID mismatch in metadata")
    for section in ("school_profile", "report_card"):
        value = record.get(section) or {}
        actual_id = get_value(value, "udiseschCode", "udiseSchCode", "udiseCode")
        if actual_id is not None and str(actual_id) != code:
            raise ValueError(f"Source identity mismatch in {section}")
        for keys, expected in ((('stateName', 'state'), state), (('districtName', 'district'), district)):
            actual = get_value(value, *keys)
            if actual and normalize(actual) != normalize(expected):
                raise ValueError(f"Source geography mismatch in {section}")
        actual_year = get_value(value, "academicYear", "sessionYear", "yearDesc")
        if actual_year and str(actual_year) != args.academic_year:
            raise ValueError(f"Source detail academic-year mismatch in {section}")
        if value.get("yearId") is not None and str(value["yearId"]) != str(args.year_id):
            raise ValueError(f"Source detail year ID mismatch in {section}")
    summary_year = get_value(row, "sessionYear", "academicYear", "yearDesc")
    return {"identity_verified": True, "geography_verified": True,
            "summary_academic_year": summary_year,
            "summary_year_matches": summary_year is None or str(summary_year) == args.academic_year}


def validate_saved_response(value):
    """Check response envelopes without interpreting a school's status field."""
    if not isinstance(value, dict) or any(
            key in value for key in ("udiseschCode", "udiseSchCode", "udiseCode")):
        return
    if value.get("status") is False:
        check_access(json.dumps(value))
        raise ValueError("Saved school list is an unsuccessful source response")
    for key in ("response", "data", "results", "result"):
        validate_saved_response(value.get(key))


def load_lists(paths):
    rows = []
    for path in paths:
        value = read_json(path)
        validate_saved_response(value)
        found = find_school_list(value)
        if any(total > len(found) for total in listing_totals(value)):
            raise ValueError(f"Saved response is paginated/incomplete: {path}; export the complete source listing")
        rows.extend(found)
    return rows


def listing_totals(value):
    """Inspect response wrappers, without mistaking school detail fields for totals."""
    if not isinstance(value, dict):
        return []
    totals = [value[key] for key in ("totalElements", "totalRecords")
              if isinstance(value.get(key), int) and not isinstance(value[key], bool)]
    for key in ("data", "response", "results", "result"):
        totals.extend(listing_totals(value.get(key)))
    return totals


def task_matches(url, task):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "kys.udiseplus.gov.in":
        return False
    query = parse_qs(parsed.query)
    if task.get("pincode"):
        return parsed.path.endswith("/search-schools") and query.get("searchType") == ["4"] and query.get("searchParam") == [task["pincode"]]
    return (parsed.path.endswith("/search-school/by-region")
            and query.get("stateId") == [str(task["stateId"])]
            and query.get("districtId") == [str(task["districtId"])]
            and query.get("yearId") == [str(task["yearId"])])


def manual_discovery(tasks, wait_seconds, report):
    """Observe only responses to searches the user submits in the official UI.

    No CAPTCHA tokens are stored or replayed. UDISE returns its full content list;
    its displayed Next/Previous pages are client-side slices of that response.
    """
    if wait_seconds <= 0:
        report["pending_discovery"] = tasks
        raise ManualIntervention(
            "UDISE school search requires manual CAPTCHA. Use --manual-captcha 300 "
            "to complete each search in the visible browser, or --school-list FILE "
            "with complete official JSON responses. Pending scopes are in the run report.")
    from playwright.sync_api import sync_playwright, TimeoutError as BrowserTimeout
    rows = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        try:
            page = browser.new_page()
            page.goto("https://kys.udiseplus.gov.in/", wait_until="domcontentloaded")
            for index, task in enumerate(tasks):
                report["pending_discovery"] = tasks[index:]
                print("MANUAL SEARCH: " + json.dumps(task, ensure_ascii=False))
                print(f"Complete the official search and CAPTCHA in the browser within {wait_seconds}s. "
                      "Choose Advanced Search for a state/district/year, or PIN Code for a PIN task.")
                try:
                    response = page.wait_for_event("response", predicate=lambda r: task_matches(r.url, task), timeout=wait_seconds * 1000)
                except BrowserTimeout as error:
                    raise ManualIntervention("Manual search timed out; rerun to resume pending discovery") from error
                check_access(response.text(), response.status)
                value = response.json()
                if value.get("status") is not True:
                    raise ManualIntervention("Official search was unsuccessful; complete a new manual search to resume")
                found = find_school_list(value)
                totals = listing_totals(value)
                if any(total > len(found) for total in totals):
                    raise RuntimeError("Source returned a partial listing; export complete official pages with --school-list")
                rows.extend(found)
                report.setdefault("listing_scopes", []).append({**task, "unique_candidates": len({
                    str(get_value(row, "udiseschCode", "udiseSchCode", "udiseCode")) for row in found}),
                    "reported_total": max(totals) if totals else None,
                    "pagination_complete": bool(totals) and all(total == len(found) for total in totals),
                    "stop_reason": "official_manual_response"})
                # Retain exact source body without query/CAPTCHA tokens in a versioned report.
                write_report(report["report_dir"], "udise_discovery", {"scope": task, "response": value})
            report["pending_discovery"] = []
        finally:
            browser.close()
    return rows


def resolve_states(client, args, report):
    states = client.catalog("states", {"yearId": args.year_id})
    names = {normalize(item["stateName"]): item for item in states}
    unknown = [name for name in args.state if normalize(name) not in names]
    if unknown:
        raise ValueError("Unknown source state(s): " + ", ".join(unknown))
    chosen = states if args.all_states else [names[normalize(name)] for name in args.state]
    report["verified_states"] = [item["stateName"] for item in chosen]
    return chosen


def discover(client, args, report):
    chosen = resolve_states(client, args, report)
    tasks = []
    if args.pincode:
        # Official PIN search is national/current. State/district scope is checked on records.
        tasks = [{"pincode": value, "academic_year": args.academic_year} for value in args.pincode]
        report["discovery_limitation"] = "National current PIN search does not establish historical regional completeness"
    else:
        matched_districts = set()
        for state in chosen:
            districts = client.catalog("districts", {"stateId": state["stateId"], "yearId": args.year_id})
            for district in districts:
                name = get_value(district, "districtName", "name")
                if args.district and normalize(name) not in {normalize(d) for d in args.district}:
                    continue
                matched_districts.add(normalize(name))
                tasks.append({"state": state["stateName"], "stateId": state["stateId"],
                              "district": name, "districtId": district["districtId"], "yearId": args.year_id})
        if any(normalize(name) not in matched_districts for name in args.district):
            raise ValueError("One or more requested districts are absent from the selected official states")
    if not tasks:
        raise RuntimeError("No official discovery scopes resolved; empty catalogs do not establish zero schools")
    report["discovery_tasks"] = tasks
    return manual_discovery(tasks, args.manual_captcha, report)


def build_record(client, row, args):
    code = str(get_value(row, "udiseschCode", "udiseSchCode", "udiseCode") or "")
    school_id = get_value(row, "schoolId", "schoolid")
    record = {
        "metadata": {"source": "UDISE+ Know Your School", "location": str(row.get("stateName", "Unknown")).title(),
                     "academic_year": args.academic_year, "year_id": args.year_id,
                     "school_name": get_value(row, "schoolName", "schName", "name"),
                     "udise_code": code, "school_id": school_id},
        "school_summary": row, "school_history": None, "_api_status": {},
    }
    for section, (label, endpoint) in DETAILS.items():
        result = client.get_json(endpoint, {"udiseSchCode": code, "yearId": args.year_id})
        value = extract_api_data(result)
        if not isinstance(value, dict) or not value:
            raise RuntimeError(f"Incomplete section: {section}; previous raw data preserved")
        record[section] = value
        record["_api_status"][label] = result
    if school_id:
        result = client.get_json("school/track", {"schoolId": school_id})
        value = extract_api_data(result)
        if value is None:
            raise RuntimeError("History request failed; record remains retryable")
        record["school_history"] = value
        record["_api_status"]["history"] = result
    else:
        record["_api_status"]["history"] = None
    return record


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    scope = command.add_mutually_exclusive_group()
    scope.add_argument("--state", action="append", default=[], help="Official state name; repeat or separate by commas")
    scope.add_argument("--all-states", action="store_true", help="Every state/UT offered by the official source")
    command.add_argument("--district", action="append", default=[], help="District name; repeat for multiple districts")
    command.add_argument("--pincode", action="append", default=[], help="Six-digit PIN; official PIN search or exact source-field filtering")
    command.add_argument("--academic-year", default=ACADEMIC_YEAR)
    command.add_argument("--year-id", type=int, help="Checked against the official year catalog; never guessed")
    command.add_argument("--school-list", action="append", type=Path, default=[], help="Complete saved official school-list JSON; repeat for multiple files")
    command.add_argument("--manual-captcha", type=int, default=0, metavar="SECONDS", help="Open visible browser and wait this long per manual official search")
    command.add_argument("--headers-file", type=Path, help="Local JSON object with authorized API headers; never logged")
    command.add_argument("--api-base-url", default=BASE_URL, help="Authorized compatible API URL")
    command.add_argument("--data-dir", type=Path, default=ROOT / "data")
    command.add_argument("--report-dir", type=Path, default=ROOT / "reports" / "scraping")
    command.add_argument("--limit", type=int, help="Maximum unique detail candidates for this run")
    command.add_argument("--delay", type=float, default=REQUEST_DELAY, help="Minimum seconds between requests")
    command.add_argument("--retries", type=int, default=3, help="Bounded transient retries; never retries CAPTCHA")
    command.add_argument("--refresh", action="store_true", help="Intentionally update changed source records after byte-verified backup")
    command.add_argument("--dry-run", action="store_true", help="Preview scope offline; no requests or file writes")
    add_arguments(command)
    return command


def run_job(args):
    command = parser()
    args.state, args.district, args.pincode = map(parse_values, (args.state, args.district, args.pincode))
    if (not args.all_states and not args.state) or any(pin(value) is None for value in args.pincode):
        command.error("Provide a nonempty state selection and valid six-digit pincodes")
    if args.delay < 0 or args.retries < 0 or args.manual_captcha < 0 or (args.limit is not None and args.limit < 1):
        command.error("Delay/retries/manual wait must be nonnegative and limit must be positive")
    if not re.fullmatch(r"\d{4}-\d{2}", args.academic_year):
        command.error("Academic year must be YYYY-YY; the source's 'Real Time' view is not a yearly snapshot")
    report = {"source": "UDISE+", "started_at": datetime.now(timezone.utc).isoformat(),
              "states": args.state or "all official states", "districts": args.district,
              "pincodes": args.pincode, "academic_year": args.academic_year,
              "report_dir": str(args.report_dir), "status": "running", "failures": [],
              "scope": {"states": args.state, "districts": args.district, "pincodes": args.pincode,
                        "limit": args.limit, "max_duration": getattr(args, "max_duration", None)},
              "record_outcomes": [], "listing_scopes": [],
              "counts": {key: 0 for key in ("discovered", "success", "updated", "unchanged", "skipped", "duplicate", "filtered", "missing_pincode", "invalid_pincode", "unverified_pincode_year", "failed", "pending")}}
    deadline = (time.monotonic() + args.max_duration if getattr(args, "max_duration", None) else None)
    def outcome(code, row, result, reason, record=None, **extra):
        evidence = source_pincode(record or {"school_summary": row}, args.academic_year)
        report["record_outcomes"].append({"udise_code": code, "state": get_value(row, "stateName", "state"),
            "district": get_value(row, "districtName", "district"), "outcome": result, "reason": reason,
            **evidence, **extra})
        if record is not None and result not in ("duplicate", "pending"):
            report["counts"]["missing_pincode"] += int(evidence["pincode_status"] == "missing")
            report["counts"]["invalid_pincode"] += int(evidence["pincode_status"] in ("invalid", "conflicting"))
            report["counts"]["unverified_pincode_year"] += int(evidence["actual_pincode"] is not None
                                                            and not evidence["scope_year_verified"])

    def pincode_filter_reason(record):
        if not args.pincode:
            return None
        evidence = source_pincode(record, args.academic_year)
        if evidence["actual_pincode"] and not evidence["scope_year_verified"]:
            return "pincode_year_mismatch" if evidence["pincode_academic_year"] else "pincode_year_unverified"
        if evidence["actual_pincode"] not in args.pincode:
            return "pincode_mismatch" if evidence["actual_pincode"] else evidence["pincode_status"] + "_pincode"
        return None
    runtime = None
    try:
        saved_rows = load_lists(args.school_list) if args.school_list else None
        if args.dry_run:
            report["status"] = "dry_run"
            report["year_id"] = args.year_id
            report["source_validation"] = "pending: dry-run makes no network requests"
            report["school_lists"] = [str(path) for path in args.school_list]
            report["selected_saved_rows"] = len(list(selected_rows(saved_rows or [], args.state, args.district, args.pincode)))
            print(json.dumps(report, indent=2))
            return 0
        headers = read_json(args.headers_file) if args.headers_file else json.loads(os.environ.get("UDISE_HEADERS_JSON", "{}"))
        if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
            raise ValueError("Authorized headers must be a JSON object of strings")
        with SourceLock(args.data_dir, "udise"):
            runtime = IncrementalRuntime(args, "udise")
            runtime.start()
            if not runtime.scope_due():
                report["status"] = "not_due"
                report["reason"] = "Location checkpoint is not due; pending database writes were checked separately"
                return 0
            client = UdiseClient(args.api_base_url, headers, args.delay, args.retries,
                                 on_event=runtime.event)
            try:
                args.year_id = client.verify_year(args.academic_year, args.year_id)
                report["year_id"] = args.year_id
                retry_rows = runtime.retry_rows()
                if retry_rows:
                    rows = [task["task_input"] for task in retry_rows]
                    report["partial_recovery"] = True
                    report["discovery_scope"] = "Eligible saved tasks only; a later geographic check can discover new schools"
                    runtime.event("failed_task_resumed")
                elif getattr(args, "retry_failed", False) and not any(
                        not task.get("school_identifier") for task in runtime.memory.tasks(
                            "udise", scope_key=runtime.scope, academic_year=runtime.year)):
                    rows = []
                    report["reason"] = "No failed school tasks in the selected scope"
                elif saved_rows is not None:
                    chosen = resolve_states(client, args, report)
                    available = {normalize(get_value(row, "stateName", "state")) for row in saved_rows}
                    missing = [state["stateName"] for state in chosen if normalize(state["stateName"]) not in available]
                    if missing:
                        raise ValueError("Saved lists do not cover selected state(s): " + ", ".join(missing))
                    if args.district:
                        official_districts = []
                        for state in chosen:
                            districts = client.catalog("districts", {"stateId": state["stateId"], "yearId": args.year_id})
                            official_districts.extend({"state": state["stateName"],
                                "district": get_value(item, "districtName", "name"),
                                "district_id": item.get("districtId")} for item in districts
                                if get_value(item, "districtName", "name"))
                        official_names = {normalize(item["district"]) for item in official_districts}
                        if any(normalize(name) not in official_names for name in args.district):
                            raise ValueError("One or more requested districts are absent from the selected official states")
                        report["verified_districts"] = [item for item in official_districts
                            if normalize(item["district"]) in {normalize(name) for name in args.district}]
                        available_districts = {normalize(get_value(row, "districtName", "district"))
                                               for row in selected_rows(saved_rows, args.state, [], [])}
                        if any(normalize(name) not in available_districts for name in args.district):
                            raise ValueError("One or more districts are not represented in the supplied official lists")
                    report["discovery_scope"] = "Supplied official lists only; statewide completeness is not established"
                    rows = saved_rows
                else:
                    rows = discover(client, args, report)
                selected = list(selected_rows(rows, args.state, args.district, args.pincode))
                # The incremental policy decides when a refresh is due; RawStore
                # then preserves the old bytes before accepting changed details.
                store = RawStore("udise", args.data_dir, args.academic_year, refresh=True)
                report["invalid_existing_files"] = store.invalid_files
                seen = set()
                counts = report["counts"]
                report["listing_data"] = rows
                report["counts"]["out_of_scope"] = len(rows) - len(selected)
                report["candidate_manifest"] = selected
                counts["discovered"] = len({str(get_value(row, "udiseschCode", "udiseSchCode", "udiseCode") or "") for row in selected})
                # Register all candidates before details; a bounded or interrupted
                # run must leave every unprocessed source ID resumable.
                for row in selected:
                    code = str(get_value(row, "udiseschCode", "udiseSchCode", "udiseCode") or "")
                    runtime.discovered(code, row, get_value(row, "stateName", "state"), get_value(row, "districtName", "district"))
                attempted = 0
                for index, row in enumerate(selected):
                    code = str(get_value(row, "udiseschCode", "udiseSchCode", "udiseCode") or "")
                    if code in seen:
                        counts["duplicate"] += 1
                        outcome(code, row, "duplicate", "duplicate_source_identifier")
                        runtime.event("duplicate_detected", identifier=code)
                        continue
                    seen.add(code)
                    budget_reached = deadline is not None and time.monotonic() >= deadline
                    if budget_reached or (args.limit is not None and attempted >= args.limit):
                        counts["pending"] += 1
                        report["time_budget_reached"] = bool(budget_reached)
                        report["candidate_limit_reached"] = bool(args.limit is not None and attempted >= args.limit)
                        outcome(code, row, "pending", "time_budget_reached" if budget_reached else "candidate_limit_reached")
                        continue
                    attempted += 1
                    try:
                        if not re.fullmatch(r"\d{11}", code):
                            raise ValueError("Missing or invalid source UDISE code")
                        state, district = row.get("stateName"), row.get("districtName")
                        cached = runtime.filtered_record(code) if args.pincode else None
                        if cached:
                            context = validate_record_context(cached, row, args)
                            reason = pincode_filter_reason(cached)
                            if reason:
                                counts["filtered"] += 1
                                outcome(code, row, "filtered", reason, cached, **context)
                                runtime.completed(code, "filtered")
                                continue
                        existing = store.existing(code)
                        if not runtime.detail_due(code, existing, store.find(code)):
                            if not existing:
                                counts["deferred"] = counts.get("deferred", 0) + 1
                                counts["pending"] += 1
                                outcome(code, row, "pending", "retry_backoff_or_attempt_limit")
                                continue
                            context = validate_record_context(existing, row, args)
                            reason = pincode_filter_reason(existing)
                            if reason:
                                counts["filtered"] += 1
                                outcome(code, row, "filtered", reason, existing, **context)
                                runtime.completed(code, "filtered")
                            else:
                                runtime.persist(code, existing, store.find(code), fetched=False)
                                counts["skipped"] += 1
                                outcome(code, row, "skipped", "detail_refresh_not_due", existing, **context)
                                runtime.event("detail_not_due", identifier=code)
                            continue
                        runtime.attempting(code, row, state, district)
                        record = build_record(client, row, args)
                        context = validate_record_context(record, row, args)
                        evidence = source_pincode(record, args.academic_year)
                        reason = pincode_filter_reason(record)
                        if reason:
                            counts["filtered"] += 1
                            outcome(code, row, "filtered", reason, record, **context)
                            runtime.filtered(code, record, evidence["actual_pincode"])
                            continue
                        location = record["metadata"]["location"]
                        path = args.data_dir / safe_filename(location) / args.academic_year / f"{safe_filename(record['metadata']['school_name'])}_{code}.json"
                        result_name = store.save(code, record, path)
                        counts[result_name] += 1
                        outcome(code, row, result_name, "raw_record_" + result_name, record, **context)
                        runtime.persist(code, record, store.find(code))
                        runtime.event("record_" + result_name, identifier=code)
                    except ManualIntervention as error:
                        counts["failed"] += 1
                        counts["pending"] += len(selected) - index - 1
                        report["failures"].append({"udise_code": code, "status": "manual_intervention"})
                        outcome(code, row, "failed", "manual_intervention")
                        for pending in selected[index + 1:]:
                            pending_code = str(get_value(pending, "udiseschCode", "udiseSchCode", "udiseCode") or "")
                            if pending_code not in seen:
                                seen.add(pending_code)
                                outcome(pending_code, pending, "pending", "manual_intervention")
                        counts["pending"] = len({item["udise_code"] for item in report["record_outcomes"] if item["outcome"] == "pending"})
                        runtime.failed(code, error, row)
                        raise
                    except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
                        counts["failed"] += 1
                        from school_scraping.memory import safe_error
                        report["failures"].append({"udise_code": code, "error": safe_error(error)})
                        outcome(code, row, "failed", "detail_or_validation_failure", error=safe_error(error))
                        runtime.failed(code, error, row)
                report["status"] = "time_budget_reached" if report.get("time_budget_reached") else "failed" if counts["failed"] else "completed"
            finally:
                client.close()
    except ManualIntervention as error:
        report["status"], report["error"] = "manual_intervention", str(error)
        if runtime and not report["failures"]:
            runtime.failed("", error, {"pending_discovery": report.get("pending_discovery", [])}, phase="discovery")
    except KeyboardInterrupt:
        report["status"] = "interrupted"
    except Exception as error:
        from school_scraping.memory import safe_error
        report["status"], report["error"] = "failed", safe_error(error)
        if runtime:
            runtime.failed("", error, phase="discovery")
    finally:
        if not args.dry_run:
            terminal = {item["udise_code"] for item in report["record_outcomes"]
                        if item["outcome"] not in ("duplicate", "pending")}
            known_pending = {item["udise_code"] for item in report["record_outcomes"] if item["outcome"] == "pending"}
            for row in report.get("candidate_manifest", []):
                code = str(get_value(row, "udiseschCode", "udiseSchCode", "udiseCode") or "")
                if code not in terminal and code not in known_pending:
                    known_pending.add(code)
                    outcome(code, row, "pending", report["status"])
            report["counts"]["pending"] = len(known_pending - terminal)
            source_scope_verified = (bool(report["listing_scopes"]) and not args.school_list
                                     and not args.pincode and not report.get("partial_recovery")
                                     and not report.get("pending_discovery")
                                     and all(scope.get("pagination_complete") for scope in report["listing_scopes"]))
            details_complete = (report["status"] == "completed" and not report["counts"].get("failed")
                                and not report["counts"].get("pending"))
            limitations = ["A complete authoritative postal denominator is required for verified PIN coverage"]
            if args.school_list:
                limitations.append("Saved official responses establish only their supplied scope; full geography is not certified")
            if args.limit is not None:
                limitations.append("Candidate-limited validation does not establish full scope coverage")
            if any(item.get("summary_year_matches") is False for item in report["record_outcomes"]):
                limitations.append("Current listing academic year differs from selected detail year; original source values preserved")
            if report["counts"].get("unverified_pincode_year"):
                limitations.append("Observed PIN fields lack verified selected-year provenance; current summary PINs do not establish historical PIN coverage")
            report["coverage"] = {"listing_complete": source_scope_verified,
                                  "details_complete": details_complete,
                                  "candidate_limit_reached": bool(report.get("candidate_limit_reached")),
                                  "scope_complete": source_scope_verified and details_complete and args.limit is None,
                                  "limitations": limitations}
            report["pending_identifiers"] = sorted({item["udise_code"] for item in report["record_outcomes"] if item["outcome"] == "pending"})
            if runtime:
                runtime.finish(report, scope_complete=report["coverage"]["scope_complete"]
                               and not getattr(args, "retry_failed", False))
            report["ended_at"] = datetime.now(timezone.utc).isoformat()
            report["duration_seconds"] = (datetime.fromisoformat(report["ended_at"]) - datetime.fromisoformat(report["started_at"])).total_seconds()
            report_path = write_report(args.report_dir, "udise", report)
            print(json.dumps(report, indent=2))
            print(f"Run report: {report_path}")
    return 0 if report["status"] in ("completed", "dry_run", "not_due") else 2 if report["status"] == "manual_intervention" else 1


def main(argv=None):
    command = parser()
    args = command.parse_args(argv)
    try:
        jobs = resolve_source_args(args, "udise", argv)
    except ConfigurationError as error:
        command.error(str(error))
    if not jobs:
        print(json.dumps({"source": "UDISE+", "status": "idle", "dry_run": args.dry_run,
                          "reason": "No enabled UDISE locations; enable locations in config or supply --state"}))
        return 0
    if args.sync_pending:
        if args.dry_run:
            print(json.dumps({"mode": "sync_pending", "dry_run": True, "database_enabled": jobs[0].database}))
            return 0
        return sync_pending(jobs[0])
    codes = [run_job(job) for job in jobs]
    return max(codes, default=0)


if __name__ == "__main__":
    raise SystemExit(main())
