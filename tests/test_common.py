import json
import tempfile
import unittest
from pathlib import Path

from school_scraping.common import (
    ManualIntervention, RawStore, SourceLock, check_access, complete_record,
    pin, record_identifier, write_report,
)


def udise_record(code="07000000001", year="2025-26", name="A School"):
    return {
        "metadata": {"udise_code": code, "academic_year": year, "school_name": name},
        "school_summary": {"udiseschCode": code, "schoolName": name},
        "school_profile": {"name": name},
        "student_teacher_statistics": {"students": 5},
        "infrastructure_facilities": {"rooms": 2},
        "report_card": {"pincode": 134001},
        "school_history": None,
        "_api_status": {"history": None},
    }


def saras_record(code="1630001", name="A School"):
    return {
        "metadata": {"affiliation_number": code},
        "listing_data": {},
        "school_details": {"Affiliation Number": code, "Name of Institution": name},
        "links": [],
    }


class StorageTests(unittest.TestCase):
    def test_source_id_dedup_across_geographic_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for source, record, relative in [
                ("udise", udise_record(), "Punjab/2025-26/first.json"),
                ("saras", saras_record(), "CBSE_SARAS/punjab/schools/first.json"),
            ]:
                with self.subTest(source=source):
                    store = RawStore(source, root, "2025-26")
                    identifier = record_identifier(source, record)
                    first = root / relative
                    self.assertEqual(store.save(identifier, record, first), "success")
                    original_bytes = first.read_bytes()
                    alternate = root / relative.replace("Punjab", "Haryana").replace("punjab", "haryana")
                    resumed = RawStore(source, root, "2025-26")
                    self.assertEqual(resumed.save(identifier, record, alternate), "skipped")
                    self.assertEqual(first.read_bytes(), original_bytes)
                    self.assertFalse(alternate.exists())

    def test_udise_same_id_keeps_distinct_yearly_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = udise_record(year="2025-26")
            new = udise_record(year="2026-27")
            first = root / "Punjab/2025-26/school.json"
            second = root / "Punjab/2026-27/school.json"
            RawStore("udise", root, "2025-26").save("07000000001", old, first)
            store = RawStore("udise", root, "2026-27")
            self.assertIsNone(store.existing("07000000001"))
            self.assertEqual(store.save("07000000001", new, second), "success")
            self.assertEqual(json.loads(first.read_text())["metadata"]["academic_year"], "2025-26")
            self.assertEqual(json.loads(second.read_text())["metadata"]["academic_year"], "2026-27")

    def test_refresh_preserves_exact_original_bytes_and_updates_original_path(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Punjab/2025-26/original_name.json"
            path.parent.mkdir(parents=True)
            old = udise_record()
            original_bytes = json.dumps(old, ensure_ascii=False, separators=(",  ", ": ")).encode() + b"\r\n"
            path.write_bytes(original_bytes)
            fresh = udise_record(name="New School Name")
            store = RawStore("udise", root, "2025-26", refresh=True)
            alternate = root / "Haryana/2025-26/renamed.json"
            self.assertEqual(store.save("07000000001", fresh, alternate), "updated")
            backups = list((root / "refresh_backups/UDISE").glob("*.json"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original_bytes)
            self.assertEqual(json.loads(path.read_text()), fresh)
            self.assertFalse(alternate.exists())

    def test_refresh_with_unchanged_data_does_not_rewrite_or_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Punjab/2025-26/school.json"
            record = udise_record()
            RawStore("udise", root, "2025-26").save("07000000001", record, path)
            before = path.read_bytes()
            candidate = udise_record()
            candidate["_api_status"]["request_time"] = "later"
            store = RawStore("udise", root, "2025-26", refresh=True)
            self.assertEqual(store.save("07000000001", candidate, path), "unchanged")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((root / "refresh_backups").exists())

    def test_incomplete_existing_record_is_retryable_and_backed_up(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Punjab/2025-26/school.json"
            path.parent.mkdir(parents=True)
            partial = udise_record()
            partial["report_card"] = None
            path.write_text(json.dumps(partial))
            before = path.read_bytes()
            store = RawStore("udise", root, "2025-26")
            self.assertIsNone(store.existing("07000000001"))
            self.assertEqual(store.save("07000000001", udise_record(), path), "updated")
            self.assertEqual(list((root / "refresh_backups/UDISE").glob("*.json"))[0].read_bytes(), before)

    def test_failed_history_does_not_count_as_complete(self):
        partial = udise_record()
        partial["metadata"]["school_id"] = 123
        partial["_api_status"]["history"] = {"success": False, "error": "Timeout"}
        self.assertFalse(complete_record("udise", partial))

    def test_malformed_existing_metadata_cannot_abort_whole_index(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Punjab/2025-26/bad.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"metadata": ["invalid"]}))
            store = RawStore("udise", root, "2025-26")
            self.assertIsNone(store.existing("07000000001"))
            self.assertFalse(complete_record("udise", {"metadata": ["invalid"]}))

    def test_incomplete_or_conflicting_records_never_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "Punjab/2025-26/existing.json"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"unindexed raw data")
            before = path.read_bytes()
            store = RawStore("udise", root, "2025-26")
            self.assertIn(str(path), store.invalid_files)
            with self.assertRaises(FileExistsError):
                store.save("07000000001", udise_record(), path)
            with self.assertRaises(ValueError):
                store.save("07000000002", udise_record(), root / "new.json")
            partial = udise_record()
            partial["school_profile"] = None
            with self.assertRaises(ValueError):
                store.save("07000000001", partial, root / "new.json")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((root / "new.json").exists())

    def test_source_locks_contend_and_release_after_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "body failure"):
                with SourceLock(folder, "udise"):
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        with SourceLock(folder, "udise"):
                            self.fail("An overlapping source run obtained the lock")
                    with SourceLock(folder, "saras"):
                        pass
                    raise ValueError("body failure")
            with SourceLock(folder, "udise"):
                pass

    def test_report_writes_are_versioned_and_keep_old_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            first = write_report(folder, "udise", {"status": "failed"})
            before = first.read_bytes()
            second = write_report(folder, "udise", {"status": "completed"})
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), before)


class AccessTests(unittest.TestCase):
    def test_challenges_require_manual_intervention(self):
        examples = [("CAPTCHA required", 200), ("{}", 403), ("{}", 401),
                    ("<input id='g-recaptcha-response'>", 200),
                    ("<html>Verify you are human</html>", 200),
                    ('{"error":"Invalid Captcha"}', 200)]
        for body, status in examples:
            with self.subTest(body=body, status=status), self.assertRaises(ManualIntervention):
                check_access(body, status)

    def test_ordinary_source_data_is_not_a_challenge(self):
        check_access('{"schoolName":"CAPTCHA ACADEMY","status":true}')

    def test_pincode_only_accepts_exact_explicit_six_digit_value(self):
        self.assertEqual(pin(134001), "134001")
        self.assertEqual(pin(" 134001 "), "134001")
        for value in (None, "", "013400", "134001.0", "Address 134001", "1340017"):
            with self.subTest(value=value):
                self.assertIsNone(pin(value))


if __name__ == "__main__":
    unittest.main()
