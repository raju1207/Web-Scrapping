import json
from pathlib import Path


DATA_DIR = Path("data") / "Chandigarh" / "2025-26"

SUMMARY_FILE = DATA_DIR / "_summary.json"
ALL_SCHOOLS_FILE = DATA_DIR / "_all_schools.json"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False
        )


def main():

    school_files = []

    for file in DATA_DIR.glob("*.json"):

        # Ignore helper files
        if file.name.startswith("_"):
            continue

        if file.name == "chandigarh_school_list.json":
            continue

        school_files.append(file)

    print(f"School JSON files found: {len(school_files)}")

    all_schools = []

    failed = []

    for file in school_files:

        try:

            data = load_json(file)

            metadata = data.get("metadata", {})
            summary = data.get("school_summary", {})

            record = {
                "school_name": metadata.get("school_name")
                    or summary.get("schoolName"),

                "udise_code": metadata.get("udise_code")
                    or summary.get("udiseschCode"),

                "school_id": metadata.get("school_id")
                    or summary.get("schoolId"),

                "academic_year": metadata.get("academic_year"),

                "state": metadata.get("state"),

                "file_name": file.name
            }

            all_schools.append(record)

        except Exception as error:

            failed.append({
                "file": file.name,
                "error": str(error)
            })

    # Sort by school name
    all_schools.sort(
        key=lambda x: str(
            x.get("school_name") or ""
        ).lower()
    )

    summary = {
        "state": "Chandigarh",
        "academic_year": "2025-26",
        "total_school_json_files": len(school_files),
        "successful_files": len(all_schools),
        "failed_files": len(failed)
    }

    save_json(
        ALL_SCHOOLS_FILE,
        all_schools
    )

    save_json(
        SUMMARY_FILE,
        summary
    )

    print()
    print("=" * 60)
    print("SUMMARY FILES CREATED")
    print("=" * 60)

    print(
        f"_all_schools.json : {len(all_schools)} schools"
    )

    print(
        f"_summary.json     : created"
    )

    print(
        f"Failed files      : {len(failed)}"
    )

    print()
    print("Saved in:")
    print(DATA_DIR)


if __name__ == "__main__":
    main()