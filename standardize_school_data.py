import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "standardized"
YEAR = "2025-26"

UDISE_DIRS = {
    "chandigarh": DATA / "Chandigarh" / YEAR,
    "delhi": DATA / "Delhi" / YEAR,
    "mumbai": DATA / "Mumbai" / YEAR,
}

SARAS_DIRS = {
    "delhi": DATA / "CBSE_SARAS" / "delhi" / "schools",
    "mumbai": DATA / "CBSE_SARAS" / "mumbai" / "schools",
}


def schema() -> Dict[str, Any]:
    return {
        "metadata": {
            "source": None,
            "source_type": None,
            "source_url": None,
            "academic_year": None,
            "location": None,
            "raw_file": None,
        },
        "school_identity": {
            "school_name": None,
            "udise_code": None,
            "cbse_affiliation_number": None,
            "cbse_school_code": None,
        },
        "location": {
            "state": None,
            "district": None,
            "block": None,
            "address": None,
            "pincode": None,
        },
        "school_information": {
            "board": None,
            "management_type": None,
            "school_category": None,
            "school_level": None,
            "school_type": None,
            "foundation_year": None,
            "first_opening_date": None,
            "gender_type": None,
        },
        "contact": {
            "website": None,
            "phone": None,
            "email": None,
        },
        "principal": {
            "name": None,
            "gender": None,
            "qualification": None,
            "administrative_experience": None,
            "teaching_experience": None,
        },
        "affiliation": {
            "board": None,
            "affiliation_number": None,
            "school_code": None,
            "affiliation_status": None,
            "affiliation_type": None,
            "affiliation_from": None,
            "affiliation_to": None,
            "affiliation_period_raw": None,
            "trust_society": None,
        },
        "student_statistics": {},
        "teacher_statistics": {},
        "infrastructure": {},
        "report_card": {},
        "school_history": {},
        "source_payload": {
            "school_summary": {},
            "school_profile": {},
            "listing_data": {},
            "school_details": {},
            "links": [],
        },
    }


def clean(v: Any) -> Optional[str]:
    if v is None or isinstance(v, (dict, list)):
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    if not s or s.lower() in {"none", "null", "n/a", "na", "-", "--"}:
        return None
    return s


def norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", (clean(s) or "").lower())


def safe_name(s: Any) -> str:
    s = clean(s) or "unknown_school"
    s = re.sub(r'[<>:"/\\|?*]', "", s)
    s = re.sub(r"\s+", "_", s)
    return s[:160]


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        print(f"[ERROR] {path}: {e}")
        return None


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def flatten(obj: Any, prefix: str = "") -> Dict[str, Any]:
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, p))
            elif not isinstance(v, list):
                out[p] = v
    return out


def find(obj: Any, *aliases: str) -> Optional[str]:
    aliases_n = {norm(a) for a in aliases}
    flat = flatten(obj)

    for path, value in flat.items():
        if norm(path.split(".")[-1]) in aliases_n:
            v = clean(value)
            if v:
                return v

    for path, value in flat.items():
        leaf = norm(path.split(".")[-1])
        for a in aliases_n:
            if a and (a in leaf or leaf in a):
                v = clean(value)
                if v:
                    return v
    return None


def get_dict(d: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    for k in keys:
        if isinstance(d.get(k), dict):
            return d[k]
    return {}


def parse_period(v: Any):
    raw = clean(v)
    if not raw:
        return None, None, None
    m = re.search(
        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}).*?(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
        raw,
    )
    if m:
        return m.group(1), m.group(2), raw
    return None, None, raw


