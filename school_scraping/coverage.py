"""Read-only school coverage accounting with explicit, conservative denominators.

``generate_location_reports`` accepts historical audit evidence and isolated run
reports. It writes reporting artifacts only; it never changes school records,
recovery state, configuration, or database contents.
"""

import csv
import hashlib
import json
import re
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from school_scraping.common import atomic_json, complete_record, normalize, pin, read_json, source_pin


LOCATIONS = ("chandigarh", "delhi", "mumbai")
SOURCES = ("udise", "saras")
CSV_FIELDS = (
    "state_ut", "district", "pincode", "source", "expected_pincode_status",
    "source_listing_count", "successfully_saved_schools", "updated_schools",
    "unchanged_schools", "missing_details", "missing_current_details", "invalid_source_listing_rows",
    "failed_records", "duplicate_records",
    "pending_records", "missing_pincodes", "invalid_pincodes", "historical_schools",
    "session_saved_schools", "last_checked_timestamp", "last_listing_checked_at",
    "last_detail_checked_at", "report_generated_at", "coverage_status", "notes",
)


def _object(value):
    return value if isinstance(value, dict) else {}


def _load(value):
    return read_json(value) if isinstance(value, (str, Path)) else value


def _list(value):
    return value if isinstance(value, (list, tuple)) else [value] if value is not None else []


def _value(record, *names):
    keys = {normalize(name) for name in names}
    return next((value for key, value in _object(record).items()
                 if normalize(key) in keys and value is not None), None)


def _bucket(value):
    if source_pin(value):
        return source_pin(value)
    return "_MISSING" if value is None or str(value).strip() == "" else "_INVALID"


def _identifier(value):
    return str(value.get("identifier") or value.get("affiliation_number")
               or value.get("udise_code") or value.get("udiseschCode")
               or value.get("udiseSchCode") or value.get("udiseCode")
               or _object(value.get("listing_data")).get("affiliation_number")
               or "").strip()


def _district(value):
    # Preserve the source's names: UDISE MUMBAI II is not the SARAS label.
    return str(value or "").strip().upper()


def _latest_timestamp(values):
    """Compare source timestamps without inventing a check at report generation."""
    parsed = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                continue
            parsed.append((stamp.astimezone(timezone.utc), value))
        except ValueError:
            continue
    return max(parsed, default=(None, ""), key=lambda item: item[0])[1]


def _run_timestamp(run):
    return _latest_timestamp(run.get(key) for key in ("ended_at", "finished_at", "started_at"))


def _listing_origin(run):
    if str(run.get("discovery_scope") or "").startswith("Supplied official lists"):
        return "supplied_official_response"
    if run.get("partial_recovery"):
        return "recovered_task_inputs"
    return "live_source_listing" if run.get("listing_scopes") else "unverified_run_inputs"


def _preserve_report(path):
    """Retain a byte-verified version before replacing a generated report."""
    path = Path(path)
    if not path.exists():
        return
    folder = path.parent / "_backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    backup = folder / f"{path.stem}_{stamp}_{uuid.uuid4().hex[:8]}{path.suffix}"
    shutil.copy2(path, backup)
    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(backup.read_bytes()).digest():
        raise OSError(f"Report backup verification failed: {path}")


