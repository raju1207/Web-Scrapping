
import argparse
import os
import re
import sys
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlsplit

import psycopg
from psycopg.types.json import Jsonb
from dotenv import load_dotenv


# ---------------------------------------------------------
# 1. DATABASE CONNECTION
# ---------------------------------------------------------

def connect_database():
    """Connect to PostgreSQL using settings from the project .env file."""
    load_dotenv()

    required = [
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ]

    missing = [key for key in required if not os.getenv(key)]

    if missing:
        raise RuntimeError(
            "Missing database settings in .env: " + ", ".join(missing)
        )

    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


# ---------------------------------------------------------
# 2. GENERAL HELPERS
# ---------------------------------------------------------

def get_object(value, field_name):
    """Return a dictionary or raise a clear validation error."""
    if value is None:
        return {}

    if not isinstance(value, dict):
        raise ValueError(
            f"{field_name} must be a JSON object, "
            f"received {type(value).__name__}"
        )

    return value


def clean_text(value):
    """Convert a simple scalar value to trimmed text."""
    if value is None:
        return None

    if isinstance(value, (dict, list, tuple, bool)):
        raise ValueError(
            f"Expected a text/scalar value, received "
            f"{type(value).__name__}"
        )

    value = str(value).strip()

    return value if value else None


def normalize_name(value):
    """
    Normalize school names for candidate matching.

    Examples:
        M.G. School -> mg school
        MG School   -> mg school
        S.D. School -> sd school

    This is only used for candidate generation.
    Original school names are never modified.
    """
    if not value:
        return ""

    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)

    tokens = value.split()
    normalized = []

    i = 0

    while i < len(tokens):

        if len(tokens[i]) == 1:
            initials = []

            while i < len(tokens) and len(tokens[i]) == 1:
                initials.append(tokens[i])
                i += 1

            normalized.append("".join(initials))

        else:
            normalized.append(tokens[i])
            i += 1

    return " ".join(normalized)


def normalize_comparison(value):
    """Normalize text for address and pincode comparisons."""
    if not value:
        return ""

    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return " ".join(value.split())


def normalize_website(value):
    """Normalize website domains for comparison."""
    if not value:
        return ""

    value = str(value).strip()

    if not value:
        return ""

    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.IGNORECASE):
        value = "//" + value

    parsed = urlsplit(value)

    hostname = (parsed.hostname or "").casefold()
    hostname = hostname.removeprefix("www.").rstrip(".")

    if not hostname:
        return normalize_comparison(value)

    path = normalize_comparison(parsed.path)
    path = path.replace(" ", "")

    return hostname + ("/" + path if path else "")


def optional_match(left, right):
    """
    Compare two optional text values.

    Returns:
        True  = both values exist and match
        False = both values exist but differ
        None  = at least one value is missing
    """
    if not left or not right:
        return None

    return normalize_comparison(left) == normalize_comparison(right)


def optional_website_match(left, right):
    """Compare two optional website values."""
    if not left or not right:
        return None

    return normalize_website(left) == normalize_website(right)


def decimal_value(
    value,
    field_name,
    precision,
    scale,
    minimum=None,
    maximum=None,
):
    """Validate and fit a number into a PostgreSQL NUMERIC column."""
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise ValueError(
            f"{field_name}: boolean is not a valid number"
        )

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(
            f"{field_name}: invalid number {value!r}"
        )

    if not number.is_finite():
        raise ValueError(
            f"{field_name}: number must be finite"
        )

    try:
        quantum = Decimal("1").scaleb(-scale)
        number = number.quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
    except InvalidOperation:
        raise ValueError(
            f"{field_name}: number cannot fit the required precision"
        )

    integer_digits = max(number.copy_abs().adjusted() + 1, 0)

    if integer_digits > precision - scale:
        raise ValueError(
            f"{field_name}: {number} exceeds "
            f"NUMERIC({precision},{scale})"
        )

    if minimum is not None and number < Decimal(str(minimum)):
        raise ValueError(
            f"{field_name}: value cannot be below {minimum}"
        )

    if maximum is not None and number > Decimal(str(maximum)):
        raise ValueError(
            f"{field_name}: value cannot exceed {maximum}"
        )

    return number


