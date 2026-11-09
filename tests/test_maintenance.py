"""Maintenance relocation/consolidation checks use isolated files only."""
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from school_scraping.maintenance import (
    audit_list_vs_raw as audit,
    build_summary,
    compare_raw_vs_standardized as preservation,
    compare_udise_lists as comparison,
    count_current_data,
    raw_data_audit,
    saras_raw_data_audit,
    section_level_audit,
)

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class ConsolidatedMaintenanceTests(unittest.TestCase):
    def test_audit_preserves_legacy_report_and_input_bytes_for_each_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = dump(root / "saved.json", {"data": {"content": [
                {"udiseSchCode": 1234567890, "schoolName": "Leading Zero"},
                {"udiseSchCode": "07000000001", "schoolName": "Missing"},
                {"udiseSchCode": 1234567890, "schoolName": "Duplicate listing"},
            ]}})
            raw = root / "raw"
            dump(raw / "one.json", {"metadata": {"udise_code": "01234567890", "school_name": "Leading Zero"}})
            dump(raw / "duplicate.json", {"metadata": {"udise_code": "01234567890", "school_name": "Duplicate"}})
            dump(raw / "extra.json", {"school_summary": {"udiseschCode": "07000000003", "schoolName": "Extra"}})
            dump(raw / "_summary.json", {"udise_code": "07000000009"})
            dump(raw / "_errors" / "ignored.json", {"udise_code": "07000000008"})
            (raw / "invalid.json").write_text("{not-json", encoding="utf-8")
            original = {path: path.read_bytes() for path in root.rglob("*.json")}
            for location in ("Chandigarh", "Delhi", "Mumbai"):
                output = root / "reports" / f"{location}.json"
                with self.subTest(location=location), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(audit.main(["--location", location, "--list-file", str(saved),
                                                 "--raw-dir", str(raw), "--report-file", str(output)]), 0)
                    report = json.loads(output.read_text())
                    self.assertEqual(set(report), {"location", "summary", "missing_raw_records", "extra_raw_records", "invalid_files", "duplicate_codes"})
                    self.assertEqual(report["location"], location)
                    self.assertEqual(report["summary"], {
                        "school_list_count": 2, "raw_school_count": 2, "common_count": 1,
                        "missing_raw_count": 1, "extra_raw_count": 1, "invalid_raw_files": 1,
                        "duplicate_udise_codes": 1})
                    self.assertEqual(report["missing_raw_records"][0]["udise_code"], "07000000001")
                    self.assertEqual(report["extra_raw_records"][0]["udise_code"], "07000000003")
                    self.assertEqual(len(report["duplicate_codes"]["01234567890"]), 2)
            for path, content in original.items():
                self.assertEqual(path.read_bytes(), content)

    def test_comparison_preserves_fields_dedup_and_correct_mumbai_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = dump(root / "old.json", {"data": [
                {"udiseSchCode": 1234567890, "schoolName": "First", "district": "District", "block": "Block"},
                {"udiseSchCode": 1234567890, "schoolName": "Ignored duplicate"},
                {"udiseSchCode": "07000000001", "schoolName": "Old"},
            ]})
            fresh = dump(root / "fresh.json", [
                {"udiseschCode": "01234567890", "schoolName": "Common"},
                {"udiseschCode": "07000000002", "schoolName": "New"},
            ])
            original = (saved.read_bytes(), fresh.read_bytes())
            for location in ("Chandigarh", "Delhi", "Mumbai"):
                output = root / f"{location}.json"
                with self.subTest(location=location), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(comparison.main(["--location", location, "--saved-list", str(saved),
                                                     "--fresh-list", str(fresh), "--report-file", str(output)]), 0)
                    report = json.loads(output.read_text())
                    self.assertEqual(report["location"], location)
                    self.assertEqual(set(report), {"location", "old_file", "fresh_file", "summary", "only_in_old", "only_in_fresh", "common_udise_codes"})
                    self.assertEqual(report["summary"], {"old_unique_udise_codes": 2,
                        "fresh_unique_udise_codes": 2, "present_in_both": 1,
                        "only_in_old": 1, "only_in_fresh": 1})
                    self.assertEqual(report["common_udise_codes"], ["01234567890"])
                    self.assertEqual(set(report["only_in_fresh"][0]), {"udise_code", "school_name", "district", "block"})
            self.assertEqual((saved.read_bytes(), fresh.read_bytes()), original)
            records = comparison.deduplicate(comparison.extract_school_records(json.loads(saved.read_text())))
            self.assertEqual(records["01234567890"]["school_name"], "First")

    def test_other_locations_work_with_explicit_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            saved = dump(root / "punjab.json", [{"udiseCode": "03000000001", "schoolName": "Punjab school"}])
            raw = root / "schools"
            dump(raw / "school.json", {"metadata": {"udise_code": "03000000001"}})
            self.assertEqual(audit.audit("Punjab", saved, raw)["summary"]["common_count"], 1)
            self.assertEqual(comparison.compare("Punjab", saved, saved)["summary"]["present_in_both"], 1)

    def test_dry_run_keeps_historical_paths_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            for location in ("Chandigarh", "Delhi", "Mumbai"):
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(audit.main(["--location", location, "--year", "2026-27", "--dry-run"]), 0)
                plan = json.loads(output.getvalue())
                self.assertEqual(Path(plan["raw_dir"]), ROOT / "data" / location / "2026-27")
                self.assertEqual(Path(plan["report_file"]), ROOT / "data/audit" / f"{location.lower()}_list_vs_raw_report.json")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    comparison.main(["--location", location, "--dry-run"])
                self.assertEqual(Path(json.loads(output.getvalue())["fresh_list"]), ROOT / "data/udise_fresh_lists" / f"{location.lower()}_fresh.json")
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_every_moved_maintenance_module_has_safe_help_from_another_cwd(self):
        names = ["audit_list_vs_raw", "compare_udise_lists", "build_summary", "count_current_data",
                 "compare_raw_vs_standardized", "raw_data_audit", "section_level_audit", "saras_raw_data_audit"]
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
            for name in names:
                with self.subTest(module=name):
                    result = subprocess.run([sys.executable, "-B", "-m", "school_scraping.maintenance." + name, "--help"],
                                            cwd=temporary, env=environment, capture_output=True, text=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)
            self.assertEqual(list(Path(temporary).iterdir()), [])


