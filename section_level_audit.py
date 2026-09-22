from pathlib import Path
import json
from collections import Counter


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"


DATASETS = {
    "UDISE Chandigarh": DATA_DIR / "Chandigarh" / "2025-26",
    "UDISE Delhi": DATA_DIR / "Delhi" / "2025-26",
    "UDISE Mumbai": DATA_DIR / "Mumbai" / "2025-26",
}


SECTIONS = [
    "school_summary",
    "school_profile",
    "student_teacher_statistics",
    "infrastructure_facilities",
    "report_card",
    "school_history",
]


def get_school_files(folder):
    return sorted(
        file
        for file in folder.glob("*.json")
        if not file.name.startswith("_")
    )


def section_status(data, section_name):

    if section_name not in data:
        return "MISSING_SECTION"

    section = data[section_name]

    if section is None:
        return "NULL_SECTION"

    if isinstance(section, dict) and not section:
        return "EMPTY_SECTION"

    if isinstance(section, list) and not section:
        return "EMPTY_SECTION"

    return "AVAILABLE"


def get_udise_code(data):

    summary = data.get("school_summary")

    if not isinstance(summary, dict):
        return None

    value = summary.get("udiseschCode")

    if value is None:
        return None

    return str(value).strip()


def audit_dataset(dataset_name, folder):

    print()
    print("=" * 90)
    print(dataset_name)
    print("=" * 90)

    files = get_school_files(folder)

    print(f"Files: {len(files)}")

    section_counts = {
        section: Counter()
        for section in SECTIONS
    }

    problem_records = []

    for file_path in files:

        try:
            with file_path.open(
                "r",
                encoding="utf-8"
            ) as file:

                data = json.load(file)

        except Exception as error:

            problem_records.append({
                "file": file_path.name,
                "udise_code": None,
                "problem": "INVALID_JSON",
                "error": str(error)
            })

            continue

        udise_code = get_udise_code(data)

        record_problems = []

        for section in SECTIONS:

            status = section_status(
                data,
                section
            )

            section_counts[section][status] += 1

            if status != "AVAILABLE":

                record_problems.append({
                    "section": section,
                    "status": status
                })

        if record_problems:

            problem_records.append({
                "file": file_path.name,
                "udise_code": udise_code,
                "problems": record_problems
            })

    print()
    print("SECTION AVAILABILITY")
    print("-" * 90)

    print(
        f"{'Section':<32}"
        f"{'Available':>12}"
        f"{'Missing':>12}"
        f"{'Null':>10}"
        f"{'Empty':>10}"
    )

    print("-" * 76)

    for section in SECTIONS:

        counts = section_counts[section]

        print(
            f"{section:<32}"
            f"{counts['AVAILABLE']:>12}"
            f"{counts['MISSING_SECTION']:>12}"
            f"{counts['NULL_SECTION']:>10}"
            f"{counts['EMPTY_SECTION']:>10}"
        )

    print()
    print(
        f"Records with section problems: "
        f"{len(problem_records)}"
    )

    if problem_records:

        print()
        print("FIRST 10 PROBLEM RECORDS")
        print("-" * 90)

        for item in problem_records[:10]:

            print()
            print(
                f"File       : {item['file']}"
            )

            print(
                f"UDISE Code : {item.get('udise_code')}"
            )

            for problem in item.get(
                "problems",
                []
            ):

                print(
                    f"  - {problem['section']}: "
                    f"{problem['status']}"
                )

    return {
        "dataset": dataset_name,
        "folder": str(folder),
        "total_files": len(files),

        "section_counts": {
            section: dict(counts)
            for section, counts
            in section_counts.items()
        },

        "problem_records": problem_records
    }


def main():

    print()
    print("=" * 90)
    print("SCHOOLFINDER SECTION LEVEL AUDIT")
    print("=" * 90)

    all_reports = []

    for dataset_name, folder in DATASETS.items():

        report = audit_dataset(
            dataset_name,
            folder
        )

        all_reports.append(report)

    output_folder = DATA_DIR / "audit"

    output_folder.mkdir(
        parents=True,
        exist_ok=True
    )

    output_file = (
        output_folder /
        "section_level_audit_report.json"
    )

    with output_file.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            all_reports,
            file,
            indent=2,
            ensure_ascii=False
        )

    print()
    print("=" * 90)
    print("SECTION AUDIT COMPLETED")
    print("=" * 90)

    print(
        f"Report saved to: {output_file}"
    )

    print()
    print(
        "No raw school JSON files were modified."
    )


if __name__ == "__main__":
    main()