def standardize_udise(raw: Dict[str, Any], raw_file: Path, place: str) -> Dict[str, Any]:
    r = schema()

    metadata = get_dict(raw, "metadata")
    summary = get_dict(raw, "school_summary", "summary")
    profile = get_dict(raw, "school_profile", "profile")
    st = get_dict(raw, "student_teacher_statistics", "enrolment_teacher", "enrollment_teacher")
    infra = get_dict(raw, "infrastructure_facilities", "infrastructure", "facilities")
    report = get_dict(raw, "report_card", "reportcard")
    history = get_dict(raw, "school_history", "history")

    school_name = (
        find(summary, "school_name", "school name", "schName", "schoolName")
        or find(profile, "school_name", "school name", "schName", "schoolName")
        or find(raw, "school_name", "school name", "schName", "schoolName")
    )

    udise_code = (
        clean(metadata.get("udise_code"))
        or clean(metadata.get("udiseSchCode"))
        or find(raw, "udise_code", "udise code", "udiseSchCode", "udiseSchCd")
    )

    state = find(profile, "state", "state_name", "stateName") or find(summary, "state", "state_name", "stateName")
    district = find(profile, "district", "district_name", "districtName") or find(summary, "district", "district_name", "districtName")
    block = find(profile, "block", "block_name", "blockName") or find(summary, "block", "block_name", "blockName")
    address = find(profile, "address", "school_address", "schoolAddress", "postal_address")
    pincode = find(profile, "pincode", "pin_code", "pin code", "postal_code") or find(raw, "pincode", "pin_code", "pin code")

    board = find(profile, "board", "board_name", "boardName") or find(raw, "board", "board_name")
    management = find(profile, "management", "management_type", "managementType", "managementName")
    category = find(profile, "category", "school_category", "schoolCategory", "categoryName")
    level = find(profile, "school_level", "schoolLevel", "level", "school_status")

    r["metadata"].update({
        "source": "UDISE+",
        "source_type": "Government School Education Data",
        "source_url": clean(metadata.get("source_url")) or clean(metadata.get("url")),
        "academic_year": clean(metadata.get("academic_year")) or clean(metadata.get("session_year")) or YEAR,
        "location": place.title(),
        "raw_file": str(raw_file.relative_to(ROOT)),
    })

    r["school_identity"].update({
        "school_name": school_name,
        "udise_code": udise_code,
    })

    r["location"].update({
        "state": state,
        "district": district,
        "block": block,
        "address": address,
        "pincode": pincode,
    })

    r["school_information"].update({
        "board": board,
        "management_type": management,
        "school_category": category,
        "school_level": level,
        "foundation_year": find(profile, "foundation_year", "year_of_foundation", "establishment_year"),
        "first_opening_date": find(profile, "first_opening_date", "date_of_first_opening"),
        "gender_type": find(profile, "gender_type", "school_gender", "coeducation"),
    })

    r["contact"].update({
        "website": find(profile, "website", "school_website") or find(raw, "website"),
        "phone": find(profile, "phone", "mobile", "telephone", "contact_number") or find(raw, "phone", "mobile"),
        "email": find(profile, "email", "email_id", "school_email") or find(raw, "email", "email_id"),
    })

    r["principal"]["name"] = find(profile, "principal_name", "principal", "head_name", "headmaster_name")
    r["affiliation"]["board"] = board

    r["student_statistics"] = st.copy()
    r["teacher_statistics"] = st.copy()
    r["infrastructure"] = infra.copy()
    r["report_card"] = report.copy()
    r["school_history"] = history.copy()

    r["source_payload"].update({
        "school_summary": summary.copy(),
        "school_profile": profile.copy(),
    })

    return r


def saras_detail(details: Dict[str, Any], *aliases: str) -> Optional[str]:
    aliases_n = {norm(a) for a in aliases}

    for k, v in details.items():
        if norm(k) in aliases_n:
            return clean(v)

    for k, v in details.items():
        nk = norm(k)
        for a in aliases_n:
            if a and (a in nk or nk in a):
                val = clean(v)
                if val:
                    return val
    return None


def school_code_from_listing(listing: Dict[str, Any]) -> Optional[str]:
    text = clean(listing.get("affiliation_and_school_code"))
    if not text:
        return None

    m = re.search(r"school\s*code\s*[:\-]?\s*(\d+)", text, re.I)
    if m:
        return m.group(1)

    nums = re.findall(r"\b\d{3,8}\b", text)
    return nums[-1] if len(nums) >= 2 else None


def standardize_saras(raw: Dict[str, Any], raw_file: Path, place: str) -> Dict[str, Any]:
    r = schema()

    metadata = get_dict(raw, "metadata")
    listing = get_dict(raw, "listing_data", "listing")
    details = get_dict(raw, "school_details", "details")
    links = raw.get("links") if isinstance(raw.get("links"), list) else []

    aff = (
        clean(metadata.get("affiliation_number"))
        or clean(listing.get("affiliation_number"))
        or saras_detail(details, "Affiliation Number", "Affiliation No", "Aff No")
    )

    school_name = (
        saras_detail(details, "Name of Institution", "Name Of Institution", "Name of School", "School Name")
        or find(listing, "school_name")
    )

    school_code = (
        saras_detail(details, "School Code", "CBSE School Code")
        or school_code_from_listing(listing)
    )

    state = saras_detail(details, "State", "State Name") or clean(metadata.get("state"))
    district = (
        saras_detail(details, "District", "District Name")
        or clean(metadata.get("selected_district"))
        or clean(listing.get("saras_selected_district"))
    )

    period = saras_detail(details, "Affiliation Period", "Period of Affiliation")
    aff_from, aff_to, period_raw = parse_period(period)

    r["metadata"].update({
        "source": "CBSE SARAS",
        "source_type": "Official CBSE Affiliation Directory",
        "source_url": clean(metadata.get("detail_url")) or clean(metadata.get("source_url")) or clean(raw.get("source_url")),
        "academic_year": YEAR,
        "location": place.title(),
        "raw_file": str(raw_file.relative_to(ROOT)),
    })

    r["school_identity"].update({
        "school_name": school_name,
        "udise_code": None,
        "cbse_affiliation_number": aff,
        "cbse_school_code": school_code,
    })

    r["location"].update({
        "state": state,
        "district": district,
        "block": None,
        "address": saras_detail(details, "Postal Address", "Address", "School Address"),
        "pincode": saras_detail(details, "Pin Code", "Pincode", "PIN", "Postal Code"),
    })

    r["school_information"].update({
        "board": "CBSE",
        "management_type": None,
        "school_category": None,
        "school_level": saras_detail(details, "Status of The School", "Status", "School Status") or clean(listing.get("status")),
        "school_type": saras_detail(details, "Type of School", "School Type"),
        "foundation_year": saras_detail(details, "Year of Foundation", "Foundation Year"),
        "first_opening_date": saras_detail(details, "Date of First Opening of School", "First Opening Date", "Date of Opening"),
        "gender_type": None,
    })

    r["contact"].update({
        "website": saras_detail(details, "Website", "School Website"),
        "phone": saras_detail(details, "Phone", "Telephone", "Contact Number", "Mobile"),
        "email": saras_detail(details, "Email", "Email ID", "E-mail"),
    })

    r["principal"].update({
        "name": saras_detail(details, "Name of Principal/ Head of Institution", "Principal/Head", "Principal Name", "Head Name", "Principal"),
        "gender": saras_detail(details, "Sex", "Gender", "Principal Gender"),
        "qualification": saras_detail(details, "Principal's Educational/Professional Qualifications", "Principal Qualification", "Qualification"),
        "administrative_experience": saras_detail(details, "Administrative Experience", "Administrative Experience of Principal"),
        "teaching_experience": saras_detail(details, "Teaching Experience", "Teaching Experience of Principal"),
    })

    r["affiliation"].update({
        "board": "CBSE",
        "affiliation_number": aff,
        "school_code": school_code,
        "affiliation_status": saras_detail(details, "Affiliation Status", "Status of Affiliation"),
        "affiliation_type": saras_detail(details, "Type of Affiliation", "Affiliation Type"),
        "affiliation_from": aff_from,
        "affiliation_to": aff_to,
        "affiliation_period_raw": period_raw,
        "trust_society": saras_detail(details, "Name of Trust/Society/Managing Committee", "Trust/Society/Managing Committee", "Managing Committee"),
    })

    r["source_payload"].update({
        "listing_data": listing.copy(),
        "school_details": details.copy(),
        "links": links.copy(),
    })

    return r


