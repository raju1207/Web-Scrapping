"""Regression checks for the cleaned root and relocated processing commands."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from database import scrape_pipeline
from school_scraping import canonical_v4_mapper, saras_canonical_v4_mapper
from school_scraping import standardize_school_data, validate_canonical_v4, validate_saras_canonical_v4
from school_scraping.datasets import DATA_ROOT, PROJECT_ROOT


PROCESSING_MODULES = (
    "school_scraping.standardize_school_data",
    "school_scraping.canonical_v4_mapper",
    "school_scraping.saras_canonical_v4_mapper",
    "school_scraping.validate_canonical_v4",
    "school_scraping.validate_saras_canonical_v4",
    "school_scraping.maintenance.missing_value_classifier",
)


def child_environment():
    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(PROJECT_ROOT) + (os.pathsep + existing if existing else "")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


class PackageLayoutTests(unittest.TestCase):
    def test_only_the_three_collection_entry_points_remain_in_root(self):
        self.assertEqual({path.name for path in PROJECT_ROOT.glob("*.py")}, {
            "udise_dynamic_scraper.py", "saras_dynamic_scraper.py", "daily_scheduler.py",
        })

    def test_database_pipeline_uses_the_relocated_processing_modules(self):
        self.assertIs(scrape_pipeline.udise_mapper, canonical_v4_mapper)
        self.assertIs(scrape_pipeline.saras_mapper, saras_canonical_v4_mapper)
        self.assertIs(scrape_pipeline.standardizer, standardize_school_data)
        self.assertIs(scrape_pipeline.udise_validator, validate_canonical_v4)
        self.assertIs(scrape_pipeline.saras_validator, validate_saras_canonical_v4)
        self.assertEqual(standardize_school_data.ROOT, PROJECT_ROOT)
        self.assertEqual(standardize_school_data.DATA, DATA_ROOT)

    def test_processing_paths_stay_anchored_when_imported_from_another_directory(self):
        program = """
import json
from school_scraping import canonical_v4_mapper as udise
from school_scraping import saras_canonical_v4_mapper as saras
from school_scraping import standardize_school_data as standardizer
from school_scraping import validate_canonical_v4 as uv
from school_scraping import validate_saras_canonical_v4 as sv
from school_scraping.maintenance import missing_value_classifier as classifier
print(json.dumps([str(standardizer.ROOT), str(standardizer.DATA), str(udise.OUTPUT_ROOT),
                  str(saras.OUTPUT_ROOT), str(uv.REPORT_PATH), str(sv.REPORT_PATH),
                  str(classifier.BASE_DIR), str(classifier.DATA_DIR)]))
"""
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, "-B", "-c", program], cwd=temporary,
                                    env=child_environment(), capture_output=True, text=True,
                                    check=True, timeout=30)
            self.assertEqual(json.loads(result.stdout), [
                str(PROJECT_ROOT), str(DATA_ROOT), str(DATA_ROOT / "canonical_v4"),
                str(DATA_ROOT / "canonical_v4" / "saras"),
                str(DATA_ROOT / "audit" / "canonical_v4_validation_report.json"),
                str(DATA_ROOT / "audit" / "saras_canonical_v4_validation_report.json"),
                str(PROJECT_ROOT), str(DATA_ROOT),
            ])
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_all_relocated_processing_commands_have_safe_module_help(self):
        with tempfile.TemporaryDirectory() as temporary:
            for module in PROCESSING_MODULES:
                with self.subTest(module=module):
                    result = subprocess.run([sys.executable, "-B", "-m", module, "--help"],
                                            cwd=temporary, env=child_environment(),
                                            capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("usage:", result.stdout.lower())
            self.assertEqual(list(Path(temporary).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
