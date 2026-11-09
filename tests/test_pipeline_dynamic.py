"""Offline compatibility checks; source data and PostgreSQL are never changed."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from school_scraping import canonical_v4_mapper as udise_mapper
from school_scraping import saras_canonical_v4_mapper as saras_mapper
from school_scraping import standardize_school_data as standardizer
from school_scraping import validate_canonical_v4 as udise_validator
from school_scraping import validate_saras_canonical_v4 as saras_validator
from database.import_json_to_postgres import classify_file
from school_scraping.datasets import (
    academic_year, discover_saras_datasets, discover_udise_datasets,
    validation_datasets,
)


ROOT = Path(__file__).resolve().parents[1]


class DatasetDiscoveryTests(unittest.TestCase):
    def test_new_states_and_original_cities_use_existing_layout(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            for place in ("Delhi", "Punjab", "Haryana"):
                folder = data / place / "2025-26"
                folder.mkdir(parents=True)
                (folder / "school_1.json").write_text("{}", encoding="utf-8")
                (folder / "_summary.json").write_text("{}", encoding="utf-8")
            (data / "Punjab" / "2026-27").mkdir()
            (data / "CBSE_SARAS" / "punjab" / "schools").mkdir(parents=True)
            folders = discover_udise_datasets(data)
            self.assertEqual(set(folders), {"delhi", "punjab", "haryana"})
            self.assertEqual(set(discover_udise_datasets(data, "2026-27")), {"punjab"})
            self.assertEqual(set(discover_saras_datasets(data)), {"punjab"})
            checks = validation_datasets("udise", {"delhi": 237}, data)
            self.assertEqual(checks["delhi"]["expected"], 237)
            self.assertEqual(checks["punjab"]["expected"], 1)
            self.assertEqual(checks["haryana"]["canonical"],
                             data / "canonical_v4" / "udise" / "haryana")

    def test_year_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            academic_year("../../outside")

    def test_archive_classification_for_dynamic_states_and_legacy_paths(self):
        examples = [
            ("data/Punjab/2026-27/school.json", {}, ("raw", "UDISE", "Punjab", "2026-27")),
            ("data/CBSE_SARAS/haryana/schools/a.json", {}, ("raw", "CBSE_SARAS", "Haryana", None)),
            ("data/Delhi/2025-26/a.json", {}, ("raw", "UDISE", "Delhi", "2025-26")),
            ("data/canonical_v4/udise/punjab/schools/a.json", {}, ("canonical", "UDISE", "Punjab", None)),
            ("data/CBSE_SARAS/delhi/schools/a.json", {"metadata": {"district": True}},
             ("raw", "CBSE_SARAS", "Delhi", None)),
        ]
        for path, raw, expected in examples:
            with self.subTest(path=path):
                self.assertEqual(classify_file(path, raw), expected)


class RawCompatibilityTests(unittest.TestCase):
    def test_new_udise_year_reaches_provenance_and_yearly_snapshot(self):
        raw = {
            "metadata": {"academic_year": "2026-27", "udise_code": "03010100101"},
            "school_summary": {"udiseschCode": "03010100101", "schoolName": "Sample",
                               "stateName": "PUNJAB", "districtName": "Sample District", "pincode": 140001},
            "school_profile": {}, "student_teacher_statistics": {},
            "infrastructure_facilities": {}, "report_card": {}, "school_history": [],
        }
        original = copy.deepcopy(raw)
        path = ROOT / "data" / "Punjab" / "2026-27" / "school.json"
        record = udise_mapper.map_udise_record(raw, path, "punjab")
        self.assertEqual(record["provenance"]["academic_year"], "2026-27")
        self.assertEqual(record["metadata"]["sources_merged"][0]["academic_year"], "2026-27")
        self.assertEqual(udise_mapper.build_yearly_snapshot(record)["academic_year"], "2026-27")
        self.assertEqual(standardizer.standardize_udise(raw, path, "punjab")["metadata"]["academic_year"], "2026-27")
        self.assertEqual(udise_validator.validate_record_structure(record), [])
        self.assertEqual(raw, original)

        # Legacy records can lack year metadata; the preserved folder supplies it.
        without_year = copy.deepcopy(raw)
        without_year["metadata"].pop("academic_year")
        fallback = udise_mapper.map_udise_record(without_year, path, "punjab")
        self.assertEqual(udise_mapper.build_yearly_snapshot(fallback)["academic_year"], "2026-27")

    def test_snapshot_validator_accepts_the_selected_year(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "_yearly_snapshots.json").write_text(json.dumps([
                {"udise_code": "03010100101", "academic_year": "2026-27", "schema_version": "4.0"}
            ]), encoding="utf-8")
            result = udise_validator.validate_snapshots(
                folder, {"03010100101": {}}, year="2026-27")
            self.assertEqual(result["invalid_year"], [])
            self.assertEqual(result["count"], 1)

    def test_existing_raw_samples_still_map_without_mutation(self):
        udise_files = sorted((ROOT / "data" / "Delhi" / "2025-26").glob("*.json"))
        saras_files = sorted((ROOT / "data" / "CBSE_SARAS" / "delhi" / "schools").glob("*.json"))
        pairs = [("udise", next((p for p in udise_files if not p.name.startswith("_")), None)),
                 ("saras", next((p for p in saras_files if not p.name.startswith("_")), None))]
        for source, path in pairs:
            with self.subTest(source=source):
                if path is None:
                    self.skipTest(f"Existing {source} sample is unavailable.")
                before = hashlib.sha256(path.read_bytes()).digest()
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                original = copy.deepcopy(raw)
                if source == "udise":
                    canonical = udise_mapper.map_udise_record(raw, path, "delhi")
                    self.assertEqual(udise_validator.validate_record_structure(canonical), [])
                    normalized = standardizer.standardize_udise(raw, path, "delhi")
                    self.assertEqual(normalized["school_identity"]["udise_code"], raw["metadata"]["udise_code"])
                else:
                    canonical = saras_mapper.map_saras_record(raw, path, "delhi")
                    self.assertFalse(saras_validator.REQUIRED_SECTIONS - canonical.keys())
                    normalized = standardizer.standardize_saras(raw, path, "delhi")
                    self.assertEqual(normalized["school_identity"]["cbse_affiliation_number"],
                                     raw["school_details"]["Affiliation Number"])
                self.assertEqual(raw, original)
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)
