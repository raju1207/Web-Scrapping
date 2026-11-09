"""Discover source folders without changing the existing raw-data layout."""

import re
from pathlib import Path


DEFAULT_YEAR = "2025-26"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "data"


def academic_year(value):
    """Validate a folder-safe academic year for the processing CLIs."""
    if not re.fullmatch(r"\d{4}-\d{2}", str(value)):
        raise ValueError("Academic year must have the form 2025-26.")
    return str(value)


def discover_udise_datasets(data_root=DATA_ROOT, year=DEFAULT_YEAR):
    """Find data/<location>/<year>, including the original city folders."""
    year = academic_year(year)
    data_root = Path(data_root)
    if not data_root.is_dir():
        return {}
    return {
        folder.parent.name.lower(): folder
        for folder in sorted(data_root.glob(f"*/{year}"))
        if folder.is_dir()
    }


def discover_saras_datasets(data_root=DATA_ROOT):
    """Find data/CBSE_SARAS/<location>/schools for every collected scope."""
    root = Path(data_root) / "CBSE_SARAS"
    if not root.is_dir():
        return {}
    return {
        folder.parent.name.lower(): folder
        for folder in sorted(root.glob("*/schools"))
        if folder.is_dir()
    }


def school_file_count(folder):
    return sum(1 for path in Path(folder).glob("*.json")
               if not path.name.startswith("_"))


def record_year(raw, raw_file, fallback=DEFAULT_YEAR):
    """Prefer the scraper's year, then its verified year-folder convention."""
    metadata = raw.get("metadata") if isinstance(raw, dict) else None
    if isinstance(metadata, dict):
        value = metadata.get("academic_year") or metadata.get("session_year")
        if isinstance(value, str) and value.strip():
            return value.strip()
    folder_year = Path(raw_file).parent.name
    return folder_year if re.fullmatch(r"\d{4}-\d{2}", folder_year) else fallback


def validation_datasets(source, historical_counts, data_root=DATA_ROOT,
                        year=DEFAULT_YEAR):
    """Keep historic baselines and calculate expectations for new scopes."""
    data_root = Path(data_root)
    if source == "udise":
        folders = discover_udise_datasets(data_root, year)
    elif source == "saras":
        folders = discover_saras_datasets(data_root)
    else:
        raise ValueError(f"Unknown source: {source}")
    result = {}
    for name, folder in folders.items():
        canonical = data_root / "canonical_v4" / source / name
        if source == "saras":
            canonical /= "schools"
        expected = (historical_counts.get(name) if year == DEFAULT_YEAR else None)
        result[name] = {
            "raw": folder,
            "canonical": canonical,
            "expected": school_file_count(folder) if expected is None else expected,
            "academic_year": year,
        }
    return result
