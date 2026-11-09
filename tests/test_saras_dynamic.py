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
                           "Name of Institution": "Test School", "Pin Code": "134001"},
                "links": []}


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

    def test_challenge_is_reported_with_resumable_affected_task(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = FakeBrowser(failure=ManualIntervention("CAPTCHA requires manual completion"))
            report = saras.run(self.args(Path(directory)), browser)
            self.assertEqual(report["status"], "manual_intervention")
            self.assertEqual(report["failures"][0]["task"]["affiliation_number"], "1630001")
            self.assertEqual(browser.detail_calls, ["1630001"])

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


if __name__ == "__main__":
    unittest.main()