def raw_record(source, record, path=None):
    """Project a raw record into reporting fields without modifying its bytes."""
    metadata = _object(record.get("metadata"))
    summary = _object(record.get("school_summary"))
    card = _object(record.get("report_card"))
    details = _object(record.get("school_details"))
    if source == "udise":
        raw_pin = _value(card, "pincode")
        card_pin = any(normalize(key) == "pincode" and value not in (None, "")
                       for key, value in card.items())
        if not card_pin:
            raw_pin = _value(summary, "pincode", "pinCode")
        identity = metadata.get("udise_code") or _value(summary, "udiseschCode", "udiseSchCode")
        state = _value(card, "stateName") or _value(summary, "stateName") or metadata.get("state")
        district = _value(card, "districtName") or _value(summary, "districtName") or metadata.get("district")
    else:
        raw_pin = _value(details, "Pin Code", "PINCode", "Postal Code")
        card_pin = False
        identity = metadata.get("affiliation_number") or _value(details, "Affiliation Number")
        state = _value(details, "State") or metadata.get("state")
        district = _value(details, "District") or metadata.get("district")
    source_fields = card if source == "udise" and card_pin else summary if source == "udise" else details
    source_values = {key: value for key, value in source_fields.items()
                     if normalize(key) in ({"pincode"} if source == "udise" else {"pincode", "postalcode"})}
    values = [item for value in source_values.values()
              for item in (value if isinstance(value, list) else [value])
              if item is not None and str(item).strip()]
    codes = [source_pin(value) for value in values]
    pincode_status = ("missing" if not values else "invalid" if None in codes
                      else "conflicting" if len(set(codes)) > 1 else "valid")
    result = {
        "identifier": str(identity or "").strip(), "state": state, "district": district,
        "pincode": codes[0] if pincode_status == "valid" else None, "raw_pincode": raw_pin,
        "pincode_status": pincode_status, "pincode_raw": source_values,
        "details_complete": complete_record(source, record),
        "metadata_year": metadata.get("academic_year"),
        "summary_year": summary.get("sessionYear"), "report_card_year": card.get("yearDesc"),
        "pincode_source": ("report_card.pincode" if card_pin else "school_summary.pincode")
        if source == "udise" else "school_details.Pin Code",
    }
    if source == "udise":
        source_year = _value(source_fields, "yearDesc", "sessionYear", "academicYear")
        if card_pin and source_year is None:
            source_year = metadata.get("academic_year")
        target_year = metadata.get("academic_year")
        result["pincode_academic_year"] = source_year
        result["scope_year_verified"] = bool(target_year and source_year and str(source_year) == str(target_year))
    if path is not None:
        result["path"] = str(path)
    return result


def collect_records(data_dir, source):
    """Read isolated scraper data roots; return normalized records and read errors."""
    root = Path(data_dir)
    patterns = ("CBSE_SARAS/*/schools/*.json",) if source == "saras" else ("*/*/*.json",)
    records, errors = [], []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if path.name.startswith("_") or "_errors" in path.parts:
                continue
            if source == "udise" and not re.fullmatch(r"\d{4}-\d{2}", path.parent.name):
                continue
            try:
                value = read_json(path)
                if not isinstance(value, dict):
                    raise ValueError("School JSON must be an object")
                records.append(raw_record(source, value, path))
            except (OSError, ValueError, TypeError, AttributeError) as error:
                errors.append({"path": str(path), "error": str(error)})
    return records, errors


def coverage_status(evidence):
    """A completed process is insufficient evidence of complete school coverage."""
    if evidence.get("blocked_captcha"):
        return "BLOCKED_CAPTCHA"
    if evidence.get("source_unavailable"):
        return "SOURCE_UNAVAILABLE"
    if not evidence.get("tested"):
        return "NOT_VERIFIED"
    required = ("geography_verified", "pagination_complete", "details_complete",
                "expected_pincodes_complete", "expected_pincodes_checked")
    blockers = ("limited", "unfinished", "failed_records", "pending_records", "missing_details",
                "missing_current_details", "invalid_source_listing_rows", "missing_pincodes", "invalid_pincodes",
                "year_scope_unverified", "out_of_scope_records")
    if all(evidence.get(key) is True for key in required) and not any(evidence.get(key) for key in blockers):
        return "COMPLETE_VERIFIED"
    return "PARTIAL"


