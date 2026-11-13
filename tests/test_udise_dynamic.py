import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import udise_dynamic_scraper as udise
from school_scraping.common import ManualIntervention


def school(code, state="Punjab", district="Patiala", pincode="147001"):
    return {"udiseschCode": code, "schoolName": "School " + code,
            "stateName": state, "districtName": district, "pincode": pincode}


def response(value=None, status=200, text=None):
    answer = Mock()
    answer.status_code = status
    answer.text = text if text is not None else json.dumps(value)
    answer.json.return_value = value
    if status >= 400:
        answer.raise_for_status.side_effect = requests.HTTPError(str(status))
    return answer


class ClientTests(unittest.TestCase):
    def test_rate_limit_stops_without_retry(self):
        on_event = Mock()
        answer = response(status=429, text="Too many requests")
        client = udise.UdiseClient(delay=0, retries=3, on_event=on_event)
        try:
            with patch.object(client.session, "get", return_value=answer) as get:
                with self.assertRaises(ManualIntervention):
                    client.get_json("school/profile", {"udiseSchCode": "03000000001", "yearId": 12})
            get.assert_called_once()
            answer.json.assert_not_called()
            answer.raise_for_status.assert_not_called()
            on_event.assert_called_once_with("rate_limit_encountered", identifier="03000000001", http_status=429)
        finally:
            client.close()

    def test_captcha_never_retries(self):
        for answer in [response({"status": False, "error": "Invalid Captcha"}),
                       response(status=403, text="Access denied"),
                       response(text="<input name='captcha'>")]:
            with self.subTest(body=answer.text):
                client = udise.UdiseClient(delay=0, retries=5)
                with patch.object(client.session, "get", return_value=answer) as get:
                    with self.assertRaises(ManualIntervention):
                        client.get_json("school/profile", {"udiseSchCode": "07000000001"})
                self.assertEqual(get.call_count, 1)
                client.close()

    def test_transient_server_error_recovers_with_bounded_retry(self):
        client = udise.UdiseClient(delay=0, retries=2)
        with patch.object(client.session, "get", side_effect=[
            response(status=503, text="Service unavailable"),
            response({"status": True, "data": {"name": "School"}}),
        ]) as get:
            result = client.get_json("school/profile")
        self.assertTrue(result["success"])
        self.assertEqual(get.call_count, 2)
        client.close()

    def test_timeout_retries_stop_at_configured_bound(self):
        client = udise.UdiseClient(delay=0, retries=2)
        with patch.object(client.session, "get", side_effect=requests.Timeout) as get:
            result = client.get_json("school/profile")
        self.assertFalse(result["success"])
        self.assertEqual(get.call_count, 3)
        client.close()

    def test_permanent_http_error_is_not_retried(self):
        client = udise.UdiseClient(delay=0, retries=3)
        with patch.object(client.session, "get", return_value=response(status=404, text="Not found")) as get:
            result = client.get_json("school/profile")
        self.assertFalse(result["success"])
        self.assertEqual(get.call_count, 1)
        client.close()

    def test_success_preserves_source_response_and_omits_sensitive_query(self):
        source = {"status": True, "data": {"missing": None, "name": "School"}, "extra": [0, ""]}
        client = udise.UdiseClient(delay=0, retries=0)
        with patch.object(client.session, "get", return_value=response(source)):
            result = client.get_json("school/profile", {
                "udiseSchCode": "07000000001", "yearId": 0,
                "captcha": "private-value", "token": "private-token"})
        self.assertIs(result["response"], source)
        self.assertIn("yearId=0", result["url"])
        self.assertNotIn("private", result["url"])
        client.close()

    def test_year_id_zero_is_valid_and_mismatch_rejected(self):
        client = udise.UdiseClient(delay=0)
        with patch.object(client, "catalog", return_value=[{"yearDesc": "2026-27", "yearId": 0}]):
            self.assertEqual(client.verify_year("2026-27"), 0)
            self.assertEqual(client.verify_year("2026-27", 0), 0)
            with self.assertRaises(ValueError):
                client.verify_year("2026-27", 12)
            with self.assertRaises(ValueError):
                client.verify_year("2025-26")
        client.close()

    def test_build_record_preserves_raw_contract_and_source_values(self):
        client = Mock()
        values = [{"status": True, "data": {"value": index, "missing": None}}
                  for index in range(4)] + [{"status": True, "data": []}]
        client.get_json.side_effect = [{"success": True, "response": value} for value in values]
        row = school("03000000001")
        row["schoolId"] = 44
        record = udise.build_record(client, row, SimpleNamespace(academic_year="2026-27", year_id=0))
        self.assertEqual(set(record), {
            "metadata", "school_summary", "school_profile", "student_teacher_statistics",
            "infrastructure_facilities", "report_card", "school_history", "_api_status"})
        self.assertIs(record["school_summary"], row)
        self.assertEqual(record["metadata"]["year_id"], 0)
        self.assertEqual(record["metadata"]["udise_code"], "03000000001")
        self.assertEqual(record["school_history"], [])
        self.assertIs(record["school_profile"], values[0]["data"])
        for call in client.get_json.call_args_list[:4]:
            self.assertEqual(call.args[1]["yearId"], 0)

    def test_build_record_failure_is_retryable_instead_of_successful_raw(self):
        client = Mock()
        client.get_json.return_value = {"success": False, "error": "Timeout"}
        with self.assertRaisesRegex(RuntimeError, "Incomplete section"):
            udise.build_record(client, school("03000000001"),
                               SimpleNamespace(academic_year="2025-26", year_id=12))


