"""Compare saved UDISE lists with raw files.

Run ``python -m school_scraping.maintenance.audit_list_vs_raw --help``.
This consolidates the historical Chandigarh, Delhi and Mumbai audit copies.
"""
import argparse
import json
import re
from pathlib import Path

from school_scraping.common import safe_filename
from school_scraping.datasets import DATA_ROOT, PROJECT_ROOT, academic_year

UDISE_KEYS = ["udiseSchCode", "udiseschCode", "udise_code", "udiseCode",
              "udiseSchoolCode", "schoolUdiseCode"]
NAME_KEYS = ["schoolName", "school_name", "schName", "name"]


def project_path(value):
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def location_name(value):
    value = str(value).strip()
    if not value:
        raise argparse.ArgumentTypeError("Location must not be empty")
    return value.title()


def load_json(path):
    with Path(path).open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def normalize_udise(value):
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value).strip())
    return digits.zfill(11) if digits else None


def find_direct_value(record, keys):
    if isinstance(record, dict):
        for key in keys:
            value = record.get(key)
            if value is not None and str(value).strip():
                return value
    return None


def find_value(obj, keys):
    if isinstance(obj, dict):
        direct = find_direct_value(obj, keys)
        if direct is not None:
            return direct
        children = obj.values()
    elif isinstance(obj, list):
        children = obj
    else:
        return None
    for child in children:
        found = find_value(child, keys)
        if found is not None:
            return found
    return None


def extract_list_records(obj):
    records = {}

    def walk(value):
        if isinstance(value, dict):
            source_code = find_direct_value(value, UDISE_KEYS)
            code = normalize_udise(source_code) if source_code else None
            if code and code not in records:
                records[code] = {"udise_code": code,
                                 "school_name": find_direct_value(value, NAME_KEYS)}
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            return
        for child in children:
            if isinstance(child, (dict, list)):
                walk(child)

    walk(obj)
    return records


def scan_raw_school_files(raw_dir):
    records, invalid_files, duplicate_codes = {}, [], {}
    for path in Path(raw_dir).rglob("*.json"):
        if path.name.startswith("_") or "_errors" in path.parts:
            continue
        try:
            raw = load_json(path)
            code = normalize_udise(find_value(raw, UDISE_KEYS))
            if not code:
                invalid_files.append({"file": str(path), "error": "UDISE code not found"})
                continue
            record = {"udise_code": code, "school_name": find_value(raw, NAME_KEYS),
                      "file": str(path)}
            if code in records:
                duplicate_codes.setdefault(code, [records[code]]).append(record)
            else:
                records[code] = record
        except Exception as error:
            invalid_files.append({"file": str(path), "error": str(error)})
    return records, invalid_files, duplicate_codes


def audit(location, list_file, raw_dir):
    """Return the original report shape without writing any source/report files."""
    expected = extract_list_records(load_json(list_file))
    records, invalid, duplicates = scan_raw_school_files(raw_dir)
    expected_codes, raw_codes = set(expected), set(records)
    missing = [{"udise_code": code, "school_name": expected[code].get("school_name")}
               for code in sorted(expected_codes - raw_codes)]
    extra = [records[code] for code in sorted(raw_codes - expected_codes)]
    return {"location": location, "summary": {
        "school_list_count": len(expected_codes), "raw_school_count": len(raw_codes),
        "common_count": len(expected_codes & raw_codes), "missing_raw_count": len(missing),
        "extra_raw_count": len(extra), "invalid_raw_files": len(invalid),
        "duplicate_udise_codes": len(duplicates)},
        "missing_raw_records": missing, "extra_raw_records": extra,
        "invalid_files": invalid, "duplicate_codes": duplicates}


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--location", type=location_name, default="Chandigarh",
                        help="Location name (default Chandigarh); supports any explicit dataset paths")
    parser.add_argument("--year", type=academic_year, default="2025-26")
    parser.add_argument("--data-dir", type=Path, default=DATA_ROOT)
    parser.add_argument("--list-file", type=Path, help="Saved official UDISE school list")
    parser.add_argument("--raw-dir", type=Path, help="Individual raw school JSON directory")
    parser.add_argument("--report-file", type=Path, help="Destination JSON audit report")
    parser.add_argument("--dry-run", action="store_true", help="Show paths without reading or writing data")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    data = project_path(args.data_dir)
    slug = safe_filename(args.location.casefold())
    list_file = project_path(args.list_file) if args.list_file else data / f"{slug}_school_list.json"
    raw_dir = project_path(args.raw_dir) if args.raw_dir else data / safe_filename(args.location) / args.year
    report_file = project_path(args.report_file) if args.report_file else data / "audit" / f"{slug}_list_vs_raw_report.json"
    if args.dry_run:
        print(json.dumps({"location": args.location, "list_file": str(list_file),
                          "raw_dir": str(raw_dir), "report_file": str(report_file)}, indent=2))
        return 0
    if not list_file.is_file() or not raw_dir.is_dir():
        parser.error("The saved school list and raw directory must exist; use path overrides for other datasets")
    report = audit(args.location, list_file, raw_dir)
    print(f"{args.location.upper()} UDISE LIST VS RAW SCHOOL FILE AUDIT")
    print(json.dumps(report["summary"], indent=2))
    for label, records in (("MISSING SCHOOL RECORDS", report["missing_raw_records"]),
                           ("EXTRA RAW RECORDS", report["extra_raw_records"])):
        print(label)
        for record in records:
            print(f"{record['udise_code']} | {record.get('school_name')} | {record.get('file', '')}")
        if not records:
            print("None")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Report: {report_file}")
    print("No existing school data was modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