def _run_evidence(runs, reference):
    latest = runs[-1] if runs else {}
    scope = _object(latest.get("scope"))
    coverage = _object(latest.get("coverage"))
    text = json.dumps(latest, ensure_ascii=True).casefold()
    manual = latest.get("status") in {"manual_intervention", "blocked_captcha"}
    latest_counts = _object(latest.get("counts"))
    available_work = bool(latest.get("candidate_manifest") or latest.get("listing_data")
                          or latest.get("record_outcomes") or latest_counts.get("discovered")
                          or any(latest_counts.get(key) for key in ("success", "updated", "unchanged", "skipped"))
                          or latest.get("listing_scopes"))
    discovery_scopes, discovery_run = [], None
    unresolved_captcha = False
    for run in runs:
        full_scope = (_object(run.get("coverage")).get("scope_complete") is True
                      and not run.get("partial_recovery") and _object(run.get("scope")).get("limit") is None)
        if full_scope:
            discovery_scopes, discovery_run, unresolved_captcha = [], None, False
            continue
        pending = run.get("pending_discovery") or []
        if pending:
            discovery_scopes, discovery_run = pending, run
        if run.get("status") in {"manual_intervention", "blocked_captcha"} and pending and "captcha" in json.dumps(run).casefold():
            unresolved_captcha = True
    listing_scopes = latest.get("listing_scopes") or []
    listing_complete = coverage.get("listing_complete") is True
    if listing_scopes:
        listing_complete = listing_complete and all(s.get("pagination_complete") is True for s in listing_scopes)
    limited = (scope.get("limit") is not None or latest.get("limit") is not None
               or coverage.get("candidate_limit_reached") is True
               or any(s.get("limited") for s in listing_scopes))
    return {
        "tested": bool(runs), "blocked_captcha": (manual and "captcha" in text) or unresolved_captcha,
        "source_unavailable": (manual and "captcha" not in text) or
        (latest.get("status") in {"source_unavailable", "failed", "startup_failed", "startup_failure", "error"} and not available_work),
        "geography_verified": coverage.get("scope_complete") is True
        and reference.get("civil_district_boundary_mapping_verified") is not False,
        "pagination_complete": listing_complete, "details_complete": coverage.get("details_complete") is True,
        "expected_pincodes_complete": reference.get("expected_pincode_inventory_complete") is True,
        "expected_pincodes_checked": coverage.get("expected_pincodes_checked") is True,
        "limited": bool(limited), "unfinished": latest.get("status") not in {"completed", "not_due"},
        "pending_discovery_scope_count": len(discovery_scopes), "pending_discovery_scopes": discovery_scopes,
        "pending_discovery_evidence_run_id": discovery_run.get("run_id") if discovery_run else None,
    }