class ScopeTests(unittest.TestCase):
    def test_saved_rows_filter_states_districts_and_only_verified_pin_values(self):
        rows = [school("03000000001"), school("03000000002", district="Ludhiana"),
                school("06000000001", "Haryana", "Ambala", "134001"),
                school("07000000001", "Delhi", "North", "110001")]
        selected = list(udise.selected_rows(rows, ["Punjab", "Haryana"], [], []))
        self.assertEqual(len(selected), 3)
        selected = list(udise.selected_rows(rows, ["HARYANA"], ["ambala"], ["134001"]))
        self.assertEqual([row["udiseschCode"] for row in selected], ["06000000001"])
        missing_pin = school("06000000002", "Haryana", "Ambala", None)
        self.assertEqual(list(udise.selected_rows([missing_pin], ["Haryana"], [], ["134001"])), [missing_pin])

    def test_load_lists_rejects_incomplete_pagination_and_source_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "list.json"
            path.write_text(json.dumps({"status": True, "data": {
                "content": [school("03000000001")], "totalElements": 2}}))
            with self.assertRaisesRegex(ValueError, "paginated/incomplete"):
                udise.load_lists([path])
            path.write_text(json.dumps({"status": False, "error": "Invalid Captcha"}))
            with self.assertRaises(ManualIntervention):
                udise.load_lists([path])

    def test_dry_run_uses_saved_rows_without_network_or_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "list.json"
            path.write_text(json.dumps({"status": True, "data": [school("03000000001")]}))
            output = io.StringIO()
            with patch.object(udise, "UdiseClient") as client, \
                    patch.object(udise, "write_report") as report, contextlib.redirect_stdout(output):
                code = udise.main(["--state", "Punjab", "--dry-run", "--school-list", str(path),
                                   "--data-dir", str(root / "absent"), "--report-dir", str(root / "reports")])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["selected_saved_rows"], 1)
            self.assertFalse((root / "absent").exists())
            self.assertFalse((root / "reports").exists())
            client.assert_not_called()
            report.assert_not_called()

    def test_manual_search_requires_exact_pincode_and_region_query(self):
        task = {"stateId": 3, "districtId": 30, "yearId": 0}
        url = "https://kys.udiseplus.gov.in/web-app/api/search-school/by-region?stateId=3&districtId=30&yearId=0"
        self.assertTrue(udise.task_matches(url, task))
        self.assertFalse(udise.task_matches(url.replace("districtId=30", "districtId=31"), task))
        pin_url = "https://kys.udiseplus.gov.in/web-app/api/search-schools?searchType=4&searchParam=134001"
        self.assertTrue(udise.task_matches(pin_url, {"pincode": "134001"}))
        self.assertFalse(udise.task_matches(pin_url, {"pincode": "147001"}))

    def test_manual_search_rejects_matching_query_from_another_origin(self):
        url = "https://example.invalid/web-app/api/search-schools?searchType=4&searchParam=134001"
        self.assertFalse(udise.task_matches(url, {"pincode": "134001"}))

    def test_no_manual_access_records_pending_tasks_without_opening_browser(self):
        report = {}
        tasks = [{"stateId": 3, "districtId": 30, "yearId": 12}]
        with self.assertRaises(ManualIntervention):
            udise.manual_discovery(tasks, 0, report)
        self.assertEqual(report["pending_discovery"], tasks)

    def test_offline_collection_deduplicates_and_resumes_by_source_id(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "list.json"
            rows = [school("03000000001"), school("06000000001", "Haryana", "Ambala", "134001"),
                    school("03000000001")]
            path.write_text(json.dumps({"status": True, "data": rows}))
            arguments = ["--state", "Punjab", "--state", "Haryana", "--school-list", str(path),
                         "--data-dir", str(root / "data"), "--report-dir", str(root / "reports")]
            client = Mock()
            client.verify_year.return_value = 12
            client.catalog.return_value = [{"stateName": "Punjab", "stateId": 3},
                                           {"stateName": "Haryana", "stateId": 6}]
            client.get_json.return_value = {"success": True, "response": {
                "status": True, "data": {"pincode": 134001, "name": "School"}}}
            with patch.object(udise, "UdiseClient", return_value=client), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(udise.main(arguments), 0)
            reports = list((root / "reports/udise").glob("*.json"))
            first = json.loads(reports[0].read_text())
            self.assertEqual(first["counts"]["success"], 2)
            self.assertEqual(first["counts"]["duplicate"], 1)
            raw = list((root / "data").glob("*/2025-26/*.json"))
            self.assertEqual(len(raw), 2)
            saved_bytes = {str(saved): saved.read_bytes() for saved in raw}
            client.get_json.reset_mock()
            with patch.object(udise, "UdiseClient", return_value=client), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(udise.main(arguments), 0)
            client.get_json.assert_not_called()
            for saved in raw:
                self.assertEqual(saved.read_bytes(), saved_bytes[str(saved)])
            final = json.loads(sorted((root / "reports/udise").glob("*.json"))[-1].read_text())
            self.assertEqual(final["status"], "completed")
            self.assertEqual(final["counts"]["discovered"], 2)
            self.assertEqual(final["counts"]["skipped"], 2)
            self.assertFalse(final["coverage"]["listing_complete"])
            self.assertFalse(final["coverage"]["scope_complete"])

    def test_limited_run_counts_duplicate_pending_school_only_once(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "list.json"
            first = school("03000000001")
            second = school("03000000002")
            path.write_text(json.dumps({"status": True, "data": [first, second, second]}))
            client = Mock()
            client.verify_year.return_value = 12
            client.catalog.return_value = [{"stateName": "Punjab", "stateId": 3}]
            client.get_json.return_value = {"success": True, "response": {
                "status": True, "data": {"pincode": 147001, "name": "School"}}}
            with patch.object(udise, "UdiseClient", return_value=client), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = udise.main(["--state", "Punjab", "--school-list", str(path), "--limit", "1",
                                   "--data-dir", str(root / "data"), "--report-dir", str(root / "reports")])
            self.assertEqual(code, 0)
            report = json.loads(next((root / "reports/udise").glob("*.json")).read_text())
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["pending"], 1)
            self.assertEqual(report["counts"]["duplicate"], 1)

    def test_source_pin_normalization_preserves_raw_dedicated_evidence(self):
        for raw in (147001, "147001", " 147 001 ", "147\u00a0001", "147-001"):
            with self.subTest(raw=raw):
                value = udise.source_pincode({"report_card": {"pinCode": raw},
                    "school_summary": {"pincode": "110001"}})
                self.assertEqual(value["actual_pincode"], "147001")
                self.assertEqual(value["pincode_status"], "valid")
                self.assertEqual(value["pincode_raw"], {"report_card.pinCode": raw})
        self.assertEqual(udise.source_pincode({"school_summary": {"pin_code": "147 001"}})["actual_pincode"], "147001")

    def test_source_pin_missing_invalid_and_conflicting_are_distinct(self):
        for record in ({}, {"report_card": {"pincode": None}},
                       {"school_summary": {"address": "Road 147001", "telephone": "147001"}}):
            with self.subTest(record=record):
                value = udise.source_pincode(record)
                self.assertEqual(value["pincode_status"], "missing")
                self.assertIsNone(value["actual_pincode"])
        for raw in (True, 147001.0, "14700", "014700", "PIN 147001", "147001 110001", "147001-", "१४७००१"):
            with self.subTest(raw=raw):
                value = udise.source_pincode({"report_card": {"pincode": raw}})
                self.assertEqual(value["pincode_status"], "invalid")
                self.assertIsNone(value["actual_pincode"])
        value = udise.source_pincode({"report_card": {"pincode": "147001", "pinCode": "110001"}})
        self.assertEqual(value["pincode_status"], "conflicting")
        self.assertIsNone(value["actual_pincode"])

    def test_whitespace_only_source_pins_are_missing_and_preserve_raw_evidence(self):
        for blank in (" ", "\t\n", "\u00a0 "):
            with self.subTest(blank=blank):
                value = udise.source_pincode({"report_card": {"pincode": blank},
                    "school_summary": {"pincode": "147001"}})
                self.assertEqual(value["pincode_status"], "missing")
                self.assertIsNone(value["actual_pincode"])
                self.assertEqual(value["pincode_raw"], {"report_card.pincode": blank})
        value = udise.source_pincode({"report_card": {"pincode": " ", "pinCode": "147 001"}})
        self.assertEqual(value["pincode_status"], "valid")
        self.assertEqual(value["actual_pincode"], "147001")
        self.assertEqual(value["pincode_raw"], {"report_card.pincode": " ", "report_card.pinCode": "147 001"})

    def test_nested_failed_saved_response_envelopes_are_rejected(self):
        row = school("03000000001")
        wrappers = ({"response": {"status": False, "error": "Search failed", "data": [row]}},
                    {"status": True, "data": {"results": {"status": False, "data": [row]}}},
                    {"result": {"response": {"status": False, "error": "Source unavailable"}}})
        for value in wrappers:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "failed.json"
                path.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, "unsuccessful source response"):
                    udise.load_lists([path])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "captcha.json"
            path.write_text(json.dumps({"response": {"data": {"status": False, "error": "Invalid Captcha"}}}))
            with self.assertRaises(ManualIntervention):
                udise.load_lists([path])

    def test_school_status_fields_are_not_mistaken_for_failed_search_envelopes(self):
        row = {**school("03000000001"), "status": False,
               "data": {"status": False}, "results": {"status": False}}
        for value in ([row], {"status": True, "response": {"data": {"content": [row]}}}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "schools.json"
                path.write_text(json.dumps(value))
                self.assertEqual(udise.load_lists([path]), [row])
        # A wrapper can also point directly at a school object; its status still
        # describes that school rather than an API success response.
        udise.validate_saved_response({"status": True, "data": row})

    def context_record(self):
        row = school("03000000001")
        fields = {"udiseSchCode": row["udiseschCode"], "stateName": "Punjab",
                  "districtName": "Patiala", "academicYear": "2025-26", "yearId": 12}
        return row, {"metadata": {"udise_code": row["udiseschCode"], "academic_year": "2025-26", "year_id": 12},
                     "school_summary": row, "school_profile": dict(fields), "report_card": dict(fields)}

    def test_context_rejects_source_identity_geography_and_detail_year_mismatches(self):
        args = SimpleNamespace(academic_year="2025-26", year_id=12)
        row, record = self.context_record()
        self.assertTrue(udise.validate_record_context(record, row, args)["geography_verified"])
        for section in ("school_profile", "report_card"):
            for key, wrong in (("udiseSchCode", "03000000002"), ("stateName", "Haryana"),
                               ("districtName", "Ludhiana"), ("academicYear", "2024-25")):
                with self.subTest(section=section, key=key):
                    row, record = self.context_record()
                    record[section][key] = wrong
                    with self.assertRaises(ValueError):
                        udise.validate_record_context(record, row, args)
        row, record = self.context_record()
        row["districtName"] = None
        with self.assertRaisesRegex(ValueError, "missing state or district"):
            udise.validate_record_context(record, row, args)

    def test_summary_year_difference_is_preserved_as_warning(self):
        row, record = self.context_record()
        row["sessionYear"] = "2026-27"
        evidence = udise.validate_record_context(record, row, SimpleNamespace(academic_year="2025-26", year_id=12))
        self.assertEqual(evidence["summary_academic_year"], "2026-27")
        self.assertFalse(evidence["summary_year_matches"])
        self.assertEqual(row["sessionYear"], "2026-27")
        self.assertEqual(record["report_card"]["academicYear"], "2025-26")

    def test_context_rejects_contradictory_cached_metadata_and_detail_year_ids(self):
        args = SimpleNamespace(academic_year="2025-26", year_id=12)
        for key, wrong in (("udise_code", "03000000002"), ("academic_year", "2024-25"), ("year_id", 13)):
            with self.subTest(metadata=key):
                row, record = self.context_record()
                record["metadata"][key] = wrong
                with self.assertRaises(ValueError):
                    udise.validate_record_context(record, row, args)
        for section in ("school_profile", "report_card"):
            with self.subTest(section=section):
                row, record = self.context_record()
                record[section]["yearId"] = 13
                with self.assertRaisesRegex(ValueError, "year ID mismatch"):
                    udise.validate_record_context(record, row, args)

    def test_nested_listing_totals_cannot_hide_partial_saved_search(self):
        row = school("03000000001")
        for wrapper in ({"status": True, "data": {"response": {"result": {"content": [row], "totalRecords": 2}}}},
                        {"status": True, "totalElements": 2, "data": {"content": [row], "totalElements": 1}}):
            with self.subTest(wrapper=wrapper), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "partial.json"
                path.write_text(json.dumps(wrapper))
                with self.assertRaisesRegex(ValueError, "paginated/incomplete"):
                    udise.load_lists([path])
        self.assertEqual(udise.listing_totals({"data": {"content": [dict(row, totalRecords=1000)],
                                                        "totalElements": True}}), [])

    def run_saved(self, folder, rows, *extra, detail_pin="147001", client=None):
        root = Path(folder)
        path = root / "list.json"
        path.write_text(json.dumps({"status": True, "data": rows}))
        client = client or Mock()
        client.verify_year.return_value = 12
        client.catalog.return_value = [{"stateName": "Punjab", "stateId": 3}]
        if client.get_json.side_effect is None:
            def detail(endpoint, params):
                return {"success": True, "response": {"status": True, "data": {
                    "udiseSchCode": params.get("udiseSchCode"), "stateName": "Punjab", "districtName": "Patiala",
                    "academicYear": "2025-26", "yearId": 12, "pincode": detail_pin, "name": "School"}}}
            client.get_json.side_effect = detail
        arguments = ["--state", "Punjab", "--school-list", str(path), "--delay", "0", "--retries", "0",
                     "--data-dir", str(root / "data"), "--state-dir", str(root / "state"),
                     "--report-dir", str(root / "reports"), *extra]
        with patch.object(udise, "UdiseClient", return_value=client), contextlib.redirect_stdout(io.StringIO()):
            code = udise.main(arguments)
        report_path = sorted((root / "reports/udise").glob("*.json"))[-1]
        return code, json.loads(report_path.read_text()), client

    def test_rate_limit_on_first_detail_stops_and_preserves_pending_candidates(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [school("03000000001"), school("03000000002"), school("03000000003")]
            actual_client = udise.UdiseClient(delay=0, retries=3)
            client = Mock()
            client.get_json.side_effect = actual_client.get_json
            try:
                with patch.object(actual_client.session, "get", return_value=response(status=429, text="Too many requests")) as get:
                    code, report, client = self.run_saved(folder, rows, "--limit", "3", client=client)
                self.assertEqual(code, 2)
                self.assertEqual(report["status"], "manual_intervention")
                self.assertEqual(report["counts"]["success"], 0)
                self.assertEqual(report["counts"]["failed"], 1)
                self.assertEqual(report["pending_identifiers"], ["03000000002", "03000000003"])
                self.assertEqual(report["candidate_manifest"], rows)
                self.assertEqual(client.get_json.call_count, 1)
                get.assert_called_once()
                self.assertEqual(list((Path(folder) / "data").glob("*/2025-26/*.json")), [])
                events = [json.loads(line) for line in (Path(folder) / "state/events.jsonl").read_text().splitlines()]
                discovered = {event["payload"]["school_identifier"] for event in events if event["kind"] == "task_discovered"}
                self.assertEqual(discovered, {"03000000001", "03000000002", "03000000003"})
            finally:
                actual_client.close()

    def test_listing_pin_mismatch_still_fetches_matching_selected_year_details(self):
        with tempfile.TemporaryDirectory() as folder:
            row = school("03000000001", pincode="110001")
            row["sessionYear"] = "2026-27"
            code, report, client = self.run_saved(folder, [row], "--pincode", "147001")
            self.assertEqual(code, 0)
            self.assertEqual(client.get_json.call_count, 4)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["filtered"], 0)
            self.assertEqual(report["record_outcomes"][0]["actual_pincode"], "147001")
            self.assertEqual(report["record_outcomes"][0]["pincode_source"], "report_card")
            self.assertEqual(report["record_outcomes"][0]["pincode_academic_year"], "2025-26")
            self.assertTrue(report["record_outcomes"][0]["scope_year_verified"])
            raw = json.loads(next((Path(folder) / "data").glob("*/2025-26/*.json")).read_text())
            self.assertEqual(raw["school_summary"]["pincode"], "110001")
            self.assertEqual(raw["report_card"]["pincode"], "147001")

    def test_current_summary_pin_cannot_match_a_historical_pin_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            row = school("03000000001")
            row["sessionYear"] = "2026-27"
            code, report, client = self.run_saved(folder, [row], "--pincode", "147001", detail_pin=None)
            self.assertEqual(code, 0)
            self.assertEqual(client.get_json.call_count, 4)
            self.assertEqual(report["counts"]["success"], 0)
            self.assertEqual(report["counts"]["filtered"], 1)
            self.assertEqual(report["counts"]["unverified_pincode_year"], 1)
            item = report["record_outcomes"][0]
            self.assertEqual(item["reason"], "pincode_year_mismatch")
            self.assertEqual(item["actual_pincode"], "147001")
            self.assertEqual(item["pincode_source"], "school_summary")
            self.assertEqual(item["pincode_academic_year"], "2026-27")
            self.assertFalse(item["scope_year_verified"])
            self.assertEqual(item["pincode_raw"], {"school_summary.pincode": "147001"})
            self.assertEqual(list((Path(folder) / "data").glob("*/2025-26/*.json")), [])
            cached = next((Path(folder) / "state/filtered_payloads/udise/2025-26").glob("*.json"))
            self.assertEqual(json.loads(cached.read_text())["school_summary"]["sessionYear"], "2026-27")

    def test_summary_pin_year_must_be_verified_independently_of_requested_details(self):
        for summary_year, expected_success, expected_reason in ((None, 0, "pincode_year_unverified"),
                                                                ("2025-26", 1, "raw_record_success")):
            with self.subTest(summary_year=summary_year), tempfile.TemporaryDirectory() as folder:
                row = school("03000000001")
                if summary_year:
                    row["sessionYear"] = summary_year
                code, report, client = self.run_saved(folder, [row], "--pincode", "147001", detail_pin=None)
                self.assertEqual(code, 0)
                self.assertEqual(report["counts"]["success"], expected_success)
                self.assertEqual(report["record_outcomes"][0]["reason"], expected_reason)
                self.assertEqual(report["record_outcomes"][0]["scope_year_verified"], bool(expected_success))

    def test_whole_geography_collection_preserves_current_pin_without_claiming_annual_pin(self):
        with tempfile.TemporaryDirectory() as folder:
            row = school("03000000001")
            row["sessionYear"] = "2026-27"
            code, report, client = self.run_saved(folder, [row], "--unlimited", detail_pin=None)
            self.assertEqual(code, 0)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["unverified_pincode_year"], 1)
            self.assertEqual(report["record_outcomes"][0]["actual_pincode"], "147001")
            self.assertFalse(report["record_outcomes"][0]["scope_year_verified"])
            self.assertTrue(any("selected-year provenance" in note for note in report["coverage"]["limitations"]))
            raw = json.loads(next((Path(folder) / "data").glob("*/2025-26/*.json")).read_text())
            self.assertEqual(raw["metadata"]["academic_year"], "2025-26")
            self.assertEqual(raw["school_summary"]["sessionYear"], "2026-27")
            self.assertEqual(raw["school_summary"]["pincode"], "147001")
            self.assertIsNone(raw["report_card"]["pincode"])

    def test_listing_matching_pin_does_not_override_nonmatching_details(self):
        with tempfile.TemporaryDirectory() as folder:
            code, report, client = self.run_saved(folder, [school("03000000001")],
                                                 "--pincode", "147001", detail_pin="110001")
            self.assertEqual(code, 0)
            self.assertEqual(client.get_json.call_count, 4)
            self.assertEqual(report["counts"]["filtered"], 1)
            self.assertEqual(report["counts"]["success"], 0)
            self.assertEqual(report["record_outcomes"][0]["reason"], "pincode_mismatch")
            self.assertEqual(report["record_outcomes"][0]["actual_pincode"], "110001")
            self.assertEqual(list((Path(folder) / "data").glob("*/2025-26/*.json")), [])

    def test_limit_journals_every_unique_candidate_and_reports_pending_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [school("03000000001"), school("03000000002"), school("03000000002"), school("03000000003")]
            code, report, client = self.run_saved(folder, rows, "--limit", "1")
            self.assertEqual(code, 0)
            self.assertEqual(report["counts"]["discovered"], 3)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["pending"], 2)
            self.assertEqual(report["counts"]["duplicate"], 1)
            self.assertEqual(report["pending_identifiers"], ["03000000002", "03000000003"])
            self.assertEqual(report["candidate_manifest"], rows)
            events = [json.loads(line) for line in (Path(folder) / "state/events.jsonl").read_text().splitlines()]
            discovered = {event["payload"]["school_identifier"] for event in events if event["kind"] == "task_discovered"}
            self.assertEqual(discovered, {"03000000001", "03000000002", "03000000003"})
            self.assertEqual(client.get_json.call_count, 4)
            self.assertFalse(report["coverage"]["scope_complete"])

    def test_time_budget_preserves_pending_after_first_detail(self):
        with tempfile.TemporaryDirectory() as folder:
            elapsed, requests_done = [0], [0]
            client = Mock()

            def detail(endpoint, params):
                requests_done[0] += 1
                if requests_done[0] == 4:
                    elapsed[0] = 2
                return {"success": True, "response": {"status": True, "data": {
                    "pincode": "147001", "name": "School", "udiseSchCode": params["udiseSchCode"]}}}

            client.get_json.side_effect = detail
            with patch.object(udise.time, "monotonic", side_effect=lambda: elapsed[0]):
                code, report, client = self.run_saved(folder, [school("03000000001"), school("03000000002")],
                    "--limit", "5", "--max-duration", "1", client=client)
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "time_budget_reached")
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["pending"], 1)
            self.assertEqual(report["pending_identifiers"], ["03000000002"])
            self.assertEqual(report["record_outcomes"][-1]["reason"], "time_budget_reached")
            self.assertFalse(report["coverage"]["scope_complete"])
            self.assertFalse(report["coverage"]["candidate_limit_reached"])

    def test_keyboard_interrupt_reconciles_pending_manifest_without_changing_saved_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            client, requests_done = Mock(), [0]

            def detail(endpoint, params):
                requests_done[0] += 1
                if requests_done[0] == 5:
                    raise KeyboardInterrupt
                return {"success": True, "response": {"status": True, "data": {
                    "udiseSchCode": params["udiseSchCode"], "pincode": "147001", "name": "School"}}}

            client.get_json.side_effect = detail
            rows = [school("03000000001"), school("03000000002"), school("03000000003")]
            code, report, client = self.run_saved(folder, rows, "--unlimited", client=client)
            self.assertEqual(code, 1)
            self.assertEqual(report["status"], "interrupted")
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["pending"], 2)
            self.assertEqual(report["pending_identifiers"], ["03000000002", "03000000003"])
            self.assertEqual(len(report["candidate_manifest"]), 3)
            self.assertEqual(len(list((Path(folder) / "data").glob("*/2025-26/*.json"))), 1)
            self.assertFalse(report["coverage"]["details_complete"])

    def test_existing_raw_pin_filter_marks_local_task_completed_and_preserves_raw(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [school("03000000001")]
            code, initial, client = self.run_saved(folder, rows, "--unlimited")
            self.assertEqual(code, 0)
            raw_path = next((Path(folder) / "data").glob("*/2025-26/*.json"))
            original_bytes = raw_path.read_bytes()
            code, report, second_client = self.run_saved(folder, rows, "--pincode", "110001")
            self.assertEqual(code, 0)
            second_client.get_json.assert_not_called()
            self.assertEqual(report["counts"]["filtered"], 1)
            self.assertEqual(report["counts"]["pending"], 0)
            self.assertEqual(raw_path.read_bytes(), original_bytes)
            events = [json.loads(line) for line in (Path(folder) / "state/events.jsonl").read_text().splitlines()]
            completed = [event for event in events if event["kind"] == "task_completed"
                         and event["payload"].get("school_identifier") == "03000000001"]
            self.assertTrue(completed)
            self.assertEqual(completed[-1]["payload"]["task_status"], "filtered")

    def test_saved_lists_never_establish_whole_source_scope_with_unlimited(self):
        with tempfile.TemporaryDirectory() as folder:
            row = school("03000000001")
            row["sessionYear"] = "2026-27"
            code, report, client = self.run_saved(folder, [row], "--unlimited")
            self.assertEqual(code, 0)
            self.assertIsNone(report["scope"]["limit"])
            self.assertTrue(report["coverage"]["details_complete"])
            self.assertFalse(report["coverage"]["listing_complete"])
            self.assertFalse(report["coverage"]["scope_complete"])
            self.assertTrue(any("Saved official" in note for note in report["coverage"]["limitations"]))
            self.assertTrue(any("academic year differs" in note for note in report["coverage"]["limitations"]))
            self.assertFalse(report["record_outcomes"][0]["summary_year_matches"])

    def test_saved_district_filter_requires_verified_official_catalog_choice(self):
        for official_name, expected_code in (("PATIALA", 0), ("LUDHIANA", 1)):
            with self.subTest(official_name=official_name), tempfile.TemporaryDirectory() as folder:
                client = Mock()
                client.catalog.side_effect = [[{"stateName": "Punjab", "stateId": 3}],
                    [{"districtName": official_name, "districtId": 36}]]
                code, report, client = self.run_saved(folder, [school("03000000001")],
                    "--district", "Patiala", client=client)
                self.assertEqual(code, expected_code)
                self.assertEqual(client.catalog.call_args_list[-1].args,
                                 ("districts", {"stateId": 3, "yearId": 12}))
                if expected_code == 0:
                    self.assertEqual(report["counts"]["success"], 1)
                    self.assertEqual(report["verified_districts"], [{"state": "Punjab", "district": "PATIALA", "district_id": 36}])
                else:
                    client.get_json.assert_not_called()
                    self.assertIn("absent from the selected official states", report["error"])


if __name__ == "__main__":
    unittest.main()