def integer_value(value, field_name, minimum=None):
    """Validate an optional integer without silently truncating it."""
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise ValueError(
            f"{field_name}: boolean is not a valid integer"
        )

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(
            f"{field_name}: invalid integer {value!r}"
        )

    if not number.is_finite():
        raise ValueError(
            f"{field_name}: number must be finite"
        )

    if number != number.to_integral_value():
        raise ValueError(
            f"{field_name}: expected a whole number, got {value!r}"
        )

    if minimum is not None and number < Decimal(str(minimum)):
        raise ValueError(
            f"{field_name}: value cannot be below {minimum}"
        )

    return int(number)


# ---------------------------------------------------------
# 3. ARCHIVE PATH PATTERNS
# ---------------------------------------------------------

CANONICAL_PATH_PATTERN = (
    r"^data/canonical_v4/(udise|saras)/([^/]+)/schools/[^/]+\.json$"
)

SNAPSHOT_PATH_PATTERN = (
    r"^data/canonical_v4/udise/([^/]+)/_yearly_snapshots\.json$"
)


# ---------------------------------------------------------
# 4. READ CANONICAL SCHOOL RECORDS FROM THE ARCHIVE
# ---------------------------------------------------------

def fetch_canonical_records(conn):
    """Read and validate canonical records using the shared per-record parser."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT file_id, relative_path, payload "
            "FROM school_scraping.json_file_archive "
            "WHERE relative_path ~ %s ORDER BY relative_path",
            (CANONICAL_PATH_PATTERN,),
        )
        return [parse_canonical_record(file_id, path, payload)
                for file_id, path, payload in cur.fetchall()
                if re.match(CANONICAL_PATH_PATTERN, path)]


def parse_canonical_record(archive_file_id, relative_path, payload):
    """Validate and normalize one canonical payload for both ETL entry paths."""
    match = re.match(CANONICAL_PATH_PATTERN, relative_path)
    if not match:
        raise ValueError("Canonical record path does not match the existing layout")
    source_system = match.group(1)
    region = match.group(2).lower()

    if not isinstance(payload, dict):
        raise ValueError(
            f"Canonical payload is not a JSON object: "
            f"{relative_path}"
        )

    metadata = get_object(
        payload.get("metadata"),
        f"{relative_path}: metadata",
    )

    identity = get_object(
        payload.get("identity_location"),
        f"{relative_path}: identity_location",
    )

    affiliation = get_object(
        payload.get("board_affiliation"),
        f"{relative_path}: board_affiliation",
    )

    resolution = get_object(
        payload.get("entity_resolution"),
        f"{relative_path}: entity_resolution",
    )

    record_id = clean_text(
        metadata.get("record_id")
    )

    school_name = clean_text(
        identity.get("school_name")
    )

    if not record_id:
        raise ValueError(
            f"Missing metadata.record_id: {relative_path}"
        )

    if not school_name:
        raise ValueError(
            f"Missing identity_location.school_name: "
            f"{relative_path}"
        )

    udise_code = clean_text(
        metadata.get("udise_code")
    )

    affiliation_number = clean_text(
        affiliation.get("affiliation_number")
    )

    latitude = decimal_value(
        identity.get("latitude"),
        "latitude",
        10,
        7,
        minimum=-90,
        maximum=90,
    )

    longitude = decimal_value(
        identity.get("longitude"),
        "longitude",
        10,
        7,
        minimum=-180,
        maximum=180,
    )

    record = {
        "archive_file_id": archive_file_id,
        "relative_path": relative_path,
        "source_system": source_system,
        "region": region,
        "source_record_id": record_id,
        "school_name": school_name,
        "normalized_school_name": normalize_name(
            school_name
        ),
        "udise_code": udise_code,
        "cbse_affiliation_number": affiliation_number,
        "state": clean_text(identity.get("state")),
        "district": clean_text(identity.get("district")),
        "address": clean_text(identity.get("address")),
        "pincode": clean_text(identity.get("pincode")),
        "latitude": latitude,
        "longitude": longitude,
        "phone": clean_text(identity.get("phone")),
        "email": clean_text(identity.get("email")),
        "website": clean_text(identity.get("website")),
        "entity_resolution": resolution,
    }

    return record


# ---------------------------------------------------------
# 5. VALIDATE SOURCE RECORD IDENTITIES
# ---------------------------------------------------------

def validate_canonical_records(records):
    """Check duplicate source identities and archive IDs."""

    source_keys = set()
    archive_ids = set()

    for record in records:

        source_key = (
            record["source_system"],
            record["region"],
            record["source_record_id"],
        )

        if source_key in source_keys:
            raise ValueError(
                f"Duplicate source identity: {source_key}"
            )

        if record["archive_file_id"] in archive_ids:
            raise ValueError(
                f"Duplicate archive file ID: "
                f"{record['archive_file_id']}"
            )

        source_keys.add(source_key)
        archive_ids.add(record["archive_file_id"])

    return source_keys


# ---------------------------------------------------------
# 6. READ AND VALIDATE YEARLY SNAPSHOTS
# ---------------------------------------------------------

def fetch_snapshot_records(
    conn,
    source_keys,
    canonical_by_key,
):
    """Read yearly snapshot arrays and validate school mappings."""

    query = """
        SELECT file_id, relative_path, payload
        FROM school_scraping.json_file_archive
        WHERE relative_path ~ %s
        ORDER BY relative_path
    """

    snapshots = []
    seen_snapshot_keys = set()

    with conn.cursor() as cur:
        cur.execute(query, (SNAPSHOT_PATH_PATTERN,))

        for archive_file_id, relative_path, payload in cur.fetchall():

            match = re.match(
                SNAPSHOT_PATH_PATTERN,
                relative_path,
            )

            if not match:
                continue

            region = match.group(1).lower()

            if not isinstance(payload, list):
                raise ValueError(
                    f"Yearly snapshot payload is not a JSON array: "
                    f"{relative_path}"
                )

            for item in payload:

                if not isinstance(item, dict):
                    raise ValueError(
                        f"Snapshot item is not a JSON object: "
                        f"{relative_path}"
                    )

                record_id = clean_text(
                    item.get("record_id")
                )

                academic_year = clean_text(
                    item.get("academic_year")
                )

                udise_code = clean_text(
                    item.get("udise_code")
                )

                if not record_id or not academic_year:
                    raise ValueError(
                        f"Snapshot is missing record_id or "
                        f"academic_year: {relative_path}"
                    )

                source_key = (
                    "udise",
                    region,
                    record_id,
                )

                if source_key not in source_keys:
                    raise ValueError(
                        f"Snapshot has no matching canonical "
                        f"UDISE record: {region}, {record_id}"
                    )

                canonical_record = canonical_by_key[source_key]

                if udise_code != canonical_record["udise_code"]:
                    raise ValueError(
                        f"Snapshot UDISE code mismatch for "
                        f"{region}/{record_id}: "
                        f"snapshot={udise_code!r}, "
                        f"canonical={canonical_record['udise_code']!r}"
                    )

                snapshot_key = (
                    source_key,
                    academic_year,
                )

                if snapshot_key in seen_snapshot_keys:
                    raise ValueError(
                        f"Duplicate yearly snapshot: {snapshot_key}"
                    )

                seen_snapshot_keys.add(snapshot_key)

                snapshots.append({
                    "source_key": source_key,
                    "source_archive_file_id": archive_file_id,
                    "academic_year": academic_year,

                    "total_students": integer_value(
                        item.get("total_students"),
                        "total_students",
                        minimum=0,
                    ),

                    "total_teachers": integer_value(
                        item.get("total_teachers"),
                        "total_teachers",
                        minimum=0,
                    ),

                    "student_teacher_ratio": decimal_value(
                        item.get("student_teacher_ratio"),
                        "student_teacher_ratio",
                        12,
                        4,
                        minimum=0,
                    ),

                    "infra_score": decimal_value(
                        item.get("infra_score"),
                        "infra_score",
                        12,
                        4,
                        minimum=0,
                    ),

                    "pass_percentage_x": decimal_value(
                        item.get("pass_percentage_x"),
                        "pass_percentage_x",
                        7,
                        3,
                        minimum=0,
                        maximum=100,
                    ),

                    "pass_percentage_xii": decimal_value(
                        item.get("pass_percentage_xii"),
                        "pass_percentage_xii",
                        7,
                        3,
                        minimum=0,
                        maximum=100,
                    ),

                    "schema_version": clean_text(
                        item.get("schema_version")
                    ),

                    "source": clean_text(
                        item.get("source")
                    ),

                    "snapshot_payload": item,
                })

    return snapshots


# ---------------------------------------------------------
# 7. BUILD UDISE-SARAS MATCH CANDIDATES
# ---------------------------------------------------------

def build_match_candidates(records):
    """
    Find records with the same normalized school name and region.

    Important:
    These are candidates for manual review.
    They are NOT automatically merged.
    """

    udise_groups = defaultdict(list)
    saras_groups = defaultdict(list)

    for record in records:

        if not record["normalized_school_name"]:
            continue

        key = (
            record["region"],
            record["normalized_school_name"],
        )

        if record["source_system"] == "udise":
            udise_groups[key].append(record)

        elif record["source_system"] == "saras":
            saras_groups[key].append(record)

    candidates = []

    matching_keys = sorted(
        set(udise_groups) & set(saras_groups)
    )

    for key in matching_keys:

        for udise in udise_groups[key]:

            for saras in saras_groups[key]:

                candidates.append({
                    "udise": udise,
                    "saras": saras,

                    "pincode_match": optional_match(
                        udise["pincode"],
                        saras["pincode"],
                    ),

                    "address_match": optional_match(
                        udise["address"],
                        saras["address"],
                    ),

                    "website_match": optional_website_match(
                        udise["website"],
                        saras["website"],
                    ),
                })

    return candidates


# ---------------------------------------------------------
# 8. LOAD SOURCE RECORDS AND INITIAL ENTITIES
# ---------------------------------------------------------

def load_source_records(conn, records):
    """
    Load source records and create initial entities.

    Each new source record gets its own entity.
    UDISE and SARAS records are not automatically merged.

    Existing shared or merged entities are protected from
    being overwritten during subsequent loader runs.
    """

    source_record_map = {}

    with conn.cursor() as cur:

        for record in records:

            key = (
                record["source_system"],
                record["region"],
                record["source_record_id"],
            )

            cur.execute(
                """
                SELECT source_record_pk, entity_id
                FROM school_scraping.school_source_records
                WHERE source_system = %s
                  AND region = %s
                  AND source_record_id = %s
                """,
                key,
            )

            existing = cur.fetchone()

            if existing:

                source_record_pk, entity_id = existing

                # Update the entity only when it is still a
                # standalone active entity with one source record.
                # This prevents one source from overwriting a
                # manually consolidated UDISE/SARAS entity.

                cur.execute(
                    """
                    UPDATE school_scraping.school_entities AS e
                    SET preferred_school_name = %s,
                        region = %s,
                        state = %s,
                        district = %s,
                        address = %s,
                        pincode = %s,
                        latitude = %s,
                        longitude = %s,
                        phone = %s,
                        email = %s,
                        website = %s,
                        updated_at = NOW()
                    WHERE e.entity_id = %s
                      AND e.entity_status = 'active'
                      AND e.merged_into_entity_id IS NULL
                      AND (
                          SELECT COUNT(*)
                          FROM school_scraping.school_source_records AS sr
                          WHERE sr.entity_id = e.entity_id
                      ) = 1
                    """,
                    (
                        record["school_name"],
                        record["region"],
                        record["state"],
                        record["district"],
                        record["address"],
                        record["pincode"],
                        record["latitude"],
                        record["longitude"],
                        record["phone"],
                        record["email"],
                        record["website"],
                        entity_id,
                    ),
                )

                cur.execute(
                    """
                    UPDATE school_scraping.school_source_records
                    SET archive_file_id = %s,
                        school_name = %s,
                        normalized_school_name = %s,
                        udise_code = %s,
                        cbse_affiliation_number = %s,
                        state = %s,
                        district = %s,
                        address = %s,
                        pincode = %s,
                        latitude = %s,
                        longitude = %s,
                        phone = %s,
                        email = %s,
                        website = %s,
                        entity_resolution = %s,
                        updated_at = NOW()
                    WHERE source_record_pk = %s
                    """,
                    (
                        record["archive_file_id"],
                        record["school_name"],
                        record["normalized_school_name"],
                        record["udise_code"],
                        record["cbse_affiliation_number"],
                        record["state"],
                        record["district"],
                        record["address"],
                        record["pincode"],
                        record["latitude"],
                        record["longitude"],
                        record["phone"],
                        record["email"],
                        record["website"],
                        Jsonb(record["entity_resolution"]),
                        source_record_pk,
                    ),
                )

            else:

                # Create one initial entity for this source record.

                cur.execute(
                    """
                    INSERT INTO school_scraping.school_entities (
                        preferred_school_name,
                        region,
                        state,
                        district,
                        address,
                        pincode,
                        latitude,
                        longitude,
                        phone,
                        email,
                        website
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    RETURNING entity_id
                    """,
                    (
                        record["school_name"],
                        record["region"],
                        record["state"],
                        record["district"],
                        record["address"],
                        record["pincode"],
                        record["latitude"],
                        record["longitude"],
                        record["phone"],
                        record["email"],
                        record["website"],
                    ),
                )

                entity_id = cur.fetchone()[0]

                cur.execute(
                    """
                    INSERT INTO school_scraping.school_source_records (
                        entity_id,
                        archive_file_id,
                        source_system,
                        region,
                        source_record_id,
                        school_name,
                        normalized_school_name,
                        udise_code,
                        cbse_affiliation_number,
                        state,
                        district,
                        address,
                        pincode,
                        latitude,
                        longitude,
                        phone,
                        email,
                        website,
                        entity_resolution
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    RETURNING source_record_pk
                    """,
                    (
                        entity_id,
                        record["archive_file_id"],
                        record["source_system"],
                        record["region"],
                        record["source_record_id"],
                        record["school_name"],
                        record["normalized_school_name"],
                        record["udise_code"],
                        record["cbse_affiliation_number"],
                        record["state"],
                        record["district"],
                        record["address"],
                        record["pincode"],
                        record["latitude"],
                        record["longitude"],
                        record["phone"],
                        record["email"],
                        record["website"],
                        Jsonb(record["entity_resolution"]),
                    ),
                )

                source_record_pk = cur.fetchone()[0]

            source_record_map[key] = source_record_pk

    return source_record_map


# ---------------------------------------------------------
# 9. LOAD YEARLY SNAPSHOTS
# ---------------------------------------------------------

def load_yearly_snapshots(
    conn,
    snapshots,
    source_record_map,
):
    """Insert or update yearly snapshot records."""

    query = """
        INSERT INTO school_scraping.school_yearly_snapshots (
            source_record_pk,
            source_archive_file_id,
            academic_year,
            total_students,
            total_teachers,
            student_teacher_ratio,
            infra_score,
            pass_percentage_x,
            pass_percentage_xii,
            schema_version,
            source,
            snapshot_payload
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (source_record_pk, academic_year)
        DO UPDATE SET
            source_archive_file_id = EXCLUDED.source_archive_file_id,
            total_students = EXCLUDED.total_students,
            total_teachers = EXCLUDED.total_teachers,
            student_teacher_ratio = EXCLUDED.student_teacher_ratio,
            infra_score = EXCLUDED.infra_score,
            pass_percentage_x = EXCLUDED.pass_percentage_x,
            pass_percentage_xii = EXCLUDED.pass_percentage_xii,
            schema_version = EXCLUDED.schema_version,
            source = EXCLUDED.source,
            snapshot_payload = EXCLUDED.snapshot_payload,
            imported_at = NOW()
    """

    with conn.cursor() as cur:

        for snapshot in snapshots:

            source_record_pk = source_record_map[
                snapshot["source_key"]
            ]

            cur.execute(
                query,
                (
                    source_record_pk,
                    snapshot["source_archive_file_id"],
                    snapshot["academic_year"],
                    snapshot["total_students"],
                    snapshot["total_teachers"],
                    snapshot["student_teacher_ratio"],
                    snapshot["infra_score"],
                    snapshot["pass_percentage_x"],
                    snapshot["pass_percentage_xii"],
                    snapshot["schema_version"],
                    snapshot["source"],
                    Jsonb(snapshot["snapshot_payload"]),
                ),
            )


# ---------------------------------------------------------
# 10. LOAD MATCH CANDIDATES AS PENDING REVIEW
# ---------------------------------------------------------

def load_match_candidates(
    conn,
    candidates,
    source_record_map,
):
    """
    Store possible UDISE-SARAS matches.

    Existing approved/rejected decisions are preserved.
    Only pending_review candidate evidence is refreshed.
    """

    query = """
        INSERT INTO school_scraping.school_match_candidates (
            udise_source_record_pk,
            saras_source_record_pk,
            match_status,
            match_method,
            name_match,
            pincode_match,
            address_match,
            website_match,
            evidence
        )
        VALUES (
            %s, %s, 'pending_review', %s,
            TRUE, %s, %s, %s, %s
        )
        ON CONFLICT (
            udise_source_record_pk,
            saras_source_record_pk
        )
        DO UPDATE SET
            match_method = EXCLUDED.match_method,
            name_match = EXCLUDED.name_match,
            pincode_match = EXCLUDED.pincode_match,
            address_match = EXCLUDED.address_match,
            website_match = EXCLUDED.website_match,
            evidence = EXCLUDED.evidence,
            updated_at = NOW()
        WHERE school_match_candidates.match_status = 'pending_review'
    """

    with conn.cursor() as cur:

        for candidate in candidates:

            udise = candidate["udise"]
            saras = candidate["saras"]

            udise_key = (
                "udise",
                udise["region"],
                udise["source_record_id"],
            )

            saras_key = (
                "saras",
                saras["region"],
                saras["source_record_id"],
            )

            evidence = {
                "region": udise["region"],
                "udise_school_name": udise["school_name"],
                "saras_school_name": saras["school_name"],
                "udise_code": udise["udise_code"],
                "cbse_affiliation_number": (
                    saras["cbse_affiliation_number"]
                ),
                "udise_pincode": udise["pincode"],
                "saras_pincode": saras["pincode"],
                "udise_address": udise["address"],
                "saras_address": saras["address"],
                "udise_website": udise["website"],
                "saras_website": saras["website"],
            }

            cur.execute(
                query,
                (
                    source_record_map[udise_key],
                    source_record_map[saras_key],
                    "exact_normalized_name_and_region",
                    candidate["pincode_match"],
                    candidate["address_match"],
                    candidate["website_match"],
                    Jsonb(evidence),
                ),
            )


# ---------------------------------------------------------
# 11. MAIN
# ---------------------------------------------------------

def main():

    parser = argparse.ArgumentParser(
        description=(
            "Load canonical school records and yearly snapshots "
            "into PostgreSQL."
        )
    )

    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Write validated records to PostgreSQL. "
            "Without this option, run dry-run only."
        ),
    )

    args = parser.parse_args()

    print("Connecting to PostgreSQL...")

    apply_summary = None

    # The connection context commits on success and rolls back
    # automatically if any operation raises an exception.

    with connect_database() as conn:

        print("Reading canonical school records...")

        records = fetch_canonical_records(conn)

        if not records:
            raise RuntimeError(
                "No canonical school records found in "
                "json_file_archive."
            )

        source_keys = validate_canonical_records(records)

        canonical_by_key = {
            (
                record["source_system"],
                record["region"],
                record["source_record_id"],
            ): record
            for record in records
        }

        print("Reading and validating yearly snapshots...")

        snapshots = fetch_snapshot_records(
            conn,
            source_keys,
            canonical_by_key,
        )

        print("Building UDISE-SARAS match candidates...")

        candidates = build_match_candidates(records)

        # ---------------------------------------------
        # PRINT MATCH CANDIDATES
        # ---------------------------------------------

        print("\n---------- MATCH CANDIDATES ----------")

        for index, candidate in enumerate(
            candidates,
            start=1,
        ):

            udise = candidate["udise"]
            saras = candidate["saras"]

            print(f"\n{index}. Region: {udise['region']}")

            print(
                f"   UDISE: {udise['school_name']}"
            )

            print(
                f"   UDISE Code: {udise['udise_code']}"
            )

            print(
                f"   SARAS: {saras['school_name']}"
            )

            print(
                f"   CBSE Affiliation: "
                f"{saras['cbse_affiliation_number']}"
            )

            print(
                f"   Pincode Match: "
                f"{candidate['pincode_match']}"
            )

            print(
                f"   Address Match: "
                f"{candidate['address_match']}"
            )

            print(
                f"   Website Match: "
                f"{candidate['website_match']}"
            )

        print("\n-------------------------------------")

        # ---------------------------------------------
        # VALIDATION SUMMARY
        # ---------------------------------------------

        udise_count = sum(
            1
            for record in records
            if record["source_system"] == "udise"
        )

        saras_count = sum(
            1
            for record in records
            if record["source_system"] == "saras"
        )

        print("\n---------- VALIDATION SUMMARY ----------")

        print(f"Canonical UDISE records : {udise_count}")
        print(f"Canonical SARAS records : {saras_count}")
        print(f"Total source records    : {len(records)}")
        print(f"Yearly snapshots        : {len(snapshots)}")
        print(f"Match candidates        : {len(candidates)}")
        print("Duplicate source keys   : 0")
        print("Snapshot mapping errors : 0")

        print("----------------------------------------")

        # ---------------------------------------------
        # DRY RUN
        # ---------------------------------------------

        if not args.apply:

            print("\nDRY RUN SUCCESSFUL.")
            print("No records were inserted or updated.")
            print(
                "Review the candidates and summary before "
                "running with --apply."
            )

            return

        # ---------------------------------------------
        # APPLY TO DATABASE
        # ---------------------------------------------

        print("\nApplying records to PostgreSQL...")

        source_record_map = load_source_records(
            conn,
            records,
        )

        if len(source_record_map) != len(records):
            raise RuntimeError(
                "Source record mapping count does not match "
                "the canonical record count."
            )

        load_yearly_snapshots(
            conn,
            snapshots,
            source_record_map,
        )

        load_match_candidates(
            conn,
            candidates,
            source_record_map,
        )

        apply_summary = {
            "source_records": len(source_record_map),
            "snapshots": len(snapshots),
            "candidates": len(candidates),
        }

    # The connection context has exited successfully here.
    # PostgreSQL has committed the transaction.

    if apply_summary:

        print("\nDATABASE LOAD COMMITTED SUCCESSFULLY.")

        print(
            f"Source records processed : "
            f"{apply_summary['source_records']}"
        )

        print(
            f"Yearly snapshots processed: "
            f"{apply_summary['snapshots']}"
        )

        print(
            f"Match candidates checked : "
            f"{apply_summary['candidates']}"
        )

        print("All database writes were committed in one transaction.")


# ---------------------------------------------------------
# 12. ENTRY POINT
# ---------------------------------------------------------

if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(
            f"\nERROR: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

        sys.exit(1)