def build_coverage(location, source, *, historical, current_runs, reference,
                   current_records=(), checked_at=None):
    """Return CSV rows and an honest source summary for one geographic scope.

    Historical records establish local inventory only. Session outcomes remain
    separate, and repeated runs or historical/current overlap never add schools.
    """
    if location not in LOCATIONS or source not in SOURCES:
        raise ValueError("Unsupported coverage location or source")
    checked_at = checked_at or datetime.now(timezone.utc).isoformat()
    loaded_runs = sorted(((_load(value), str(value) if isinstance(value, (str, Path)) else None)
                          for value in current_runs),
                         key=lambda value: value[0].get("started_at") or value[0].get("finished_at") or "")
    runs = [value for value, _ in loaded_runs]
    expected = set(reference.get("verified_reference_pincodes", []))
    historical_rows = historical.get("records", [])
    baseline = {_identifier(row): row for row in historical_rows if _identifier(row)}
    merged = dict(baseline)
    current = {_identifier(row): row for row in current_records if _identifier(row)}
    merged.update(current)
    outcomes = [value for run in runs for value in run.get("record_outcomes", [])]
    outcome_latest = {_identifier(row): row for row in outcomes if _identifier(row)
                      and row.get("outcome") != "duplicate"}
    outcome_timestamps, detail_timestamps = {}, {}
    for run in runs:
        stamp = _run_timestamp(run)
        for row in run.get("record_outcomes", []):
            identity = _identifier(row)
            if not identity:
                continue
            if row.get("outcome") in {"success", "updated", "unchanged", "failed"} or row.get("detail_source") == "live_detail":
                outcome_timestamps[identity] = _latest_timestamp((outcome_timestamps.get(identity), stamp))
            if row.get("outcome") in {"success", "updated", "unchanged"} or row.get("detail_source") == "live_detail":
                detail_timestamps[identity] = _latest_timestamp((detail_timestamps.get(identity), stamp))
    groups = {}

    def group(district, postal):
        key = (_district(district), postal)
        if key not in groups:
            groups[key] = dict.fromkeys(CSV_FIELDS, 0)
            groups[key].update({"state_ut": reference.get("state") or
                               ("MAHARASHTRA" if location == "mumbai" else location.upper()),
                               "district": key[0], "pincode": postal, "source": source,
                               "last_checked_timestamp": "", "last_listing_checked_at": "",
                               "last_detail_checked_at": "", "report_generated_at": checked_at, "notes": ""})
        return groups[key]

    def checked(value, stamp, kind=None):
        value["last_checked_timestamp"] = _latest_timestamp((value["last_checked_timestamp"], stamp))
        if kind:
            field = "last_" + kind + "_checked_at"
            value[field] = _latest_timestamp((value[field], stamp))

    def fields(row):
        postal = row.get("actual_pincode") or row.get("pincode")
        raw = row.get("raw_pincode", postal)
        status = row.get("pincode_status")
        bucket = source_pin(postal) or ("_INVALID" if status in {"invalid", "conflicting"} else _bucket(raw))
        if (row.get("outcome") in {"failed", "pending"} and not source_pin(postal)
                and not row.get("pincode_field") and not row.get("pincode_raw")):
            bucket = "_UNKNOWN"
        return row.get("detail_district") or row.get("district") or row.get("districtName"), bucket

    for identity, row in merged.items():
        district, postal = fields(row)
        value = group(district, postal)
        value["historical_schools"] += int(identity in baseline)
        value["successfully_saved_schools"] += int(row.get("details_complete") is True)
        value["missing_details"] += int(row.get("details_complete") is not True)
        value["missing_pincodes"] += int(postal == "_MISSING")
        value["invalid_pincodes"] += int(postal == "_INVALID")
        checked(value, detail_timestamps.get(identity), "detail")
    for identity, paths in historical.get("duplicate_identifiers", {}).items():
        if identity in baseline:
            group(*fields(baseline[identity]))["duplicate_records"] += max(0, len(paths) - 1)

    listing_documents = []
    for run, path in loaded_runs:
        for key in ("candidate_manifest", "listing_data", "listing_records"):
            if isinstance(run.get(key), list):
                listing_documents.append((run, path, key, run[key]))
                break
    # A new fully enumerated source snapshot supersedes removed identifiers.
    # Partial recovery manifests contain only unfinished tasks, so keep their
    # cumulative session evidence until a fresh whole-scope listing replaces it.
    if listing_documents:
        last = listing_documents[-1]
        if _object(last[0].get("coverage")).get("listing_complete") is True and not last[0].get("partial_recovery"):
            listing_documents = [last]
            listing_basis = "current_verified_listing_snapshot"
        else:
            listing_basis = "current_partial_manifest_union"
        origins = {_listing_origin(run) for run, _, _, _ in listing_documents}
        if origins == {"supplied_official_response"}:
            listing_basis = "supplied_official_response_inputs"
        elif "live_source_listing" not in origins:
            listing_basis = "saved_or_unverified_task_inputs"
        latest_listing = [row for _, _, _, records in listing_documents for row in records]
    else:
        listing_basis = "historical_saved_listing"
        latest_listing = [row for value in historical.get("listing_files", []) for row in value.get("records", [])]
    listing_evidence_runs = [{"run_id": run.get("run_id"), "started_at": run.get("started_at"),
                              "report_path": path, "manifest_field": key, "rows": len(records),
                              "partial_recovery": bool(run.get("partial_recovery")),
                              "discovery_origin": _listing_origin(run),
                              "listing_complete": _object(run.get("coverage")).get("listing_complete") is True}
                             for run, path, key, records in listing_documents]
    source_scope_evidence = {}
    for run, path, _, _ in listing_documents:
        if _listing_origin(run) != "live_source_listing":
            continue
        for scope in run.get("listing_scopes", []):
            key = (normalize(scope.get("state")), normalize(scope.get("district")))
            source_scope_evidence[key] = {**scope, "run_id": run.get("run_id"), "report_path": path,
                                          "source_checked_at": _run_timestamp(run)}
    invalid_listing_rows = sum(max(0, scope.get("invalid_source_listing_rows", 0))
                               for scope in source_scope_evidence.values())
    if not source_scope_evidence:
        invalid_listing_rows = max((run.get("counts", {}).get("invalid_listing_rows", 0) for run in runs), default=0)
    source_rows_observed = (sum(scope["source_rows_observed"] for scope in source_scope_evidence.values())
                            if source_scope_evidence and all(isinstance(scope.get("source_rows_observed"), int)
                                                              for scope in source_scope_evidence.values()) else None)
    quarantined_listing_rows = [{"state": scope.get("state"), "district": scope.get("district"), **row}
                                for scope in source_scope_evidence.values() for row in scope.get("unparsed_listing_rows", [])]
    listing_timestamps = {}
    live_listing_ids, supplied_listing_ids = set(), set()
    for run, _, _, records in listing_documents:
        for row in records:
            identity = _identifier(row)
            if identity:
                if _listing_origin(run) == "live_source_listing":
                    live_listing_ids.add(identity)
                    listing_timestamps[identity] = _latest_timestamp((listing_timestamps.get(identity), _run_timestamp(run)))
                elif _listing_origin(run) == "supplied_official_response":
                    supplied_listing_ids.add(identity)
    listed = {}
    for row in latest_listing:
        identity = _identifier(row)
        if identity:
            listed[identity] = row
    if invalid_listing_rows:
        group("", "_UNKNOWN")["invalid_source_listing_rows"] = invalid_listing_rows
        checked(group("", "_UNKNOWN"), _latest_timestamp(scope.get("source_checked_at")
                                                          for scope in source_scope_evidence.values()), "listing")
    current_detail_ids = {identity for identity, row in current.items() if row.get("details_complete") is True}
    current_detail_ids.update(identity for identity, row in outcome_latest.items()
                              if row.get("identity_verified") is True and row.get("geography_verified") is True
                              and row.get("outcome") not in {"failed", "pending"})
    listing_pin_basis = Counter()
    for identity, row in listed.items():
        details = merged.get(identity) or outcome_latest.get(identity) or row
        district, postal = fields(details)
        if identity not in merged and identity not in outcome_latest and not source_pin(row.get("pincode")):
            postal = "_UNKNOWN"
        value = group(district, postal)
        value["source_listing_count"] += 1
        checked(value, listing_timestamps.get(identity), "listing")
        value["missing_current_details"] += int(identity not in current_detail_ids)
        listing_pin_basis["current_details" if pin(postal) and identity in current_detail_ids
                          else "historical_details" if pin(postal) and identity in baseline
                          else "unknown"] += 1
        if identity not in merged:
            value["missing_details"] += 1

    outcome_counter = Counter()
    for identity, row in outcome_latest.items():
        district, postal = fields(row)
        value = group(district, postal)
        checked(value, outcome_timestamps.get(identity))
        checked(value, detail_timestamps.get(identity), "detail")
        checked(value, listing_timestamps.get(identity), "listing")
        outcome = row.get("outcome")
        outcome_counter[outcome] += 1
        key = {"success": "session_saved_schools", "updated": "updated_schools",
               "unchanged": "unchanged_schools", "failed": "failed_records", "pending": "pending_records"}.get(outcome)
        if key:
            value[key] += 1
    for row in outcomes:
        if row.get("outcome") == "duplicate":
            group(*fields(row))["duplicate_records"] += 1
    for postal in sorted(expected):
        if not any(key[1] == postal for key in groups):
            group("", postal)

    evidence = _run_evidence(runs, reference)
    evidence["invalid_source_listing_rows"] = invalid_listing_rows
    evidence["source_pagination_complete"] = bool(source_scope_evidence) and all(
        scope.get("source_pagination_complete", scope.get("pagination_complete")) is True
        for scope in source_scope_evidence.values())
    last_counts = _object(runs[-1].get("counts")) if runs else {}
    historical_mismatch = sum(bool(row.get("metadata_year") and row.get("summary_year")
                                   and row["metadata_year"] != row["summary_year"]) for row in historical_rows)
    unverified_year_ids = {identity for identity, row in current.items()
                           if source == "udise" and (row.get("scope_year_verified") is False
                           or ("scope_year_verified" not in row and (not row.get("metadata_year")
                           or row.get("report_card_year") != row.get("metadata_year"))))}
    unverified_year_ids.update(identity for identity, row in outcome_latest.items()
                               if source == "udise" and row.get("scope_year_verified") is False
                               and row.get("outcome") not in {"failed", "pending", "duplicate"})
    year_unverified = len(unverified_year_ids)
    allowed_districts = {normalize(value) for value in reference.get("districts", {}).get(source, [])}
    outside_current = sum(
        bool(row.get("state") and normalize(row["state"]) != normalize(reference.get("state")))
        or bool(allowed_districts and row.get("district") and normalize(row["district"]) not in allowed_districts)
        for row in current.values())
    missing_geography = sum(not row.get("state") or not row.get("district") for row in current.values())
    if missing_geography:
        evidence["geography_verified"] = False
    evidence.update({key: sum(value[key] for value in groups.values()) for key in
                     ("missing_details", "missing_current_details", "failed_records", "pending_records", "missing_pincodes", "invalid_pincodes")})
    evidence["failed_records"] = max(evidence["failed_records"], last_counts.get("failed", 0))
    evidence["pending_records"] = max(evidence["pending_records"], last_counts.get("pending", 0),
                                       len(runs[-1].get("pending_identifiers", [])) if runs else 0)
    # Discovery/startup failures and unresolved tasks may have no school PIN.
    # Keep their counts visible in CSV without inventing a location or PIN.
    for key in ("failed_records", "pending_records"):
        unassigned = evidence[key] - sum(value[key] for value in groups.values())
        if unassigned > 0:
            group("", "_UNKNOWN")[key] += unassigned
            checked(group("", "_UNKNOWN"), _run_timestamp(runs[-1]) if runs else "")
    evidence["year_scope_unverified"] = year_unverified
    evidence["out_of_scope_records"] = historical.get("out_of_scope_count", 0) + outside_current
    status = coverage_status(evidence)
    reasons = list(reference.get("scope_limitations", []))
    if not evidence["expected_pincodes_complete"]:
        reasons.append("Complete authoritative postal inventory unavailable; coverage percentage has no verified denominator")
    if not evidence["pagination_complete"]:
        reasons.append("Complete usable current source listing not verified")
    if evidence["limited"]:
        reasons.append("Candidate-limited run cannot verify complete geographic coverage")
    if evidence["unfinished"] and runs:
        reasons.append("Latest run did not finish full source scope")
    if historical_mismatch:
        reasons.append("Historical UDISE summary academic year differs from metadata; requested-year report card used when available")
    if missing_geography:
        reasons.append(f"current_missing_geography={missing_geography}")
    if evidence["pending_discovery_scope_count"]:
        reasons.append(f"pending_discovery_scopes={evidence['pending_discovery_scope_count']}; school population remains unknown until authorized source searches finish")
    for key in ("missing_details", "missing_current_details", "invalid_source_listing_rows", "missing_pincodes", "invalid_pincodes", "failed_records", "pending_records", "year_scope_unverified", "out_of_scope_records"):
        if evidence.get(key):
            reasons.append(f"{key}={evidence[key]}")
    for (district, postal), row in groups.items():
        row["expected_pincode_status"] = ("VERIFIED_REFERENCE_NONEXHAUSTIVE" if postal in expected
                                           else "MISSING" if postal == "_MISSING"
                                           else "INVALID" if postal == "_INVALID"
                                           else "UNKNOWN" if postal == "_UNKNOWN" else "OBSERVED_SCHOOL_ONLY")
        row["coverage_status"] = status
        if status != "COMPLETE_VERIFIED" and row["source_listing_count"] == 0 and row["successfully_saved_schools"] == 0 and postal in expected:
            row["coverage_status"] = status if status in {"BLOCKED_CAPTCHA", "SOURCE_UNAVAILABLE"} else "PENDING"
        row["notes"] = (f"Listing basis: {listing_basis}. Saved schools are unique complete local detail records, including historical data. "
                        "Listing PIN group can use historical detail when no current detail; not currently reverified. "
                        "Supplied official response inputs are not a new live listing measurement. "
                        "Quarantined source listing rows lack usable identities and are separate from known school/detail/pending counts. "
                        "missing_details counts absent/incomplete local detail; missing_current_details counts listing IDs without usable current detail evidence. "
                        "Check timestamps describe recorded source activity, not report generation; historical-only rows can have no known source-check timestamp. "
                        "Session columns count latest outcome per school across supplied runs. No current zero-school conclusion from absent historical rows. "
                        + "; ".join(reasons))
    summary = {
        "location": location, "source": source, "coverage_status": status, "checked_at": checked_at,
        "report_generated_at": checked_at, "last_source_checked_at": _latest_timestamp(_run_timestamp(run) for run in runs),
        "historical_raw_schools": historical.get("raw_files", len(historical_rows)),
        "historical_unique_identifiers": len(baseline),
        "historical_incomplete_details": historical.get("incomplete_detail_records", 0),
        "historical_summary_year_mismatch": historical_mismatch,
        "historical_missing_pincodes": historical.get("missing_pincode_count", 0),
        "historical_invalid_pincodes": historical.get("invalid_pincode_count", 0),
        "historical_duplicate_records": historical.get("duplicate_extra_records", 0),
        "historical_missing_state": historical.get("missing_state_count", 0),
        "historical_missing_district": historical.get("missing_district_count", 0),
        "historical_out_of_scope": historical.get("out_of_scope_count", 0),
        "historical_listing_without_detail": historical.get("listing_without_detail_count", 0),
        "current_isolated_unique_identifiers": len(current),
        "current_out_of_scope_records": outside_current,
        "current_missing_state": sum(not row.get("state") for row in current.values()),
        "current_missing_district": sum(not row.get("district") for row in current.values()),
        "current_summary_year_mismatch": sum(bool(row.get("metadata_year") and row.get("summary_year")
                                                and row["metadata_year"] != row["summary_year"]) for row in current.values()),
        "new_identifiers_vs_historical": sorted(set(current) - set(baseline)),
        "new_source_listing_identifiers_vs_historical": sorted(live_listing_ids - set(baseline)),
        "new_supplied_listing_identifiers_vs_historical": sorted(supplied_listing_ids - set(baseline)),
        "current_source_listing_count": len(live_listing_ids)
        if any(_listing_origin(run) == "live_source_listing" for run, _, _, _ in listing_documents) else None,
        "supplied_official_listing_count": len(supplied_listing_ids),
        "source_rows_observed": source_rows_observed,
        "valid_source_identifier_count": len(live_listing_ids)
        if any(_listing_origin(run) == "live_source_listing" for run, _, _, _ in listing_documents) else None,
        "invalid_source_listing_rows": invalid_listing_rows,
        "quarantined_listing_rows": quarantined_listing_rows,
        "source_pagination_complete": evidence["source_pagination_complete"],
        "source_listing_without_any_local_detail": len(set(listed) - set(merged)),
        "missing_current_details": len(set(listed) - current_detail_ids),
        "listing_pins_from_current_details": listing_pin_basis["current_details"],
        "listing_pins_from_historical_details": listing_pin_basis["historical_details"],
        "listing_pins_unknown": listing_pin_basis["unknown"],
        "existing_identifiers_reobserved": len(set(current) & set(baseline)),
        "union_unique_identifiers": len(merged),
        "successfully_saved_schools": sum(row.get("details_complete") is True for row in merged.values()),
        "source_listing_count": len(listed), "listing_count_basis": listing_basis,
        "listing_evidence_runs": listing_evidence_runs,
        "verified_reference_pincode_count": len(expected),
        "observed_valid_pincode_count": len({fields(row)[1] for row in merged.values() if pin(fields(row)[1])}),
        "expected_complete_pincode_count": len(expected) if evidence["expected_pincodes_complete"] else None,
        "pincode_coverage_percent": None,
        "session_latest_outcomes": dict(outcome_counter),
        "session_run_counts": [run.get("counts", {}) for run in runs],
        "latest_run_status": runs[-1].get("status") if runs else None,
        "pending_discovery_scope_count": evidence["pending_discovery_scope_count"],
        "pending_discovery_scopes": evidence["pending_discovery_scopes"],
        "run_evidence": evidence, "limitations": reasons,
    }
    if status == "COMPLETE_VERIFIED" and expected:
        summary["pincode_coverage_percent"] = 100.0
    return {"rows": [groups[key] for key in sorted(groups)], "summary": summary}


