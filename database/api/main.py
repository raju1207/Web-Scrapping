
import os

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from psycopg.rows import dict_row

load_dotenv()

app = FastAPI(
    title="School Search API",
    description="Search UDISE+ and CBSE SARAS school records.",
    version="1.0.0",
)

VIEW_NAME = "school_scraping.school_master_search"


def get_connection():
    """Create a read-only PostgreSQL connection."""
    required_settings = [
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
    ]

    missing = [
        key for key in required_settings
        if not os.getenv(key)
    ]

    if missing:
        raise RuntimeError(
            f"Missing database settings: {', '.join(missing)}"
        )

    return psycopg.connect(
        host=os.getenv("DB_HOST"),
        port=int(os.getenv("DB_PORT")),
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        connect_timeout=5,
        options="-c default_transaction_read_only=on",
        row_factory=dict_row,
    )


@app.get("/")
def home():
    return {
        "message": "School Search API is running",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT current_database() AS database_name"
                )
                result = cur.fetchone()

        return {
            "status": "healthy",
            "database": result["database_name"],
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed",
        )


@app.get("/schools")
def search_schools(
    q: str | None = Query(default=None, description="School name or keyword"),
    region: str | None = None,
    udise_code: str | None = None,
    cbse_affiliation_number: str | None = None,
    pincode: str | None = None,
    has_udise: bool | None = None,
    has_saras: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    conditions = []
    params = []

    if q and q.strip():
        pattern = f"%{q.strip()}%"

        conditions.append("""
            (
                school_name ILIKE %s
                OR COALESCE(udise_school_name, '') ILIKE %s
                OR COALESCE(saras_school_name, '') ILIKE %s
                OR COALESCE(address, '') ILIKE %s
                OR COALESCE(district, '') ILIKE %s
                OR COALESCE(udise_code, '') ILIKE %s
                OR COALESCE(cbse_affiliation_number, '') ILIKE %s
            )
        """)

        params.extend([pattern] * 7)

    if region:
        conditions.append("region ILIKE %s")
        params.append(region.strip())

    if udise_code:
        conditions.append("udise_code = %s")
        params.append(udise_code.strip())

    if cbse_affiliation_number:
        conditions.append("cbse_affiliation_number = %s")
        params.append(cbse_affiliation_number.strip())

    if pincode:
        conditions.append("pincode = %s")
        params.append(pincode.strip())

    if has_udise is not None:
        conditions.append("has_udise = %s")
        params.append(has_udise)

    if has_saras is not None:
        conditions.append("has_saras = %s")
        params.append(has_saras)

    where_clause = (
        "WHERE " + " AND ".join(conditions)
        if conditions
        else ""
    )

    offset = (page - 1) * page_size

    count_sql = f"""
        SELECT COUNT(*) AS total
        FROM {VIEW_NAME}
        {where_clause}
    """

    search_sql = f"""
        SELECT
            entity_id,
            school_name,
            region,
            state,
            district,
            address,
            pincode,
            phone,
            email,
            website,
            udise_code,
            cbse_affiliation_number,
            source_record_count,
            has_udise,
            has_saras,
            source_systems
        FROM {VIEW_NAME}
        {where_clause}
        ORDER BY school_name, entity_id
        LIMIT %s OFFSET %s
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, params)
                total = cur.fetchone()["total"]

                cur.execute(
                    search_sql,
                    params + [page_size, offset],
                )
                results = cur.fetchall()

        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (
                (total + page_size - 1) // page_size
                if total else 0
            ),
            "results": results,
        }

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="School search failed",
        )


@app.get("/schools/{entity_id}")
def get_school(entity_id: int):
    sql = f"""
        SELECT *
        FROM {VIEW_NAME}
        WHERE entity_id = %s
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, [entity_id])
                result = cur.fetchone()

        if result is None:
            raise HTTPException(
                status_code=404,
                detail="School not found",
            )

        return result

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            status_code=500,
            detail="Unable to retrieve school",
        )
