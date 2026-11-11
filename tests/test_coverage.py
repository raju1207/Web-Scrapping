"""Coverage must describe evidence, including absent or incomplete denominators."""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from school_scraping.coverage import (
    LOCATIONS, SOURCES, build_coverage, collect_records, coverage_status,
    generate_location_reports, raw_record,
)
from school_scraping.common import source_pin


def reference():
    return {"state": "CHANDIGARH", "districts": {"saras": ["CHANDIGARH"]},
            "verified_reference_pincodes": ["160017", "160036"],
            "expected_pincode_inventory_complete": False}


def record(identity="1234567", pincode="160017", **extra):
    return {"identifier": identity, "state": "CHANDIGARH", "district": "CHANDIGARH",
            "pincode": pincode, "raw_pincode": pincode, "details_complete": True, **extra}


def historical(records=None):
    records = [record()] if records is None else records
    return {"raw_files": len(records), "records": records,
            "listing_files": [{"records": copy.deepcopy(records)}]}


def finished_run():
    return {"status": "completed", "scope": {"limit": None},
            "listing_scopes": [{"pagination_complete": True, "limited": False}],
            "coverage": {"scope_complete": True, "listing_complete": True,
                         "details_complete": True, "expected_pincodes_checked": True},
            "counts": {"failed": 0, "pending": 0}}


