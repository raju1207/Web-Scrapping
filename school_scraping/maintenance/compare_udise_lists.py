"""Compare saved and fresh UDISE lists without changing either input.

Run ``python -m school_scraping.maintenance.compare_udise_lists --help``.
This replaces the three historical location-specific comparison copies.
"""
import argparse
import json
from pathlib import Path

from school_scraping.common import safe_filename
from school_scraping.datasets import DATA_ROOT, academic_year
from school_scraping.maintenance.audit_list_vs_raw import (
    NAME_KEYS, UDISE_KEYS, find_direct_value, load_json, location_name,
    normalize_udise, project_path,
)

DISTRICT_KEYS = ["districtName", "district_name", "district"]
BLOCK_KEYS = ["blockName", "block_name", "block"]


def first_value(record, keys):
    return find_direct_value(record, keys)


def extract_school_records(obj):
    records = []
    if isinstance(obj, dict):
        code = normalize_udise(first_value(obj, UDISE_KEYS))
        if code:
            records.append({"udise_code": code, "school_name": first_value(obj, NAME_KEYS),
                            "district": first_value(obj, DISTRICT_KEYS),
                            "block": first_value(obj, BLOCK_KEYS), "raw": obj})
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return records
    for child in children:
        if isinstance(child, (dict, list)):
            records.extend(extract_school_records(child))
    return records


def deduplicate(records):
    unique = {}
    for record in records:
        unique.setdefault(record["udise_code"], record)
    return unique


def compact_record(record):
    return {key: record.get(key) for key in ("udise_code", "school_name", "district", "block")}


def compare(location, saved_list, fresh_list):
    """Keep the legacy comparison report fields and first-record dedup policy."""
    old = deduplicate(extract_school_records(load_json(saved_list)))
    fresh = deduplicate(extract_school_records(load_json(fresh_list)))
    old_codes, fresh_codes = set(old), set(fresh)
    only_old = [compact_record(old[code]) for code in sorted(old_codes - fresh_codes)]
    only_fresh = [compact_record(fresh[code]) for code in sorted(fresh_codes - old_codes)]
    return {"location": location, "old_file": str(saved_list), "fresh_file": str(fresh_list),
            "summary": {"old_unique_udise_codes": len(old_codes),
                        "fresh_unique_udise_codes": len(fresh_codes),
                        "present_in_both": len(old_codes & fresh_codes),
                        "only_in_old": len(only_old), "only_in_fresh": len(only_fresh)},
            "only_in_old": only_old, "only_in_fresh": only_fresh,
            "common_udise_codes": sorted(old_codes & fresh_codes)}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", type=location_name, default="Chandigarh")
    parser.add_argument("--year", type=academic_year, default="2025-26",
                        help="Scope label for consistency; list paths retain their existing convention")
    parser.add_argument("--data-dir", type=Path, default=DATA_ROOT)
    parser.add_argument("--saved-list", type=Path, help="Previously saved school list")
    parser.add_argument("--fresh-list", type=Path, help="Fresh official school-list response")
    parser.add_argument("--report-file", type=Path, help="Destination JSON comparison report")
    parser.add_argument("--dry-run", action="store_true", help="Show paths without reading or writing data")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    data = project_path(args.data_dir)
    slug = safe_filename(args.location.casefold())
    saved = project_path(args.saved_list) if args.saved_list else data / f"{slug}_school_list.json"
    fresh = project_path(args.fresh_list) if args.fresh_list else data / "udise_fresh_lists" / f"{slug}_fresh.json"
    output = project_path(args.report_file) if args.report_file else data / "audit" / f"{slug}_fresh_list_comparison.json"
    if args.dry_run:
        print(json.dumps({"location": args.location, "year": args.year, "saved_list": str(saved),
                          "fresh_list": str(fresh), "report_file": str(output)}, indent=2))
        return 0
    if not saved.is_file() or not fresh.is_file():
        parser.error("Both saved and fresh lists must exist; supply explicit file paths if needed")
    report = compare(args.location, saved, fresh)
    print(f"UDISE+ {args.location.upper()} SCHOOL LIST COMPARISON")
    print(json.dumps(report["summary"], indent=2))
    for label, records in (("ONLY IN OLD LIST", report["only_in_old"]),
                           ("ONLY IN FRESH LIST", report["only_in_fresh"])):
        print(label)
        for record in records:
            print(f"{record['udise_code']} | {record['school_name']} | {record['district']} | {record['block']}")
        if not records:
            print("None")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Report saved: {output}")
    print("No existing UDISE data was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
