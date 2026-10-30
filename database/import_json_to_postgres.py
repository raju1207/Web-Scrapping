
import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"
REPORT_PATH = PROJECT_ROOT / "reports" / "postgres_json_import_failures.csv"

TABLE = "school_scraping.json_file_archive"
BATCH_SIZE = 100

REGION_NAMES = {
    "chandigarh": "Chandigarh",
    "delhi": "Delhi",
    "mumbai": "Mumbai",
}

UPSERT_SQL = f"""
INSERT INTO {TABLE} (
    relative_path,
    dataset_layer,
    source_system,
    region,
    academic_year,
    file_name,
    content_sha256,
    file_size_bytes,
    json_root_type,
    raw_json,
    payload,
    file_modified_at
)
VALUES (
    %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s
)
ON CONFLICT (relative_path)
DO UPDATE SET
    dataset_layer = EXCLUDED.dataset_layer,
    source_system = EXCLUDED.source_system,
    region = EXCLUDED.region,
    academic_year = EXCLUDED.academic_year,
    file_name = EXCLUDED.file_name,
    content_sha256 = EXCLUDED.content_sha256,
    file_size_bytes = EXCLUDED.file_size_bytes,
    json_root_type = EXCLUDED.json_root_type,
    raw_json = EXCLUDED.raw_json,
    payload = EXCLUDED.payload,
    file_modified_at = EXCLUDED.file_modified_at,
    updated_at = NOW()
WHERE school_scraping.json_file_archive.content_sha256
      IS DISTINCT FROM EXCLUDED.content_sha256
"""


def reject_nonstandard_json_constant(value):
    raise ValueError(f"Invalid JSON constant: {value}")


def get_root_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"

    raise ValueError(f"Unsupported JSON root type: {type(value).__name__}")


def get_nested_value(data, keys):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)

    return current


def classify_file(relative_path, payload):
    parts = list(Path(relative_path).parts)

    # Relative paths are stored from the project root, e.g.
    # data/Chandigarh/2025-26/example.json
    data_parts = parts[1:] if parts and parts[0].lower() == "data" else parts
    lower_parts = [part.lower() for part in data_parts]

    top_folder = lower_parts[0] if lower_parts else ""
    file_name = Path(relative_path).name

    if top_folder == "audit":
        dataset_layer = "audit"
    elif top_folder == "canonical_v4":
        dataset_layer = "canonical"
    elif top_folder == "standardized":
        dataset_layer = "standardized"
    elif top_folder == "refresh_backups":
        dataset_layer = "backup"
    elif top_folder == "udise_fresh_lists":
        dataset_layer = "reference_list"
    elif top_folder == "cbse_saras":
        dataset_layer = "raw"
    elif top_folder in REGION_NAMES:
        dataset_layer = "raw"
    elif (
        len(data_parts) == 1
        and file_name.lower().endswith("_school_list.json")
    ):
        dataset_layer = "reference_list"
    else:
        dataset_layer = "other"

    if "saras" in lower_parts or "cbse_saras" in lower_parts:
        source_system = "CBSE_SARAS"
    elif (
        "udise" in lower_parts
        or top_folder in REGION_NAMES
        or top_folder == "udise_fresh_lists"
        or (
            len(data_parts) == 1
            and file_name.lower().endswith("_school_list.json")
        )
    ):
        source_system = "UDISE"
    else:
        # Audit files can describe multiple sources.
        source_system = "unknown"

    region = None

    for part in data_parts[:-1]:
        if part.lower() in REGION_NAMES:
            region = REGION_NAMES[part.lower()]
            break

    academic_year = None

    for part in data_parts:
        if re.fullmatch(r"\d{4}-\d{2}", part):
            academic_year = part
            break

    if isinstance(payload, dict) and not academic_year:
        year_paths = [
            ("metadata", "academic_year"),
            ("metadata", "academicYear"),
            ("metadata", "sessionYear"),
            ("provenance", "academic_year"),
            ("academic_year",),
        ]

        for keys in year_paths:
            value = get_nested_value(payload, keys)

            if value is not None and str(value).strip():
                academic_year = str(value).strip()
                break

    return dataset_layer, source_system, region, academic_year


def prepare_record(file_path):
    relative_path = file_path.relative_to(PROJECT_ROOT).as_posix()

    # Hash the exact file bytes so any file-content change is detectable.
    file_bytes = file_path.read_bytes()
    checksum = hashlib.sha256(file_bytes).hexdigest()

    # UTF-8 with optional BOM support.
    raw_json = file_bytes.decode("utf-8-sig")

    payload = json.loads(
        raw_json,
        parse_constant=reject_nonstandard_json_constant,
    )

    root_type = get_root_type(payload)
    layer, source, region, year = classify_file(relative_path, payload)
    stat = file_path.stat()

    modified_at = datetime.fromtimestamp(
        stat.st_mtime,
        tz=timezone.utc,
    )

    return {
        "relative_path": relative_path,
        "dataset_layer": layer,
        "source_system": source,
        "region": region,
        "academic_year": year,
        "file_name": file_path.name,
        "content_sha256": checksum,
        "file_size_bytes": len(file_bytes),
        "json_root_type": root_type,
        "raw_json": raw_json,
        "payload": payload,
        "file_modified_at": modified_at,
    }