class CoverageTests(unittest.TestCase):
    def test_complete_requires_every_independent_gate(self):
        good = {key: True for key in ("tested", "geography_verified", "pagination_complete",
                                      "details_complete", "expected_pincodes_complete", "expected_pincodes_checked")}
        self.assertEqual(coverage_status(good), "COMPLETE_VERIFIED")
        for key in ("geography_verified", "pagination_complete", "details_complete",
                    "expected_pincodes_complete", "expected_pincodes_checked"):
            with self.subTest(key=key):
                self.assertEqual(coverage_status({**good, key: False}), "PARTIAL")
        for key in ("limited", "unfinished", "failed_records", "pending_records", "missing_details",
                    "missing_current_details", "invalid_source_listing_rows", "missing_pincodes", "invalid_pincodes", "year_scope_unverified", "out_of_scope_records"):
            with self.subTest(key=key):
                self.assertEqual(coverage_status({**good, key: 1}), "PARTIAL")

    def test_untested_captcha_and_source_unavailable_are_distinct(self):
        self.assertEqual(coverage_status({}), "NOT_VERIFIED")
        self.assertEqual(coverage_status({"blocked_captcha": True}), "BLOCKED_CAPTCHA")
        self.assertEqual(coverage_status({"source_unavailable": True}), "SOURCE_UNAVAILABLE")

    def test_historical_inventory_is_not_current_complete_source_total(self):
        value = build_coverage("chandigarh", "saras", historical=historical(), current_runs=[], reference=reference())
        self.assertEqual(value["summary"]["coverage_status"], "NOT_VERIFIED")
        self.assertEqual(value["summary"]["historical_raw_schools"], 1)
        self.assertIsNone(value["summary"]["pincode_coverage_percent"])
        empty = next(row for row in value["rows"] if row["pincode"] == "160036")
        self.assertEqual(empty["coverage_status"], "PENDING")
        self.assertIn("No current zero-school conclusion", empty["notes"])

    def test_completed_unlimited_run_still_needs_complete_postal_denominator(self):
        value = build_coverage("chandigarh", "saras", historical=historical(),
                               current_runs=[finished_run()], reference=reference())
        self.assertEqual(value["summary"]["coverage_status"], "PARTIAL")
        self.assertIsNone(value["summary"]["expected_complete_pincode_count"])

    def test_bounded_or_interrupted_run_never_becomes_complete(self):
        ref = {**reference(), "expected_pincode_inventory_complete": True}
        for change in ({"scope": {"limit": 2}}, {"status": "interrupted"}, {"status": "time_budget_reached"}):
            run = {**finished_run(), **change}
            value = build_coverage("chandigarh", "saras", historical=historical(), current_runs=[run], reference=ref)
            self.assertEqual(value["summary"]["coverage_status"], "PARTIAL")

    def test_historical_and_repeated_current_school_ids_are_unioned(self):
        run = finished_run()
        run["record_outcomes"] = [{"affiliation_number": "1234567", "actual_pincode": "160017",
                                   "district": "CHANDIGARH", "outcome": "success"}]
        later = copy.deepcopy(run)
        later["record_outcomes"][0]["outcome"] = "unchanged"
        value = build_coverage("chandigarh", "saras", historical=historical(), current_runs=[run, later],
                               reference=reference(), current_records=[record(), record(), record("7654321")])
        summary = value["summary"]
        self.assertEqual(summary["union_unique_identifiers"], 2)
        self.assertEqual(summary["new_identifiers_vs_historical"], ["7654321"])
        self.assertEqual(summary["existing_identifiers_reobserved"], 1)
        self.assertEqual(summary["session_latest_outcomes"], {"unchanged": 1})
        self.assertEqual(sum(row["duplicate_records"] for row in value["rows"]), 0)

    def test_nested_candidate_manifest_retains_ids_and_missing_details(self):
        run = finished_run()
        run["candidate_manifest"] = [{"state": "CHANDIGARH", "district": "CHANDIGARH",
                                      "listing_data": {"affiliation_number": "9999999"}}]
        value = build_coverage("chandigarh", "saras", historical=historical([]), current_runs=[run], reference=reference())
        row = next(row for row in value["rows"] if row["pincode"] == "_UNKNOWN")
        self.assertEqual(row["source_listing_count"], 1)
        self.assertEqual(row["missing_details"], 1)
        self.assertEqual(value["summary"]["source_listing_count"], 1)
        self.assertEqual(value["summary"]["new_source_listing_identifiers_vs_historical"], ["9999999"])
        self.assertEqual(value["summary"]["new_identifiers_vs_historical"], [])

    def test_quarantined_source_rows_are_counted_without_fabricated_school_tasks(self):
        records = [record("1111111"), record("2222222", "160036")]
        run = {**finished_run(), "status": "partial_source_data", "candidate_manifest": records,
               "finished_at": "2026-11-11T02:05:00+00:00", "counts": {"invalid_listing_rows": 1},
               "listing_scopes": [{"state": "CHANDIGARH", "district": None, "source_rows_observed": 3,
                                   "valid_source_identifier_count": 2, "parseable_rows_observed": 2,
                                   "invalid_source_listing_rows": 1, "source_pagination_complete": True,
                                   "pagination_complete": True,
                                   "unparsed_listing_rows": [{"reason": "invalid_source_identifier", "listing_raw_columns": ["XXXXXXX"]}]}],
               "coverage": {"source_pagination_complete": True, "listing_complete": False,
                            "details_complete": True, "scope_complete": False}}
        value = build_coverage("chandigarh", "saras", historical=historical([]), current_runs=[run],
                               current_records=records, reference={**reference(), "expected_pincode_inventory_complete": True})
        summary = value["summary"]
        self.assertEqual((summary["source_rows_observed"], summary["valid_source_identifier_count"],
                          summary["invalid_source_listing_rows"]), (3, 2, 1))
        self.assertTrue(summary["source_pagination_complete"])
        self.assertEqual(summary["source_listing_count"], 2)
        self.assertEqual(summary["coverage_status"], "PARTIAL")
        self.assertEqual(summary["run_evidence"]["pending_records"], 0)
        unknown = next(row for row in value["rows"] if row["pincode"] == "_UNKNOWN")
        self.assertEqual((unknown["invalid_source_listing_rows"], unknown["source_listing_count"],
                          unknown["failed_records"], unknown["pending_records"]), (1, 0, 0, 0))
        self.assertEqual(unknown["last_listing_checked_at"], run["finished_at"])

    def test_latest_source_scope_snapshot_replaces_repeated_quarantine_count(self):
        item = record()
        first = {**finished_run(), "started_at": "2026-11-10T02:00:00+00:00", "candidate_manifest": [item],
                 "listing_scopes": [{"state": "CHANDIGARH", "district": None, "source_rows_observed": 2,
                                     "invalid_source_listing_rows": 1, "pagination_complete": True}]}
        first["coverage"] = {"listing_complete": False, "scope_complete": False}
        repeated = copy.deepcopy(first)
        repeated["started_at"] = "2026-11-11T02:00:00+00:00"
        value = build_coverage("chandigarh", "saras", historical=historical([]), current_runs=[first, repeated],
                               current_records=[item], reference=reference())
        self.assertEqual(value["summary"]["invalid_source_listing_rows"], 1)
        self.assertEqual(value["summary"]["source_rows_observed"], 2)
        clean = {**finished_run(), "started_at": "2026-11-11T03:00:00+00:00", "candidate_manifest": [item],
                 "listing_scopes": [{"state": "CHANDIGARH", "district": None, "source_rows_observed": 1,
                                     "invalid_source_listing_rows": 0, "pagination_complete": True}]}
        cleaned = build_coverage("chandigarh", "saras", historical=historical([]), current_runs=[first, repeated, clean],
                                 current_records=[item], reference=reference())
        self.assertEqual(cleaned["summary"]["invalid_source_listing_rows"], 0)
        self.assertEqual(cleaned["summary"]["source_rows_observed"], 1)

    def test_partial_manifests_accumulate_and_fresh_verified_snapshot_supersedes(self):
        original = {**finished_run(), "run_id": "initial", "started_at": "2026-11-10T01:00:00+00:00",
                    "candidate_manifest": [record("1111111"), record("2222222")]}
        original["coverage"] = {**original["coverage"], "listing_complete": False, "scope_complete": False}
        recovery = {**finished_run(), "run_id": "resume", "started_at": "2026-11-10T02:00:00+00:00",
                    "partial_recovery": True, "candidate_manifest": [record("2222222"), record("3333333")]}
        recovery["coverage"] = {**recovery["coverage"], "listing_complete": False, "scope_complete": False}
        partial = build_coverage("chandigarh", "saras", historical=historical([]),
                                 current_runs=[recovery, original], reference=reference())
        self.assertEqual(partial["summary"]["source_listing_count"], 3)
        self.assertEqual(partial["summary"]["listing_count_basis"], "current_partial_manifest_union")
        self.assertEqual([row["run_id"] for row in partial["summary"]["listing_evidence_runs"]], ["initial", "resume"])
        fresh = {**finished_run(), "run_id": "fresh", "started_at": "2026-11-10T03:00:00+00:00",
                 "candidate_manifest": [record("3333333")]}
        verified = build_coverage("chandigarh", "saras", historical=historical([]),
                                  current_runs=[original, recovery, fresh], reference=reference())
        self.assertEqual(verified["summary"]["source_listing_count"], 1)
        self.assertEqual(verified["summary"]["listing_count_basis"], "current_verified_listing_snapshot")
        # An explicitly verified empty scoped manifest is a current zero listing,
        # and must not fall back to an unrelated unfiltered listing or history.
        fresh["candidate_manifest"] = []
        fresh["listing_data"] = [record("9999999")]
        zero = build_coverage("chandigarh", "saras", historical=historical(), current_runs=[fresh], reference=reference())
        self.assertEqual(zero["summary"]["current_source_listing_count"], 0)

    def test_detail_failure_after_available_listing_is_partial_source_startup_failure_is_unavailable(self):
        detail_failure = {"status": "failed", "candidate_manifest": [record()], "counts": {"failed": 1}}
        partial = build_coverage("chandigarh", "udise", historical=historical(), current_runs=[detail_failure], reference=reference())
        self.assertEqual(partial["summary"]["coverage_status"], "PARTIAL")
        startup = {"status": "failed", "error": "Official catalog unavailable", "counts": {"failed": 1}}
        unavailable = build_coverage("chandigarh", "udise", historical=historical(), current_runs=[startup], reference=reference())
        self.assertEqual(unavailable["summary"]["coverage_status"], "SOURCE_UNAVAILABLE")
        saras_startup = build_coverage("chandigarh", "saras", historical=historical(),
                                      current_runs=[{"status": "startup_failure"}], reference=reference())
        self.assertEqual(saras_startup["summary"]["coverage_status"], "SOURCE_UNAVAILABLE")

    def test_historical_pin_group_does_not_imply_current_detail_validation(self):
        records = [record("1111111"), record("2222222", "160036")]
        run = {**finished_run(), "candidate_manifest": records}
        result = build_coverage("chandigarh", "saras", historical=historical(records), current_runs=[run],
                                reference=reference(), current_records=[record("1111111")])
        summary = result["summary"]
        self.assertEqual(summary["missing_current_details"], 1)
        self.assertEqual((summary["listing_pins_from_current_details"], summary["listing_pins_from_historical_details"],
                          summary["listing_pins_unknown"]), (1, 1, 0))
        historic = next(row for row in result["rows"] if row["pincode"] == "160036")
        self.assertEqual(historic["missing_details"], 0)
        self.assertEqual(historic["missing_current_details"], 1)
        self.assertIn("not currently reverified", historic["notes"])

    def test_failed_and_pending_extractions_without_source_pin_fields_are_unknown(self):
        run = {**finished_run(), "record_outcomes": [
            {"affiliation_number": "1111111", "outcome": "failed", "actual_pincode": None,
             "pincode_status": "missing", "pincode_raw": {}, "pincode_field": []},
            {"affiliation_number": "2222222", "outcome": "pending", "actual_pincode": None},
        ]}
        result = build_coverage("chandigarh", "saras", historical=historical([]), current_runs=[run], reference=reference())
        unknown = next(row for row in result["rows"] if row["pincode"] == "_UNKNOWN")
        self.assertEqual((unknown["failed_records"], unknown["pending_records"]), (1, 1))
        self.assertFalse(any(row["pincode"] == "_MISSING" for row in result["rows"]))
        self.assertEqual(result["summary"]["run_evidence"]["missing_pincodes"], 0)

    def test_partial_saved_list_success_does_not_clear_whole_scope_captcha(self):
        scopes = [{"state": "DELHI", "district": str(index)} for index in range(16)]
        blocked = {"status": "manual_intervention", "error": "CAPTCHA required", "pending_discovery": scopes,
                   "started_at": "2026-11-10T01:00:00+00:00", "run_id": "blocked"}
        sample = {**finished_run(), "started_at": "2026-11-10T02:00:00+00:00", "run_id": "sample",
                  "candidate_manifest": [record()], "counts": {"success": 1}}
        sample["coverage"] = {"listing_complete": False, "details_complete": True, "scope_complete": False}
        value = build_coverage("delhi", "udise", historical=historical(), current_runs=[blocked, sample], reference=reference())
        self.assertEqual(value["summary"]["latest_run_status"], "completed")
        self.assertEqual(value["summary"]["coverage_status"], "BLOCKED_CAPTCHA")
        self.assertEqual(value["summary"]["pending_discovery_scope_count"], 16)
        self.assertEqual(value["summary"]["run_evidence"]["pending_records"], 0)
        full = {**finished_run(), "started_at": "2026-11-10T03:00:00+00:00"}
        resolved = build_coverage("delhi", "udise", historical=historical(), current_runs=[blocked, sample, full], reference=reference())
        self.assertFalse(resolved["summary"]["run_evidence"]["blocked_captcha"])
        self.assertEqual(resolved["summary"]["pending_discovery_scope_count"], 0)

    def test_supplied_official_inputs_are_not_relabelled_as_current_live_listings(self):
        saved = {**finished_run(), "discovery_scope": "Supplied official lists only; statewide completeness is not established",
                 "started_at": "2026-11-11T02:00:00+00:00", "finished_at": "2026-11-11T02:05:00+00:00",
                 "candidate_manifest": [record("1111111"), record("2222222", "160036")],
                 "record_outcomes": [{"udise_code": "1111111", "district": "CHANDIGARH",
                                      "actual_pincode": "160017", "outcome": "success"},
                                     {"udise_code": "2222222", "district": "CHANDIGARH",
                                      "actual_pincode": "160036", "outcome": "pending"}]}
        saved["listing_scopes"] = []
        saved["coverage"] = {"scope_complete": False, "listing_complete": False, "details_complete": False}
        value = build_coverage("chandigarh", "udise", historical=historical([]), current_runs=[saved],
                               current_records=[record("1111111")], reference=reference())
        summary = value["summary"]
        self.assertEqual(summary["source_listing_count"], 2)
        self.assertIsNone(summary["current_source_listing_count"])
        self.assertEqual(summary["supplied_official_listing_count"], 2)
        self.assertEqual(summary["listing_count_basis"], "supplied_official_response_inputs")
        self.assertEqual(summary["new_source_listing_identifiers_vs_historical"], [])
        self.assertEqual(summary["new_supplied_listing_identifiers_vs_historical"], ["1111111", "2222222"])
        pending = next(row for row in value["rows"] if row["pincode"] == "160036")
        self.assertEqual(pending["last_listing_checked_at"], "")
        self.assertEqual(pending["last_checked_timestamp"], "")

    def test_saved_udise_record_without_verified_year_cannot_be_complete(self):
        ref = {**reference(), "expected_pincode_inventory_complete": True}
        result = build_coverage("chandigarh", "udise", historical=historical([]), current_runs=[finished_run()],
                                reference=ref, current_records=[record()])
        self.assertEqual(result["summary"]["run_evidence"]["year_scope_unverified"], 1)
        self.assertEqual(result["summary"]["coverage_status"], "PARTIAL")

    def test_current_summary_pin_retains_its_own_year_and_blocks_annual_completeness(self):
        raw = {"metadata": {"udise_code": "04000000001", "academic_year": "2025-26"},
               "school_summary": {"pincode": 160017, "sessionYear": "2026-27"},
               "report_card": {"pincode": None, "yearDesc": "2025-26"}}
        current = raw_record("udise", raw)
        self.assertEqual(current["pincode"], "160017")
        self.assertEqual(current["pincode_academic_year"], "2026-27")
        self.assertFalse(current["scope_year_verified"])
        result = build_coverage("chandigarh", "udise", historical=historical([]), current_runs=[finished_run()],
                                reference=reference(), current_records=[current])
        self.assertEqual(result["summary"]["run_evidence"]["year_scope_unverified"], 1)
        raw["school_summary"]["sessionYear"] = "2025-26"
        self.assertTrue(raw_record("udise", raw)["scope_year_verified"])
        # Annual report-card fields may inherit their requested metadata context.
        raw["report_card"] = {"pincode": 160036}
        self.assertTrue(raw_record("udise", raw)["scope_year_verified"])

    def test_missing_invalid_and_failed_details_are_not_zero_matches(self):
        records = [record(pincode=None), record("7654321", None, raw_pincode="160")]
        run = finished_run()
        run["record_outcomes"] = [{"affiliation_number": "9999999", "outcome": "failed",
                                   "reason": "Detail HTTP 503", "district": "CHANDIGARH"}]
        run["counts"]["failed"] = 1
        value = build_coverage("chandigarh", "saras", historical=historical(records), current_runs=[run], reference=reference())
        evidence = value["summary"]["run_evidence"]
        self.assertEqual((evidence["missing_pincodes"], evidence["invalid_pincodes"], evidence["failed_records"]), (1, 1, 1))
        self.assertEqual(value["summary"]["coverage_status"], "PARTIAL")

    def test_captcha_preserves_pending_and_does_not_imply_no_schools(self):
        run = {"status": "manual_intervention", "reason": "CAPTCHA required", "counts": {"pending": 8},
               "pending_identifiers": ["1234567"]}
        value = build_coverage("chandigarh", "udise", historical=historical(), current_runs=[run], reference=reference())
        self.assertEqual(value["summary"]["coverage_status"], "BLOCKED_CAPTCHA")
        self.assertEqual(value["summary"]["run_evidence"]["pending_records"], 8)
        self.assertEqual(value["summary"]["historical_raw_schools"], 1)
        self.assertEqual(sum(row["pending_records"] for row in value["rows"]), 8)

    def test_raw_projection_uses_dedicated_pin_and_requested_year_card(self):
        raw = {"metadata": {"udise_code": "04000000001", "academic_year": "2025-26"},
               "school_summary": {"pincode": 160036, "sessionYear": "2026-27"},
               "report_card": {"pincode": 160017, "yearDesc": "2025-26"}}
        projected = raw_record("udise", raw)
        self.assertEqual(projected["pincode"], "160017")
        self.assertEqual(projected["pincode_source"], "report_card.pincode")
        self.assertEqual(projected["summary_year"], "2026-27")
        saras = raw_record("saras", {"school_details": {"Pin Code": "160036", "Postal Address": "160017"}})
        self.assertEqual(saras["pincode"], "160036")

    def test_source_field_formatting_and_conflicts_match_scraper_policy(self):
        for value in (160017, " 160017 ", "160 017", "160-017", "160\u00a0017"):
            with self.subTest(value=value):
                self.assertEqual(source_pin(value), "160017")
                row = raw_record("saras", {"school_details": {"Pin Code": value}})
                self.assertEqual((row["pincode"], row["pincode_status"]), ("160017", "valid"))
        for value in (True, 160017.0, "PIN160017", "160017 address", "160--017", "016001", {"pin": "160017"}):
            with self.subTest(value=value):
                self.assertIsNone(source_pin(value))
        conflicting = raw_record("saras", {"school_details": {"Pin Code": ["160017", "160036"]}})
        self.assertIsNone(conflicting["pincode"])
        self.assertEqual(conflicting["pincode_status"], "conflicting")
        fallback = raw_record("udise", {"report_card": {"pincode": ""}, "school_summary": {"pincode": "160 017"}})
        self.assertEqual(fallback["pincode"], "160017")
        self.assertEqual(fallback["pincode_source"], "school_summary.pincode")

    def test_missing_sections_and_year_mismatch_are_explicit(self):
        row = record(details_complete=False, metadata_year="2025-26", summary_year="2026-27", report_card_year=None)
        value = build_coverage("chandigarh", "udise", historical=historical([row]),
                               current_runs=[finished_run()], current_records=[row], reference=reference())
        self.assertEqual(value["summary"]["historical_summary_year_mismatch"], 1)
        self.assertEqual(value["summary"]["run_evidence"]["year_scope_unverified"], 1)
        self.assertEqual(value["summary"]["run_evidence"]["missing_details"], 1)

    def test_current_wrong_geography_cannot_be_complete(self):
        ref = {**reference(), "expected_pincode_inventory_complete": True}
        value = build_coverage("chandigarh", "saras", historical=historical([]), current_runs=[finished_run()],
                               current_records=[record(state="MAHARASHTRA")], reference=ref)
        self.assertEqual(value["summary"]["current_out_of_scope_records"], 1)
        self.assertEqual(value["summary"]["coverage_status"], "PARTIAL")

    def test_source_district_names_are_preserved(self):
        row = record(state="MAHARASHTRA", district="MUMBAI II")
        ref = {"state": "MAHARASHTRA", "districts": {"udise": ["MUMBAI II", "MUMBAI (SUBURBAN)"]}}
        value = build_coverage("mumbai", "udise", historical=historical([row]), current_runs=[], reference=ref)
        self.assertEqual(value["rows"][0]["district"], "MUMBAI II")

    def test_report_generation_does_not_relabel_old_or_unknown_source_checks(self):
        original = record("1111111")
        untested = record("2222222", "160036")
        run = {**finished_run(), "started_at": "2026-11-10T01:00:00+00:00",
               "finished_at": "2026-11-10T01:05:00+00:00", "candidate_manifest": [original],
               "record_outcomes": [{"affiliation_number": "1111111", "district": "CHANDIGARH",
                                    "actual_pincode": "160017", "outcome": "success"}]}
        generated = "2026-11-11T02:00:00+00:00"
        value = build_coverage("chandigarh", "saras", historical=historical([original, untested]),
                               current_runs=[run], current_records=[original], reference=reference(),
                               checked_at=generated)
        checked = next(row for row in value["rows"] if row["pincode"] == "160017")
        historical_only = next(row for row in value["rows"] if row["pincode"] == "160036")
        self.assertEqual(checked["report_generated_at"], generated)
        self.assertEqual(checked["last_checked_timestamp"], run["finished_at"])
        self.assertEqual(checked["last_detail_checked_at"], run["finished_at"])
        self.assertEqual(historical_only["last_checked_timestamp"], "")
        self.assertEqual(value["summary"]["last_source_checked_at"], run["finished_at"])
        self.assertEqual(value["summary"]["report_generated_at"], generated)

    def test_cached_skip_does_not_advance_known_detail_fetch_timestamp(self):
        item = record()
        fetched = {**finished_run(), "finished_at": "2026-11-10T01:05:00Z",
                   "record_outcomes": [{"affiliation_number": item["identifier"], "district": "CHANDIGARH",
                                        "actual_pincode": item["pincode"], "outcome": "success"}]}
        cached = {**finished_run(), "started_at": "2026-11-11T02:00:00+00:00",
                  "finished_at": "2026-11-11T02:05:00+00:00", "candidate_manifest": [item],
                  "record_outcomes": [{"affiliation_number": item["identifier"], "district": "CHANDIGARH",
                                       "actual_pincode": item["pincode"], "outcome": "skipped",
                                       "detail_source": "raw_cache"}]}
        value = build_coverage("chandigarh", "saras", historical=historical(),
                               current_runs=[fetched, cached], current_records=[item], reference=reference())
        row = next(row for row in value["rows"] if row["pincode"] == item["pincode"])
        self.assertEqual(row["last_detail_checked_at"], fetched["finished_at"])
        self.assertEqual(row["last_listing_checked_at"], cached["finished_at"])
        self.assertEqual(row["last_checked_timestamp"], cached["finished_at"])

    def test_report_orchestration_preserves_inputs_and_versions_existing_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audit = {"locations": {loc: {src: historical() for src in SOURCES} for loc in LOCATIONS}}
            ref = {"locations": {loc: reference() for loc in LOCATIONS}}
            original = json.dumps([audit, ref], sort_keys=True)
            output = root / "reports"
            output.mkdir()
            (output / "summary.json").write_bytes(b'{"previous":true}')
            result = generate_location_reports(historical_audit=audit, geography_reference=ref,
                                                run_reports={}, output_dir=output)
            self.assertEqual(json.dumps([audit, ref], sort_keys=True), original)
            self.assertEqual(result["source_totals"]["saras"]["union_unique_identifiers"], 3)
            for loc in LOCATIONS:
                with (output / f"{loc}_coverage.csv").open(newline="", encoding="utf-8") as handle:
                    self.assertTrue(list(csv.DictReader(handle)))
            self.assertEqual(next((output / "_backups").glob("summary_*.json")).read_bytes(), b'{"previous":true}')

    def test_isolated_reader_ignores_errors_helpers_and_keeps_input_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = root / "CBSE_SARAS/chandigarh/schools/one.json"
            raw.parent.mkdir(parents=True)
            value = {"metadata": {"affiliation_number": "1234567"}, "school_details": {"Pin Code": "160017"}}
            raw.write_text(json.dumps(value), encoding="utf-8")
            before = raw.read_bytes()
            (raw.parent / "_helper.json").write_text("{}", encoding="utf-8")
            (raw.parent / "bad.json").write_text("{", encoding="utf-8")
            rows, errors = collect_records(root, "saras")
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(errors), 1)
            self.assertEqual(raw.read_bytes(), before)

    def test_unreadable_local_detail_does_not_clear_discovery_captcha_blocker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            raw = data / "CHANDIGARH/2025-26/invalid.json"
            raw.parent.mkdir(parents=True)
            raw.write_text("{", encoding="utf-8")
            audit = {"locations": {loc: {src: historical() for src in SOURCES} for loc in LOCATIONS}}
            ref = {"locations": {loc: reference() for loc in LOCATIONS}}
            blocked = {"status": "manual_intervention", "error": "Manual CAPTCHA required",
                       "pending_discovery": [{"state": "CHANDIGARH", "district": "CHANDIGARH"}]}
            result = generate_location_reports(historical_audit=audit, geography_reference=ref,
                                                run_reports={"chandigarh": {"udise": [blocked]}},
                                                data_dirs={"chandigarh": {"udise": [data]}}, output_dir=root / "reports")
            source = result["locations"]["chandigarh"]["udise"]
            self.assertEqual(source["coverage_status"], "BLOCKED_CAPTCHA")
            self.assertEqual(len(source["current_read_errors"]), 1)
            with (root / "reports/chandigarh_coverage.csv").open(newline="", encoding="utf-8") as handle:
                self.assertTrue(all(row["coverage_status"] == "BLOCKED_CAPTCHA" for row in csv.DictReader(handle)
                                    if row["source"] == "udise"))


if __name__ == "__main__":
    unittest.main()
