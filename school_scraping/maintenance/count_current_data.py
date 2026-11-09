"""Historical maintenance utility.

Run ``python -m school_scraping.maintenance.count_current_data --help``.
"""
import argparse

from school_scraping.datasets import PROJECT_ROOT

from pathlib import Path


DATASETS = {
    "UDISE RAW Chandigarh": (PROJECT_ROOT / Path("data/Chandigarh/2025-26")),
    "UDISE RAW Delhi": (PROJECT_ROOT / Path("data/Delhi/2025-26")),
    "UDISE RAW Mumbai": (PROJECT_ROOT / Path("data/Mumbai/2025-26")),

    "SARAS RAW Chandigarh": (PROJECT_ROOT / Path("data/CBSE_SARAS/Chandigarh/schools")),
    "SARAS RAW Delhi": (PROJECT_ROOT / Path("data/CBSE_SARAS/delhi/schools")),
    "SARAS RAW Mumbai": (PROJECT_ROOT / Path("data/CBSE_SARAS/mumbai/schools")),

    "UDISE CANONICAL Chandigarh": (PROJECT_ROOT / Path(
        "data/canonical_v4/udise/chandigarh/schools"
    )),
    "UDISE CANONICAL Delhi": (PROJECT_ROOT / Path(
        "data/canonical_v4/udise/delhi/schools"
    )),
    "UDISE CANONICAL Mumbai": (PROJECT_ROOT / Path(
        "data/canonical_v4/udise/mumbai/schools"
    )),

    "SARAS CANONICAL Chandigarh": (PROJECT_ROOT / Path(
        "data/canonical_v4/saras/chandigarh/schools"
    )),
    "SARAS CANONICAL Delhi": (PROJECT_ROOT / Path(
        "data/canonical_v4/saras/delhi/schools"
    )),
    "SARAS CANONICAL Mumbai": (PROJECT_ROOT / Path(
        "data/canonical_v4/saras/mumbai/schools"
    )),
}


def count_json_files(path: Path) -> int:
    """Count school JSON files and ignore summary/helper files."""
    if not path.exists():
        return 0

    return sum(
        1
        for file in path.glob("*.json")
        if not file.name.startswith("_")
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)

    print()
    print("=" * 65)
    print("CURRENT DATASET COUNT")
    print("=" * 65)

    raw_udise_total = 0
    raw_saras_total = 0

    canonical_udise_total = 0
    canonical_saras_total = 0

    for name, path in DATASETS.items():
        count = count_json_files(path)

        print(f"{name:<35} {count:>6}")

        if name.startswith("UDISE RAW"):
            raw_udise_total += count

        elif name.startswith("SARAS RAW"):
            raw_saras_total += count

        elif name.startswith("UDISE CANONICAL"):
            canonical_udise_total += count

        elif name.startswith("SARAS CANONICAL"):
            canonical_saras_total += count

    print("-" * 65)

    print(f"{'UDISE RAW TOTAL':<35} {raw_udise_total:>6}")
    print(f"{'SARAS RAW TOTAL':<35} {raw_saras_total:>6}")
    print(f"{'COMBINED RAW TOTAL':<35} {raw_udise_total + raw_saras_total:>6}")

    print()

    print(f"{'UDISE CANONICAL TOTAL':<35} {canonical_udise_total:>6}")
    print(f"{'SARAS CANONICAL TOTAL':<35} {canonical_saras_total:>6}")
    print(
        f"{'COMBINED CANONICAL TOTAL':<35} "
        f"{canonical_udise_total + canonical_saras_total:>6}"
    )

    print("=" * 65)


if __name__ == "__main__":
    main()