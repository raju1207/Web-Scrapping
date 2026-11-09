"""Moving examples into data must not turn them into imported school records."""
import tempfile
import unittest
from pathlib import Path

from database.import_json_to_postgres import discover_json_files


class ImportSelectionTests(unittest.TestCase):
    def test_samples_and_recovery_state_are_excluded_but_school_history_remains(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            included = {
                "Punjab/2025-26/school.json",
                "CBSE_SARAS/haryana/schools/school.json",
                "canonical_v4/udise/punjab/schools/school.json",
                "refresh_backups/UDISE/school_before_update.json",
            }
            excluded = {
                "samples/gallery_data.json",
                "samples/nested/example.json",
                ".scraping_state/import_payloads/pending.json",
                "Punjab/.scraping_state/checkpoint.json",
                ".scraper_locks/metadata.json",
            }
            for name in included | excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
            selected = {path.relative_to(root).as_posix() for path in discover_json_files(root)}
            self.assertEqual(selected, included)


if __name__ == "__main__":
    unittest.main()
