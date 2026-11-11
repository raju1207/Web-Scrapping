"""Offline regressions for the consolidated SARAS collector."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import saras_dynamic_scraper as saras
from school_scraping.common import ManualIntervention


def listing(affiliation="1630001"):
    return {"affiliation_number": affiliation,
            "detail_url": saras.BASE_URL + "/SARAS/AffiliatedList/AfflicationDetails/" + affiliation}


class FakeBrowser:
    def __init__(self, rows=None, failure=None):
        self.rows = rows or {"PUNJAB": [listing()], "HARYANA": [listing(), listing("530001")]}
        self.failure = failure
        self.current = None
        self.detail_calls = []

    def states(self):
        return [{"value": "live-punjab", "text": "PUNJAB"},
                {"value": "live-haryana", "text": "HARYANA"}]

    def configure_state(self, state):
        return [{"value": "live-district", "text": "TEST DISTRICT"}]

    def search(self, state, district):
        self.current = state["text"]

    def collect(self, limit=None):
        rows = self.rows[self.current]
        return rows[:limit] if limit else rows

    def detail(self, school):
        affiliation = school["affiliation_number"]
        self.detail_calls.append(affiliation)
        if self.failure and affiliation == "1630001":
            raise self.failure
        return {"fields": {"Affiliation Number": affiliation,
                           "Name of Institution": "Test School", "Pin Code": "134001",
                           "State": self.current, "District": "TEST DISTRICT"},
                "links": []}


class ExhaustiveBrowser(FakeBrowser):
    """A fixture with explicit, independently verified listing exhaustion."""

    def collect(self, limit=None):
        rows = super().collect(limit)
        self.last_collection = {"pagination_complete": limit is None, "reported_total": len(self.rows[self.current]),
                                "unique_candidates": len(rows), "rows_observed": len(rows)}
        return rows


class SarasDynamicTests(unittest.TestCase):
    def args(self, directory, *extra):
        args = saras.build_parser().parse_args(
            ["--state", "Punjab", "--state", "Haryana", "--data-dir", str(directory), *extra]
        )
        return args

    def test_listing_preserves_legacy_columns_and_identifier(self):
        html = """<table><tr><th>School</th></tr><tr><td>1</td><td>Aff. No. 1630001</td>
        <td>State : PUNJAB</td><td>Secondary</td><td>Test School</td><td>Address</td>
        <td><a href='/SARAS/AffiliatedList/AfflicationDetails/1630001'>View</a></td></tr></table>"""
        rows = saras.parse_listing_rows(html)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["affiliation_number"], "1630001")
        self.assertEqual(rows[0]["school_and_head"], "Test School")
        self.assertEqual(len(rows[0]["listing_raw_columns"]), 7)
        self.assertTrue(rows[0]["detail_url"].startswith(saras.BASE_URL))

    def test_detail_preserves_labels_missing_values_and_repeated_fields(self):
        html = """<table><tr><td>Name of Institution</td><td>Test School</td></tr>
        <tr><td>Remarks</td><td></td></tr><tr><td>Experience</td><td>3</td></tr>
        <tr><td>Experience</td><td>4</td></tr></table>
        <a href='/document'>Document</a><a href='/document'>Document</a>"""
        detail = saras.parse_detail_page(html, saras.LIST_URL)
        self.assertEqual(detail["fields"]["Experience"], ["3", "4"])
        self.assertIsNone(detail["fields"]["Remarks"])
        self.assertEqual(detail["links"], [{"text": "Document", "url": saras.BASE_URL + "/document"}])

    def test_pincode_uses_only_explicit_source_field(self):
        self.assertEqual(saras.verified_pincode({"Pin Code": "134001"}), "134001")
        self.assertIsNone(saras.verified_pincode({"Postal Address": "Road 134001", "Telephone": "134001"}))
        self.assertIsNone(saras.verified_pincode({"Pin Code": ["134001", "110001"]}))
        self.assertIsNone(saras.verified_pincode({"Pin Code": "134001 110001"}))

    def test_wrong_or_empty_detail_response_is_rejected(self):
        for fields in ({}, {"CAPTCHA": "Please solve"}, {"Affiliation Number": "9999999"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                saras.validate_detail(fields, "1630001")
        with self.assertRaises(ValueError):
            saras.validate_detail({"Affiliation Number": "1630001", "Name of Institution": None}, "1630001")
        saras.validate_detail({"Affiliation Number": "1630001", "Name of Institution": "School"}, "1630001")

    def test_visible_captcha_widgets_block_but_hidden_widgets_do_not(self):
        page, body, widgets = Mock(), Mock(), Mock()
        body.inner_text.return_value = "Official school directory"
        widgets.count.return_value = 1
        widgets.nth.return_value.is_visible.return_value = True
        page.locator.side_effect = lambda selector: body if selector == "body" else widgets
        args = SimpleNamespace(timeout=1, manual_captcha=0, headless=True)
        browser = saras.SarasBrowser(page, args)
        with self.assertRaises(ManualIntervention):
            browser.guard()
        widgets.nth.return_value.is_visible.return_value = False
        browser.guard()

    def test_rate_limited_navigation_retries_with_a_bound(self):
        page = Mock()
        page.goto.side_effect = [SimpleNamespace(status=429), SimpleNamespace(status=200)]
        args = SimpleNamespace(delay=0, timeout=1, retries=1)
        browser = saras.SarasBrowser(page, args)
        browser.guard = Mock()
        with patch.object(saras.time, "sleep"):
            browser.navigate(saras.LIST_URL)
        self.assertEqual(page.goto.call_count, 2)

    def test_pagination_collects_multiple_pages_and_stops_at_disabled_next(self):
        def html(affiliation):
            return ("<table><tr><td>1</td><td>Affiliation</td><td>State</td><td>Status</td>"
                    "<td>School</td><td>Address</td><td><a href='/SARAS/AffiliatedList/"
                    f"AfflicationDetails/{affiliation}'>View</a></td></tr></table>")
        page, candidates, button = Mock(), Mock(), Mock()
        page.content.side_effect = [html("1630001"), html("1630002"), html("1630002")]
        page.locator.return_value = candidates
        candidates.count.return_value = 1
        candidates.nth.return_value = button
        button.evaluate.side_effect = [False, True]
        button.is_visible.return_value = True
        browser = saras.SarasBrowser(page, SimpleNamespace(delay=0, timeout=1))
        browser.guard = Mock()
        with patch.object(saras.time, "sleep"):
            rows = browser.collect()
        self.assertEqual([row["affiliation_number"] for row in rows], ["1630001", "1630002"])
        button.click.assert_called_once()

    def test_geography_aliases_resolve_live_values_only(self):
        options = [{"text": "ANDAMAN & NICOBAR", "value": "verified"}]
        self.assertEqual(saras.resolve_options(["Andaman and Nicobar Islands"], options, "State"), options)
        self.assertEqual(saras.geography_key("Chandigarh"), "chandigarh")
        with self.assertRaises(ValueError):
            saras.resolve_options(["Unsupported State"], options, "State")

    def test_raw_schema_and_legacy_folder_compatibility(self):
        detail = {"fields": {"Affiliation Number": "1630001"}, "links": []}
        record = saras.build_record(listing(), detail, "PUNJAB")
        self.assertEqual(set(record), {"metadata", "listing_data", "school_details", "links"})
        self.assertEqual(record["metadata"]["affiliation_number"], "1630001")
        path = saras.output_path(Path("data"), "CHANDIGARH", "1630001", detail["fields"])
        self.assertIn("Chandigarh", path.parts)

    def test_cross_state_dedup_and_second_run_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            args, browser = self.args(Path(directory)), FakeBrowser()
            report = saras.run(args, browser)
            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["counts"]["success"], 2)
            self.assertEqual(report["counts"]["duplicate"], 1)
            self.assertEqual(len(list(Path(directory).glob("CBSE_SARAS/*/schools/*.json"))), 2)
            again = FakeBrowser()
            second = saras.run(args, again)
            self.assertEqual(second["counts"]["skipped"], 2)
            self.assertEqual(second["counts"]["success"], 0)
            self.assertEqual(again.detail_calls, [])

    def test_pincode_rejection_does_not_write_raw_school(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory), "--pincode", "110001")
            report = saras.run(args, FakeBrowser())
            self.assertEqual(report["counts"]["filtered"], 2)
            self.assertEqual(report["counts"]["success"], 0)
            self.assertEqual(list(Path(directory).glob("CBSE_SARAS/*/schools/*.json")), [])
            again = FakeBrowser()
            second = saras.run(args, again)
            self.assertEqual(second["counts"]["filtered"], 2)
            self.assertEqual(again.detail_calls, [])

    def test_individual_detail_failure_allows_other_school(self):
        with tempfile.TemporaryDirectory() as directory:
            report = saras.run(self.args(Path(directory)), FakeBrowser(failure=ValueError("Invalid detail")))
            self.assertEqual(report["status"], "partial_failure")
            self.assertEqual(report["counts"]["failed"], 1)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["pending"], 0)

    def test_challenge_is_reported_with_resumable_affected_task(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser(failure=ManualIntervention("CAPTCHA requires manual completion"))
            report = saras.run(self.args(Path(directory)), browser)
            self.assertEqual(report["status"], "manual_intervention")
            self.assertEqual(report["failures"][0]["task"]["affiliation_number"], "1630001")
            self.assertEqual(browser.detail_calls, ["1630001"])
            self.assertEqual(report["counts"]["failed"], 1)
            self.assertEqual(report["counts"]["pending"], 0)

    def test_limit_is_global_across_states(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser()
            report = saras.run(self.args(Path(directory), "--limit", "1"), browser)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(browser.detail_calls, ["1630001"])

    def test_qualified_district_does_not_broaden_other_states(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser()
            report = saras.run(self.args(Path(directory), "--district", "Punjab:TEST DISTRICT"), browser)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(browser.detail_calls, ["1630001"])

    def test_dry_run_has_no_file_writes_or_browser_import(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            code = saras.main(["--state", "Punjab,Haryana", "--pincode", "134001", "--dry-run",
                               "--data-dir", directory, "--report-dir", directory])
            self.assertEqual(code, 0)
            scope = json.loads(output.getvalue())
            self.assertEqual(scope["states"], ["Punjab", "Haryana"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_browser_startup_failure_writes_safe_report(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            with patch("playwright.sync_api.sync_playwright", side_effect=RuntimeError("private-session-token")):
                code = saras.main(["--state", "Punjab", "--data-dir", str(root / "data"),
                                   "--report-dir", str(root / "reports")])
            self.assertEqual(code, 1)
            reports = list((root / "reports").rglob("*.json"))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "startup_failure")
            self.assertNotIn("private-session-token", reports[0].read_text(encoding="utf-8"))

    def test_source_pin_formatting_is_normalized_without_extracting_address_digits(self):
        for value in ("134001", 134001, " 134 001 ", "134\u00a0001", "134-001", "1 3 4 0 0 1"):
            with self.subTest(value=value):
                self.assertEqual(saras.normalize_pincode(value), "134001")
        for value in (None, True, 134001.0, "PIN 134001", "134001.0", "134001 110001",
                      "134-00-1", "-134001", "134001-", "013400", "１３４００１", "134"):
            with self.subTest(value=value):
                self.assertIsNone(saras.normalize_pincode(value))
        evidence = saras.pincode_evidence({"Pin Code": "134 001", "Postal Address": "Delhi 110001"})
        self.assertEqual(evidence["actual_pincode"], "134001")
        self.assertEqual(evidence["pincode_raw"], {"Pin Code": "134 001"})

    def test_conflicting_dedicated_pin_aliases_and_invalid_fields_are_distinct(self):
        self.assertEqual(saras.pincode_evidence({})["pincode_status"], "missing")
        self.assertEqual(saras.pincode_evidence({"Pin Code": "160"})["pincode_status"], "invalid")
        self.assertEqual(saras.pincode_evidence({"Pin Code": "134001", "Postal Code": "110001"})["pincode_status"], "conflicting")
        self.assertIsNone(saras.verified_pincode({"Pin Code": ["134001", "invalid"]}))
        self.assertEqual(saras.verified_pincode({"Pin Code": ["134001", "134 001"]}), "134001")

    def test_url_trim_preserves_internal_encoding_queries_and_original_listing(self):
        url = saras.BASE_URL + "/some%20path/500001%20%20?query=%20#fragment%20"
        self.assertEqual(saras.normalized_detail_url(url),
                         saras.BASE_URL + "/some%20path/500001?query=%20#fragment%20")
        escaped = saras.BASE_URL + "/500001%2520"
        self.assertEqual(saras.normalized_detail_url(escaped), escaped)
        html = ("<table><tr><td>1</td><td>Aff. No.: 500001</td><td>State: HARYANA</td>"
                "<td>Status</td><td>School</td><td>Address</td>"
                "<td><a href='/SARAS/AffiliatedList/AfflicationDetails/500001%20'>View</a></td></tr></table>")
        row = saras.parse_listing_rows(html)[0]
        self.assertTrue(row["detail_url"].endswith("500001"))
        self.assertTrue(row["original_detail_url"].endswith("500001%20"))
        with self.assertRaises(ValueError):
            saras.parse_listing_rows(html.replace("Aff. No.: 500001", "Aff. No.: 500002"))

    def test_missing_invalid_and_mismatched_pins_have_explicit_evidence(self):
        for raw_pin, reason, missing, invalid in ((None, "missing_pincode", 1, 0),
                ("160", "invalid_pincode", 0, 1), ("133001", "pincode_mismatch", 0, 0)):
            with self.subTest(pin=raw_pin), tempfile.TemporaryDirectory() as directory:
                browser = FakeBrowser()
                original = browser.detail

                def detail(school):
                    result = original(school)
                    result["fields"]["Pin Code"] = raw_pin
                    return result

                browser.detail = detail
                report = saras.run(self.args(Path(directory), "--limit", "1", "--pincode", "134001"), browser)
                self.assertEqual(report["counts"]["filtered"], 1)
                self.assertEqual(report["counts"]["failed"], 0)
                self.assertEqual(report["counts"]["missing_pincode"], missing)
                self.assertEqual(report["counts"]["invalid_pincode"], invalid)
                self.assertEqual(report["record_outcomes"][0]["reason"], reason)
                self.assertEqual(report["record_outcomes"][0]["pincode_raw"], {"Pin Code": raw_pin})
                self.assertTrue(report["record_outcomes"][0]["identity_verified"])
                self.assertFalse(report["coverage"]["scope_complete"])

    def test_wrong_geography_is_validation_failure_even_with_matching_pin(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser()
            original = browser.detail

            def detail(school):
                value = original(school)
                value["fields"]["State"] = "MAHARASHTRA"
                return value

            browser.detail = detail
            report = saras.run(self.args(Path(directory), "--limit", "1", "--pincode", "134001"), browser)
            self.assertEqual(report["counts"]["failed"], 1)
            self.assertEqual(report["counts"]["filtered"], 0)
            self.assertEqual(report["counts"]["success"], 0)
            self.assertEqual(report["record_outcomes"][0]["reason"], "detail_validation_failed")
            self.assertEqual(list(Path(directory).glob("CBSE_SARAS/*/schools/*.json")), [])

    def test_listing_and_detail_geography_disagreement_is_rejected(self):
        school = {**listing(), "state_and_district": "State : PUNJAB District : LUDHIANA"}
        saras.validate_geography({"State": "PUNJAB", "District": "LUDHIANA"}, school, "Punjab", "Ludhiana")
        for fields in ({"State": "HARYANA", "District": "LUDHIANA"},
                       {"State": "PUNJAB", "District": "AMBALA"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                saras.validate_geography(fields, school, "Punjab", "Ludhiana")
        with self.assertRaises(ValueError):
            saras.validate_geography({}, listing(), "Punjab", "Ludhiana")

    def test_candidate_limit_is_applied_before_pin_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser()
            report = saras.run(self.args(Path(directory), "--limit", "1", "--pincode", "110001"), browser)
            self.assertEqual(browser.detail_calls, ["1630001"])
            self.assertEqual(report["counts"]["discovered"], 1)
            self.assertEqual(report["counts"]["filtered"], 1)
            self.assertTrue(report["coverage"]["candidate_limit_reached"])
            self.assertFalse(report["coverage"]["listing_complete"])

    def test_time_budget_journals_all_candidates_and_resumes_pending(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.state = ["Punjab"]
            args.max_duration = 1
            rows = {"PUNJAB": [listing("1630001"), listing("1630002"), listing("1630003")]}
            browser, elapsed = ExhaustiveBrowser(rows), [0]
            original = browser.detail

            def detail(school):
                result = original(school)
                elapsed[0] = 2
                return result

            browser.detail = detail
            with patch.object(saras.time, "monotonic", side_effect=lambda: elapsed[0]):
                report = saras.run(args, browser)
            self.assertEqual(report["status"], "time_budget_exhausted")
            self.assertEqual(report["counts"]["discovered"], 3)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["pending_identifiers"], ["1630002", "1630003"])
            self.assertEqual(len(report["candidate_manifest"]), 3)
            self.assertTrue(report["coverage"]["listing_complete"])
            self.assertFalse(report["coverage"]["details_complete"])
            self.assertFalse(report["coverage"]["scope_complete"])
            self.assertEqual(report["duration_seconds"], 2)
            self.assertEqual(report["pending_listing_scopes"], [])
            args.max_duration = None
            resumed_browser = FakeBrowser(rows)
            resumed_browser.current = "PUNJAB"  # Saved tasks fetch details before listing navigation.
            resumed = saras.run(args, resumed_browser)
            self.assertEqual(resumed["status"], "completed")
            self.assertEqual(resumed_browser.detail_calls, ["1630002", "1630003"])
            self.assertEqual(resumed["counts"]["pending"], 0)
            self.assertEqual(len(list(Path(directory).glob("CBSE_SARAS/*/schools/*.json"))), 3)

    def test_listing_exhaustion_is_preserved_when_detail_validation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.state = ["Punjab"]
            browser = ExhaustiveBrowser({"PUNJAB": [listing(), listing("1630002")]},
                                        failure=ValueError("Invalid detail"))
            report = saras.run(args, browser)
            self.assertEqual(report["status"], "partial_failure")
            self.assertTrue(report["coverage"]["listing_complete"])
            self.assertFalse(report["coverage"]["details_complete"])
            self.assertFalse(report["coverage"]["scope_complete"])
            self.assertEqual(report["counts"]["failed"], 1)
            self.assertEqual(report["counts"]["pending"], 0)

    def test_timed_recovery_reports_all_already_known_pending_candidates(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.state, args.max_duration = ["Punjab"], 1
            rows = {"PUNJAB": [listing(str(1630001 + value)) for value in range(5)]}
            elapsed = [0]

            def time_bounded_browser():
                browser = FakeBrowser(rows)
                browser.current = "PUNJAB"  # Recovery details precede fresh listing.
                detail = browser.detail

                def advance(school):
                    result = detail(school)
                    elapsed[0] = 2
                    return result

                browser.detail = advance
                return browser

            with patch.object(saras.time, "monotonic", side_effect=lambda: elapsed[0]):
                first = saras.run(args, time_bounded_browser())
            self.assertEqual(first["counts"]["pending"], 4)
            elapsed[0] = 0
            with patch.object(saras.time, "monotonic", side_effect=lambda: elapsed[0]):
                resumed = saras.run(args, time_bounded_browser())
            self.assertEqual(resumed["status"], "time_budget_exhausted")
            self.assertEqual(resumed["counts"]["discovered"], 4)
            self.assertEqual(resumed["counts"]["success"], 1)
            self.assertEqual(resumed["counts"]["pending"], 3)
            self.assertEqual(resumed["pending_identifiers"], ["1630003", "1630004", "1630005"])
            self.assertEqual(len(resumed["candidate_manifest"]), 4)
            self.assertFalse(resumed["coverage"]["scope_complete"])

    def test_mumbai_unvisited_second_district_prevents_complete_listing_claim(self):
        class MumbaiBrowser(ExhaustiveBrowser):
            def states(self):
                return [{"value": "official-maharashtra", "text": "MAHARASHTRA"}]

            def configure_state(self, state):
                return [{"value": "official-city", "text": "MUMBAI"},
                        {"value": "official-suburban", "text": "MUMBAI SUBURBAN"}]

            def search(self, state, district):
                super().search(state, district)
                self.district = district["text"]

            def detail(self, school):
                result = super().detail(school)
                result["fields"]["District"] = self.district
                elapsed[0] = 2
                return result

        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.state = ["Maharashtra"]
            args.district = ["MUMBAI", "MUMBAI SUBURBAN"]
            args.max_duration, elapsed = 1, [0]
            browser = MumbaiBrowser({"MAHARASHTRA": [listing()]})
            with patch.object(saras.time, "monotonic", side_effect=lambda: elapsed[0]):
                report = saras.run(args, browser)
            self.assertEqual(report["status"], "time_budget_exhausted")
            self.assertEqual(len(report["requested_listing_scopes"]), 2)
            self.assertTrue(all(scope["catalog_verified"] for scope in report["requested_listing_scopes"]))
            self.assertEqual(report["exhausted_listing_scopes"], [{"state": "MAHARASHTRA", "district": "MUMBAI"}])
            self.assertEqual([scope["district"] for scope in report["pending_listing_scopes"]], ["MUMBAI SUBURBAN"])
            self.assertFalse(report["coverage"]["listing_complete"])
            self.assertFalse(report["coverage"]["scope_complete"])
            self.assertEqual(report["counts"]["failed"], 0)
            self.assertEqual(report["counts"]["pending"], 0)

    def pagination_browser(self, html, *, total=None, next_count=0, disabled=True):
        page, candidates, button = Mock(), Mock(), Mock()
        page.content.return_value = html
        page.evaluate.return_value = {"total": total} if total is not None else None
        page.locator.return_value = candidates
        candidates.count.return_value = next_count
        candidates.nth.return_value = button
        button.evaluate.return_value = disabled
        button.is_visible.return_value = True
        browser = saras.SarasBrowser(page, SimpleNamespace(delay=0, timeout=0.001))
        browser.guard = Mock()
        return browser

    def pagination_html(self, *identifiers):
        return "<table>" + "".join(
            "<tr><td>1</td><td>School</td><td>State</td><td>Status</td><td>Name</td><td>Address</td>"
            f"<td><a href='/SARAS/AffiliatedList/AfflicationDetails/{identifier}'>View</a></td></tr>"
            for identifier in identifiers) + "</table>"

    def page_length_browser(self, options, total=1):
        browser = self.pagination_browser(self.pagination_html("500001"), total=total)
        controls, control = Mock(), Mock()
        controls.count.return_value = 0 if options is None else 1
        controls.nth.return_value = control
        control.is_visible.return_value = True
        control.is_enabled.return_value = True
        control.locator.return_value.evaluate_all.return_value = options
        control.select_option.side_effect = lambda *, value: [value]
        next_controls = browser.page.locator.return_value
        browser.page.locator.side_effect = lambda selector: (controls if selector ==
            "#myTable_length select, select[name='myTable_length']" else next_controls)
        return browser, control, controls

    def test_unlimited_listing_uses_only_source_offered_all_page_length(self):
        options = [{"value": "10", "text": "10", "selected": True},
                   {"value": "100", "text": "100"}, {"value": "-1", "text": "All"}]
        browser, control, _ = self.page_length_browser(options)
        self.assertEqual(len(browser.collect()), 1)
        control.select_option.assert_called_once_with(value="-1")
        self.assertEqual(browser.last_collection["page_length"]["offered_options"], options)
        self.assertEqual(browser.last_collection["page_length"]["selected_value"], "-1")
        self.assertEqual(browser.last_collection["page_length"]["selected_rows"], -1)
        self.assertTrue(browser.last_collection["pagination_complete"])

    def test_unlimited_listing_selects_largest_valid_enabled_offered_length(self):
        options = [{"value": "10", "text": "10"}, {"value": "250", "text": "250"},
                   {"value": "-1", "text": "All", "disabled": True},
                   {"value": "500", "text": "500", "disabled": True},
                   {"value": "all", "text": "All"}, {"value": "0", "text": "0"}]
        browser, control, _ = self.page_length_browser(options)
        browser.collect()
        control.select_option.assert_called_once_with(value="250")
        self.assertEqual(browser.last_collection["page_length"]["selected_rows"], 250)

    def test_missing_page_length_control_preserves_normal_pagination(self):
        browser, control, _ = self.page_length_browser(None)
        self.assertEqual(len(browser.collect()), 1)
        control.select_option.assert_not_called()
        self.assertEqual(browser.last_collection["page_length"]["status"], "control_unavailable")
        self.assertIsNone(browser.last_collection["page_length"]["selected_value"])
        self.assertTrue(browser.last_collection["pagination_complete"])

    def test_candidate_limited_listing_leaves_source_page_length_unchanged(self):
        browser, control, controls = self.page_length_browser([{"value": "-1", "text": "All"}])
        browser.collect(limit=1)
        controls.count.assert_not_called()
        control.select_option.assert_not_called()
        self.assertEqual(browser.last_collection["page_length"]["status"], "candidate_limited")
        self.assertFalse(browser.last_collection["pagination_complete"])

    def test_offered_all_page_length_still_requires_complete_reported_total(self):
        browser, control, _ = self.page_length_browser([{"value": "-1", "text": "All"}], total=2)
        with self.assertRaisesRegex(RuntimeError, "reported listing total"):
            browser.collect()
        control.select_option.assert_called_once_with(value="-1")
        self.assertFalse(browser.last_collection["pagination_complete"])

    def test_missing_next_with_unreached_source_total_fails(self):
        browser = self.pagination_browser(self.pagination_html("500001"), total=20)
        with self.assertRaisesRegex(RuntimeError, "reported listing total"):
            browser.collect()
        self.assertFalse(browser.last_collection["pagination_complete"])
        self.assertEqual(browser.last_collection["rows_observed"], 1)
        self.assertEqual(browser.last_collection["unique_candidates"], 1)
        self.assertEqual(browser.last_collection["source_rows_observed"], 1)

    def test_unparseable_listing_row_retains_public_evidence_without_tokens(self):
        malformed = ("<tr><td>2</td><td>Aff. No.: 2730692</td><td>State : DELHI</td>"
                     "<td>Secondary</td><td>School Name</td><td>School Address</td>"
                     "<td><a href='/SARAS/AffiliatedList/AfflicationDetails/?token=private#secret'>View</a>"
                     "<input type='hidden' value='hidden-secret'></td></tr>")
        html = self.pagination_html("2700001").replace("</table>", malformed + "</table>")
        diagnostics = {}
        schools = saras.parse_listing_rows(html, diagnostics)
        self.assertEqual([row["affiliation_number"] for row in schools], ["2700001"])
        self.assertEqual(diagnostics["source_row_count"], 2)
        self.assertEqual(diagnostics["parseable_row_count"], 1)
        evidence = diagnostics["unparsed_rows"][0]
        self.assertEqual(evidence["displayed_affiliation"], "2730692")
        self.assertEqual(evidence["reason"], "missing_numeric_affiliation_detail_link")
        self.assertEqual(evidence["link_paths"], ["/SARAS/AffiliatedList/AfflicationDetails/"])
        self.assertNotIn("private", json.dumps(diagnostics))
        self.assertNotIn("secret", json.dumps(diagnostics))

    def test_main_listing_body_excludes_layout_tables_and_footer_rows(self):
        html = self.pagination_html("500099") + self.pagination_html("500001").replace(
            "<table>", "<table id='myTable'><thead><tr><td>Header</td></tr></thead><tbody>").replace(
            "</table>", "</tbody><tfoot><tr><td>Footer</td></tr></tfoot></table>")
        diagnostics = {}
        rows = saras.parse_listing_rows(html, diagnostics)
        self.assertEqual([row["affiliation_number"] for row in rows], ["500001"])
        self.assertEqual(diagnostics["source_row_count"], 1)
        self.assertEqual(diagnostics["unparsed_rows"], [])

    def test_accounted_source_row_quarantine_preserves_valid_candidates(self):
        malformed = ("<tr><td>2</td><td>Aff. No.: 500002</td><td>State</td>"
                     "<td>Status</td><td>School</td><td>Address</td><td>No detail link</td></tr>")
        html = self.pagination_html("500001").replace("</table>", malformed + "</table>")
        browser = self.pagination_browser(html, total=2)
        known = []
        browser.on_candidate = known.append
        rows = browser.collect()
        self.assertEqual([row["affiliation_number"] for row in rows], ["500001"])
        self.assertEqual([row["affiliation_number"] for row in known], ["500001"])
        self.assertEqual(browser.last_collection["rows_observed"], 1)
        self.assertEqual(browser.last_collection["unique_candidates"], 1)
        self.assertEqual(browser.last_collection["source_rows_observed"], 2)
        self.assertEqual(browser.last_collection["parseable_rows_observed"], 1)
        self.assertEqual(len(browser.last_collection["unparsed_listing_rows"]), 1)
        self.assertEqual(browser.last_collection["stop_reason"], "source_rows_quarantined")
        self.assertEqual(browser.last_collection["valid_source_identifier_count"], 1)
        self.assertEqual(browser.last_collection["invalid_source_listing_rows"], 1)
        self.assertTrue(browser.last_collection["source_pagination_complete"])
        self.assertTrue(browser.last_collection["pagination_complete"])

    def test_quarantined_source_identity_does_not_abort_valid_details_or_verify_scope(self):
        class QuarantinedBrowser(ExhaustiveBrowser):
            def collect(self, limit=None):
                rows = super().collect(limit)
                self.last_collection.update(source_rows_observed=2, parseable_rows_observed=1,
                    reported_total=2, source_pagination_complete=True,
                    invalid_source_listing_rows=1, valid_source_identifier_count=1,
                    unparsed_listing_rows=[{"source_row_index": 2,
                        "reason": "missing_numeric_affiliation_detail_link",
                        "listing_raw_columns": ["2", "Aff. No.: NONNUMERIC", "Source district"],
                        "displayed_affiliation": None,
                        "link_paths": ["/SARAS/AffiliatedList/AfflicationDetails/NONNUMERIC"]}])
                return rows

        with tempfile.TemporaryDirectory() as directory:
            args = self.args(Path(directory))
            args.state = ["Punjab"]
            browser = QuarantinedBrowser({"PUNJAB": [listing()]})
            report = saras.run(args, browser)
            self.assertEqual(report["status"], "partial_source_data")
            self.assertEqual(report["counts"]["discovered"], 1)
            self.assertEqual(report["counts"]["success"], 1)
            self.assertEqual(report["counts"]["invalid_listing_rows"], 1)
            self.assertEqual(report["counts"]["failed"], 0)
            self.assertEqual(report["counts"]["pending"], 0)
            self.assertEqual(len(report["quarantined_listing_rows"]), 1)
            self.assertEqual(len(report["candidate_manifest"]), 1)
            self.assertTrue(report["coverage"]["source_pagination_complete"])
            self.assertFalse(report["coverage"]["listing_complete"])
            self.assertTrue(report["coverage"]["details_complete"])
            self.assertFalse(report["coverage"]["scope_complete"])
            self.assertEqual(len(list(Path(directory).glob("CBSE_SARAS/*/schools/*.json"))), 1)

    def test_absent_pagination_evidence_never_claims_completeness(self):
        browser = self.pagination_browser(self.pagination_html("500001"))
        self.assertEqual(len(browser.collect()), 1)
        self.assertFalse(browser.last_collection["pagination_complete"])
        self.assertEqual(browser.last_collection["stop_reason"], "pagination_unverified")
        verified = self.pagination_browser(self.pagination_html("500001"), total=1)
        verified.collect()
        self.assertTrue(verified.last_collection["pagination_complete"])
        empty = self.pagination_browser("<table></table>", total=0)
        self.assertEqual(empty.collect(), [])
        self.assertTrue(empty.last_collection["pagination_complete"])

    def test_global_limit_counts_new_affiliations_after_overlapping_district(self):
        browser = self.pagination_browser(self.pagination_html("500001", "500002"), total=2)
        rows = browser.collect(1, exclude_identifiers={"500001"})
        self.assertEqual([row["affiliation_number"] for row in rows], ["500001", "500002"])
        self.assertEqual(browser.last_collection["unique_candidates"], 2)
        self.assertEqual(browser.last_collection["new_unique_candidates"], 1)
        self.assertEqual(browser.last_collection["stop_reason"], "candidate_limit")

    def test_stalled_next_is_failure_and_discovered_inputs_are_preserved(self):
        browser = self.pagination_browser(self.pagination_html("500001"), total=2, next_count=1, disabled=False)
        discovered = []
        browser.on_candidate = discovered.append
        with patch.object(saras.time, "sleep"), self.assertRaisesRegex(RuntimeError, "Next did not change"):
            browser.collect()
        self.assertEqual([row["affiliation_number"] for row in discovered], ["500001"])


if __name__ == "__main__":
    unittest.main()