def generate_location_reports(*, historical_audit, geography_reference, run_reports,
                              data_dirs=None, output_dir="reports/location_coverage", checked_at=None):
    """Write all three requested CSVs and summary from explicit evidence inputs."""
    historical_audit = _load(historical_audit)
    geography_reference = _load(geography_reference)
    checked_at = checked_at or datetime.now(timezone.utc).isoformat()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    result = {"checked_at": checked_at, "report_generated_at": checked_at, "locations": {}, "source_totals": {},
              "denominator_policy": geography_reference.get("denominator_policy"),
              "notes": "Historical local school counts are not current official source totals. No school data was modified."}
    for location in LOCATIONS:
        result["locations"][location] = {}
        rows = []
        reference = geography_reference["locations"][location]
        for source in SOURCES:
            records, read_errors = [], []
            for root in _list(_object(_object(data_dirs).get(location)).get(source)):
                found, errors = collect_records(root, source)
                records.extend(found)
                read_errors.extend(errors)
            reports = _list(_object(_object(run_reports).get(location)).get(source))
            coverage = build_coverage(location, source,
                                      historical=historical_audit["locations"][location][source],
                                      current_runs=reports, reference=reference,
                                      current_records=records, checked_at=checked_at)
            coverage["summary"]["current_read_errors"] = read_errors
            if read_errors:
                blocked = coverage["summary"]["coverage_status"] in {"BLOCKED_CAPTCHA", "SOURCE_UNAVAILABLE"}
                if not blocked:
                    coverage["summary"]["coverage_status"] = "PARTIAL"
                coverage["summary"]["limitations"].append("Unreadable isolated detail files")
                for row in coverage["rows"]:
                    row["coverage_status"] = coverage["summary"]["coverage_status"]
            result["locations"][location][source] = coverage["summary"]
            rows.extend(coverage["rows"])
        csv_path = output / f"{location}_coverage.csv"
        _preserve_report(csv_path)
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
    for source in SOURCES:
        scopes = [result["locations"][location][source] for location in LOCATIONS]
        result["source_totals"][source] = {
            key: sum(scope[key] for scope in scopes) for key in
            ("historical_raw_schools", "historical_unique_identifiers", "historical_incomplete_details",
             "historical_missing_pincodes", "historical_invalid_pincodes", "historical_duplicate_records",
             "current_isolated_unique_identifiers", "union_unique_identifiers", "successfully_saved_schools")}
        result["source_totals"][source]["new_identifiers_vs_historical"] = sum(len(scope["new_identifiers_vs_historical"]) for scope in scopes)
        result["source_totals"][source]["all_locations_complete_verified"] = all(scope["coverage_status"] == "COMPLETE_VERIFIED" for scope in scopes)
    _preserve_report(output / "summary.json")
    atomic_json(output / "summary.json", result)
    return result
