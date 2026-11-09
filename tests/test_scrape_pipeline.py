"""Per-school ETL tests; PostgreSQL tests require an explicit isolated-test DSN."""

import hashlib
import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from psycopg.conninfo import conninfo_to_dict

from database import scrape_pipeline as pipeline
from database.import_json_to_postgres import prepare_json_record


def udise_raw(identifier, year="2025-26", name="Pipeline Test School"):
    return {
        "metadata": {"source": "UDISE+ Know Your School", "location": "Punjab",
                     "academic_year": year, "udise_code": identifier},
        "school_summary": {"udiseschCode": identifier, "schoolName": name,
                           "stateName": "PUNJAB", "districtName": "LUDHIANA",
                           "pincode": 141001, "address": "School Road"},
        "school_profile": {"address": "School Road", "boardSecName": "CBSE"},
        "student_teacher_statistics": {"totalBoy": 4, "totalGirl": 6, "totalCount": 10},
        "infrastructure_facilities": {"rampsYn": 1}, "report_card": {"totalTeacher": 2},
        "school_history": [],
    }


def saras_raw(identifier, name):
    return {"metadata": {"affiliation_number": identifier, "state": "PUNJAB"},
            "listing_data": {"affiliation_number": identifier},
            "school_details": {"Affiliation Number": identifier, "Name of Institution": name,
                               "State": "PUNJAB", "District": "LUDHIANA",
                               "Postal Address": "School Road", "Pin Code": "141001"},
            "links": []}