class RetainedMaintenanceTests(unittest.TestCase):
    def test_project_paths_remain_anchored_after_relocation(self):
        self.assertEqual(raw_data_audit.BASE_DIR, ROOT)
        self.assertEqual(section_level_audit.BASE_DIR, ROOT)
        self.assertEqual(build_summary.DATA_DIR, ROOT / "data/Chandigarh/2025-26")
        self.assertEqual(preservation.OUTPUT, ROOT / "data/audit/raw_vs_standardized_report.json")
        self.assertEqual(saras_raw_data_audit.REPORT_PATH, ROOT / "data/audit/saras_raw_data_audit_report.json")
        self.assertTrue(all(path.is_absolute() for path in count_current_data.DATASETS.values()))

    def test_audits_preserve_meaningful_zero_false_and_missing_sections(self):
        self.assertFalse(raw_data_audit.is_missing(0))
        self.assertFalse(raw_data_audit.is_missing(False))
        self.assertEqual(preservation.classify(True, 0, True, 0), "PASS")
        self.assertEqual(preservation.classify(True, "Name", False, None), "MAPPING_LOSS")
        self.assertEqual(section_level_audit.section_status({}, "school_profile"), "MISSING_SECTION")
        self.assertEqual(section_level_audit.section_status({"school_profile": None}, "school_profile"), "NULL_SECTION")
        self.assertEqual(saras_raw_data_audit.classify_value(False), "BOOLEAN")
        self.assertTrue(saras_raw_data_audit.inspect_affiliation_period("From: 01/04/2020 To: 31/03/2025")["parseable"])

    def test_counting_and_summary_rebuild_preserve_individual_school_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = dump(root / "school.json", {"metadata": {"school_name": "School", "udise_code": "04010800107",
                                                           "academic_year": "2025-26"}, "school_summary": {"schoolId": 123}})
            before = hashlib.sha256(raw.read_bytes()).hexdigest()
            dump(root / "_old_helper.json", {})
            self.assertEqual(count_current_data.count_json_files(root), 1)
            with patch.object(build_summary, "DATA_DIR", root), \
                 patch.object(build_summary, "SUMMARY_FILE", root / "_summary.json"), \
                 patch.object(build_summary, "ALL_SCHOOLS_FILE", root / "_all_schools.json"), \
                 contextlib.redirect_stdout(io.StringIO()):
                build_summary.main([])
            self.assertEqual(json.loads((root / "_summary.json").read_text())["total_school_json_files"], 1)
            self.assertEqual(json.loads((root / "_all_schools.json").read_text())[0]["udise_code"], "04010800107")
            self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