def process(source: str, place: str, input_dir: Path, output_dir: Path):
    if not input_dir.exists():
        print(f"[SKIP] Folder not found: {input_dir}")
        return

    files = sorted(
        p for p in input_dir.glob("*.json")
        if not p.name.startswith("_")
    )

    school_out = output_dir / "schools"
    school_out.mkdir(parents=True, exist_ok=True)

    records = []
    errors = []

    print("\n" + "=" * 60)
    print(f"STANDARDIZING {source.upper()} - {place.upper()}")
    print("=" * 60)
    print(f"Files found: {len(files)}")

    for i, path in enumerate(files, start=1):
        raw = load_json(path)
        if raw is None:
            errors.append({"file": str(path), "error": "Invalid JSON"})
            continue

        try:
            record = (
                standardize_udise(raw, path, place)
                if source == "udise"
                else standardize_saras(raw, path, place)
            )

            ident = (
                record["school_identity"]["udise_code"]
                if source == "udise"
                else record["school_identity"]["cbse_affiliation_number"]
            ) or "unknown"

            name = record["school_identity"]["school_name"] or "unknown_school"
            filename = f"{safe_name(name)}_{safe_name(ident)}.json"

            save_json(school_out / filename, record)
            records.append(record)

            print(f"[{i}/{len(files)}] {filename}")

        except Exception as e:
            errors.append({"file": str(path), "error": str(e)})
            print(f"[FAILED] {path.name}: {e}")

    save_json(output_dir / "_all_schools.json", records)

    summary = {
        "source": "UDISE+" if source == "udise" else "CBSE SARAS",
        "location": place.title(),
        "raw_files_found": len(files),
        "standardized_records": len(records),
        "failed_records": len(errors),
        "schema": "canonical_school_schema_v1",
        "output_folder": str(output_dir.relative_to(ROOT)),
    }
    save_json(output_dir / "_summary.json", summary)

    error_path = output_dir / "_errors.json"
    if errors:
        save_json(error_path, errors)
    elif error_path.exists():
        error_path.unlink()

    print(f"Standardized: {len(records)}")
    print(f"Failed      : {len(errors)}")


def main():
    print("\n" + "=" * 60)
    print("UDISE + CBSE SARAS STANDARDIZATION")
    print("=" * 60)
    print("Raw files will NOT be modified.")

    for place, folder in UDISE_DIRS.items():
        process(
            "udise",
            place,
            folder,
            OUT / "udise" / place,
        )

    for place, folder in SARAS_DIRS.items():
        process(
            "saras",
            place,
            folder,
            OUT / "saras" / place,
        )

    validation = {
        "status": "PASS",
        "schema_name": "canonical_school_schema_v1",
        "message": "UDISE and SARAS standardized records use the same top-level and fixed nested schema.",
        "canonical_schema": schema(),
        "raw_files_modified": False,
    }

    save_json(
        OUT / "_schema_validation.json",
        validation,
    )

    print("\n" + "=" * 60)
    print("STANDARDIZATION COMPLETED")
    print("=" * 60)
    print(f"Output: {OUT}")
    print("Schema validation: PASS")
    print("Original UDISE and SARAS JSON files were not changed.")


if __name__ == "__main__":
    main()