def write_failure_report(failures):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with REPORT_PATH.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["relative_path", "error"],
        )
        writer.writeheader()
        writer.writerows(failures)


def print_summary(files_found, valid_count, layer_counts, failures):
    print("\n========== IMPORT SUMMARY ==========")
    print(f"JSON files selected: {files_found}")
    print(f"Valid JSON files:    {valid_count}")
    print(f"Failed files:        {len(failures)}")

    print("\nFiles by dataset layer:")

    for layer, count in sorted(layer_counts.items()):
        print(f"  {layer:<18} {count}")

    if failures:
        print(f"\nFailure report: {REPORT_PATH}")
    else:
        print("\nNo failures in this run.")


def main():
    parser = argparse.ArgumentParser(
        description="Import project JSON files into PostgreSQL."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate files without inserting or updating database rows.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N files, useful for testing.",
    )
    args = parser.parse_args()

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer.")

    if not DATA_ROOT.is_dir():
        raise SystemExit(f"Data folder not found: {DATA_ROOT}")

    files = sorted(DATA_ROOT.rglob("*.json"))

    if args.limit is not None:
        files = files[:args.limit]

    layer_counts = Counter()
    failures = []
    valid_count = 0
    inserted_or_updated = 0
    unchanged = 0

    print(f"Project: {PROJECT_ROOT}")
    print(f"Selected JSON files: {len(files)}")
    print(f"Dry run: {args.dry_run}")

    if args.dry_run:
        for file_path in files:
            try:
                record = prepare_record(file_path)
                valid_count += 1
                layer_counts[record["dataset_layer"]] += 1
            except Exception as exc:
                failures.append({
                    "relative_path": file_path.relative_to(
                        PROJECT_ROOT
                    ).as_posix(),
                    "error": str(exc)[:1000],
                })

        write_failure_report(failures)
        print_summary(len(files), valid_count, layer_counts, failures)
        print("\nDry run complete. No database rows were changed.")
        return

    load_dotenv(PROJECT_ROOT / ".env")

    required = [
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ]
    missing = [
        key for key in required
        if not os.getenv(key)
    ]

    if missing:
        raise SystemExit(
            "Missing environment variables in .env: "
            + ", ".join(missing)
        )

    try:
        port = int(os.environ["DB_PORT"])
    except ValueError:
        raise SystemExit("DB_PORT must be a valid integer.")

    connection_settings = {
        "host": os.environ["DB_HOST"],
        "port": port,
        "dbname": os.environ["DB_NAME"],
        "user": os.environ["DB_USER"],
        "password": os.environ["DB_PASSWORD"],
        "connect_timeout": 10,
    }

    with psycopg.connect(**connection_settings) as connection:
        with connection.cursor() as cursor:
            for start in range(0, len(files), BATCH_SIZE):
                batch = files[start:start + BATCH_SIZE]

                # One transaction per batch; individual rows use savepoints
                # so a single failed insert won't discard other good rows.
                with connection.transaction():
                    for file_path in batch:
                        relative_path = file_path.relative_to(
                            PROJECT_ROOT
                        ).as_posix()

                        try:
                            record = prepare_record(file_path)
                            valid_count += 1
                            layer_counts[record["dataset_layer"]] += 1

                            values = (
                                record["relative_path"],
                                record["dataset_layer"],
                                record["source_system"],
                                record["region"],
                                record["academic_year"],
                                record["file_name"],
                                record["content_sha256"],
                                record["file_size_bytes"],
                                record["json_root_type"],
                                record["raw_json"],
                                Jsonb(record["payload"]),
                                record["file_modified_at"],
                            )

                            with connection.transaction():
                                cursor.execute(UPSERT_SQL, values)

                            if cursor.rowcount == 1:
                                inserted_or_updated += 1
                            else:
                                unchanged += 1

                        except Exception as exc:
                            failures.append({
                                "relative_path": relative_path,
                                "error": str(exc)[:1000],
                            })

                completed = min(start + len(batch), len(files))
                print(
                    f"Processed {completed}/{len(files)} files | "
                    f"changed rows: {inserted_or_updated} | "
                    f"unchanged: {unchanged} | "
                    f"failures: {len(failures)}"
                )

    write_failure_report(failures)
    print_summary(len(files), valid_count, layer_counts, failures)
    print(f"Rows inserted or updated: {inserted_or_updated}")
    print(f"Unchanged rows skipped:   {unchanged}")


if __name__ == "__main__":
    main()