def make_event(root, source, identifier, raw, region="Punjab", event_offset=0):
    data_root = Path(root) / "data"
    year = raw.get("metadata", {}).get("academic_year")
    path = (data_root / region / year / f"school_{identifier}.json" if source == "udise"
            else data_root / "CBSE_SARAS" / region.lower() / "schools" / f"school_{identifier}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(raw, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(content)
    snapshot = Path(root) / "snapshots" / f"{uuid.uuid4().hex}.json"
    snapshot.parent.mkdir(exist_ok=True)
    snapshot.write_bytes(content)
    event_time = time.time() + event_offset
    os.utime(snapshot, (event_time, event_time))
    return {"source": source, "school_identifier": identifier, "academic_year": year,
            "region": region, "data_root": str(data_root), "raw_path": str(path),
            "snapshot_path": str(snapshot), "raw_sha256": hashlib.sha256(content).hexdigest()}


class PipelinePureTests(unittest.TestCase):
    def test_checksum_failure_precedes_database_access(self):
        with tempfile.TemporaryDirectory() as root:
            event = make_event(root, "udise", "03010100101", udise_raw("03010100101"))
            event["raw_sha256"] = "0" * 64
            with self.assertRaisesRegex(pipeline.PipelineValidationError, "checksum"):
                pipeline.import_scraped_record(None, event)

    def test_archive_preparation_preserves_bom_bytes(self):
        content = b'\xef\xbb\xbf{"metadata": {"academic_year": "2025-26"}}'
        record = prepare_json_record("data/Punjab/2025-26/a.json", content)
        self.assertEqual(record["content_sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(record["raw_json"].encode("utf-8"), content)
        self.assertEqual(record["dataset_layer"], "raw")


@unittest.skipUnless(os.getenv("SCHOOL_SCRAPING_TEST_DSN"), "Isolated PostgreSQL test DSN not supplied")
class PipelinePostgresTests(unittest.TestCase):
    def setUp(self):
        dsn = os.environ["SCHOOL_SCRAPING_TEST_DSN"]
        settings = conninfo_to_dict(dsn)
        # Refuse ordinary database configuration before making a connection.
        if settings.get("host") != "127.0.0.1" or settings.get("port") != "55439":
            self.skipTest("DSN is not the explicitly provisioned isolated test cluster")
        self.connection = psycopg.connect(dsn)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.rollback)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.identifier = str(uuid.uuid4().int % 10 ** 11).zfill(11)
        self.name = "Pipeline Test School " + uuid.uuid4().hex

    def event(self, year="2025-26", name=None, region="Punjab", offset=0):
        return make_event(self.root, "udise", self.identifier,
                          udise_raw(self.identifier, year, name or self.name), region, offset)

    def one(self, query, params=()):
        return self.connection.execute(query, params).fetchone()

    def test_replay_does_not_duplicate_sources_entities_or_archive(self):
        event = self.event()
        before = Path(event["raw_path"]).read_bytes()
        first = pipeline.import_scraped_record(self.connection, event)
        archive_count = self.one("SELECT count(*) FROM school_scraping.json_file_archive")[0]
        second = pipeline.import_scraped_record(self.connection, event)
        self.assertEqual(first["source_record_pk"], second["source_record_pk"])
        self.assertEqual(first["entity_id"], second["entity_id"])
        self.assertEqual(self.one("SELECT count(*) FROM school_scraping.json_file_archive")[0], archive_count)
        self.assertEqual(self.one("SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s",
                                  (self.identifier,))[0], 1)
        self.assertEqual(Path(event["raw_path"]).read_bytes(), before)

    def test_state_alias_reuses_existing_identity_and_preserves_previous_raw_archive(self):
        event = self.event()
        first = pipeline.import_scraped_record(self.connection, event)
        updated = self.event(name=self.name + " Updated", region="Haryana", offset=1)
        second = pipeline.import_scraped_record(self.connection, updated)
        self.assertEqual(second["source_record_pk"], first["source_record_pk"])
        self.assertEqual(second["region"], "punjab")
        self.assertEqual(self.one("SELECT school_name FROM school_scraping.school_source_records WHERE source_record_pk=%s",
                                  (first["source_record_pk"],))[0], self.name + " Updated")
        raw_json = self.one("SELECT raw_json FROM school_scraping.json_file_archive WHERE content_sha256=%s LIMIT 1",
                            (event["raw_sha256"],))[0]
        self.assertEqual(hashlib.sha256(raw_json.encode("utf-8")).hexdigest(), event["raw_sha256"])

    def test_older_year_keeps_newer_master_and_both_yearly_snapshots(self):
        newer = pipeline.import_scraped_record(self.connection,
                    self.event(year="2026-27", name=self.name + " New"))
        older = pipeline.import_scraped_record(self.connection,
                    self.event(year="2025-26", name=self.name + " Old", offset=1))
        self.assertTrue(older["preserved_newer_master"])
        self.assertEqual(self.one("SELECT school_name FROM school_scraping.school_source_records WHERE source_record_pk=%s",
                                  (newer["source_record_pk"],))[0], self.name + " New")
        years = self.connection.execute("SELECT academic_year FROM school_scraping.school_yearly_snapshots WHERE source_record_pk=%s ORDER BY academic_year",
                                        (newer["source_record_pk"],)).fetchall()
        self.assertEqual(years, [("2025-26",), ("2026-27",)])

    def test_same_path_update_keeps_original_raw_bytes_in_archive_history(self):
        event = self.event()
        pipeline.import_scraped_record(self.connection, event)
        original = Path(event["snapshot_path"]).read_bytes()
        replacement = self.event(name=self.name + " Changed", offset=1)
        pipeline.import_scraped_record(self.connection, replacement)
        logical_path = pipeline.logical_raw_path(event, Path(event["raw_path"]).resolve())
        historic_path = pipeline.history_path("udise", logical_path, event["raw_sha256"])
        self.assertEqual(self.one("SELECT raw_json FROM school_scraping.json_file_archive WHERE relative_path=%s",
                                  (historic_path,))[0].encode("utf-8"), original)
        self.assertEqual(self.one("SELECT content_sha256 FROM school_scraping.json_file_archive WHERE relative_path=%s",
                                  (logical_path,))[0], replacement["raw_sha256"])

    def test_stale_same_year_event_does_not_replace_newer_source_or_snapshot(self):
        first = pipeline.import_scraped_record(self.connection, self.event())
        stale = pipeline.import_scraped_record(self.connection,
                    self.event(name=self.name + " Stale", offset=-100))
        self.assertTrue(stale["preserved_newer_master"])
        self.assertFalse(stale["snapshot_updated"])
        self.assertEqual(self.one("SELECT school_name FROM school_scraping.school_source_records WHERE source_record_pk=%s",
                                  (first["source_record_pk"],))[0], self.name)

    def test_newer_academic_year_advances_canonical_even_if_observed_earlier(self):
        pipeline.import_scraped_record(self.connection, self.event(year="2025-26"))
        newer = pipeline.import_scraped_record(self.connection,
                    self.event(year="2026-27", name=self.name + " New Year", offset=-100))
        self.assertFalse(newer["preserved_newer_master"])
        archived = self.one("SELECT payload FROM school_scraping.json_file_archive WHERE file_id=%s",
                            (newer["canonical_archive_file_id"],))[0]
        self.assertEqual(archived["provenance"]["academic_year"], "2026-27")
        self.assertEqual(archived["identity_location"]["school_name"], self.name + " New Year")

    def test_failure_rolls_back_then_retry_recovers(self):
        event = self.event()
        with self.assertRaisesRegex(RuntimeError, "injected"):
            with self.connection.transaction():
                with patch.object(pipeline.loader, "load_yearly_snapshots", side_effect=RuntimeError("injected")):
                    pipeline.import_scraped_record(self.connection, event)
        self.assertEqual(self.one("SELECT count(*) FROM school_scraping.school_source_records WHERE udise_code=%s",
                                  (self.identifier,))[0], 0)
        recovered = pipeline.import_scraped_record(self.connection, event)
        self.assertEqual(recovered["status"], "imported")

    def test_approved_matching_and_shared_entity_are_preserved(self):
        first = pipeline.import_scraped_record(self.connection, self.event())
        affiliation = str(uuid.uuid4().int % 10 ** 7).zfill(7)
        saras_event = make_event(self.root, "saras", affiliation, saras_raw(affiliation, self.name))
        second = pipeline.import_scraped_record(self.connection, saras_event)
        self.assertEqual(second["match_candidates"], 1)
        self.connection.execute("UPDATE school_scraping.school_match_candidates SET match_status='approved', review_notes='keep review' WHERE udise_source_record_pk=%s",
                                (first["source_record_pk"],))
        self.connection.execute("UPDATE school_scraping.school_source_records SET entity_id=%s WHERE source_record_pk=%s",
                                (first["entity_id"], second["source_record_pk"]))
        self.connection.execute("UPDATE school_scraping.school_entities SET preferred_school_name='Reviewed master' WHERE entity_id=%s",
                                (first["entity_id"],))
        self.connection.execute("UPDATE school_scraping.school_source_records SET entity_resolution='{\"review\":\"approved\"}' WHERE source_record_pk=%s",
                                (first["source_record_pk"],))
        result = pipeline.import_scraped_record(self.connection, self.event(offset=1))
        self.assertEqual(self.one("SELECT preferred_school_name FROM school_scraping.school_entities WHERE entity_id=%s",
                                  (first["entity_id"],))[0], "Reviewed master")
        self.assertEqual(self.one("SELECT entity_resolution FROM school_scraping.school_source_records WHERE source_record_pk=%s",
                                  (first["source_record_pk"],))[0], {"review": "approved"})
        self.assertEqual(self.one("SELECT match_status, review_notes FROM school_scraping.school_match_candidates WHERE udise_source_record_pk=%s",
                                  (first["source_record_pk"],)), ("approved", "keep review"))
        self.assertEqual(result["entity_id"], first["entity_id"])

    def test_identity_ambiguity_requires_review(self):
        first = pipeline.import_scraped_record(self.connection, self.event())
        alias_archive = self.one("""INSERT INTO school_scraping.json_file_archive
            (relative_path,dataset_layer,source_system,region,file_name,content_sha256,file_size_bytes,json_root_type,raw_json,payload)
            SELECT relative_path || '.alias', dataset_layer,source_system,'alias',file_name,content_sha256,file_size_bytes,json_root_type,raw_json,payload
            FROM school_scraping.json_file_archive WHERE file_id=%s RETURNING file_id""",
            (first["canonical_archive_file_id"],))[0]
        self.connection.execute("""INSERT INTO school_scraping.school_source_records
            (entity_id,archive_file_id,source_system,region,source_record_id,school_name,normalized_school_name,udise_code)
            SELECT entity_id,%s,source_system,'alias',source_record_id,school_name,normalized_school_name,udise_code
            FROM school_scraping.school_source_records WHERE source_record_pk=%s""",
            (alias_archive, first["source_record_pk"]))
        with self.assertRaisesRegex(pipeline.PipelineValidationError, "manual review"):
            pipeline.import_scraped_record(self.connection, self.event(offset=1))
