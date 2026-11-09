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
            self.assertEqual(final["status"], "not_due")
            self.assertEqual(final["counts"]["discovered"], 0)

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


if __name__ == "__main__":
    unittest.main()
