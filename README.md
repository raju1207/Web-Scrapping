# School Data Scraping and Unified Database

## 1. Project Overview

This project collects, processes, standardizes, and manages school information from two sources:

- **UDISE+** – Unified District Information System for Education.
- **CBSE SARAS** – Central Board of Secondary Education affiliation records.

The project stores the original scraped JSON files, loads them into PostgreSQL, creates a unified school master database, identifies possible matches between sources, and provides a searchable REST API using FastAPI.

## 2. Project Objectives

- Collect school information from UDISE+ and CBSE SARAS.
- Preserve original scraped JSON data.
- Store and manage school records in PostgreSQL.
- Maintain source-specific school information.
- Organize yearly UDISE school snapshots.
- Identify possible duplicate schools across sources.
- Review and approve school matches manually.
- Provide searchable school records through REST API endpoints.

## 3. Technology Stack

| Technology | Purpose |
|---|---|
| Python | Scraping and data processing |
| PostgreSQL | Database storage |
| SQL | Database schema and queries |
| FastAPI | REST API development |
| Uvicorn | API server |
| Psycopg | PostgreSQL connectivity |
| python-dotenv | Environment configuration |
| Git and GitHub | Version control |

## 4. Data Sources

### UDISE+
Provides school information, including UDISE codes, school names, location details, and available yearly statistics.

### CBSE SARAS
Provides CBSE-affiliated school information, including affiliation numbers, school names, addresses, and contact details where available.

The two sources have different identifiers and data structures. The project maintains their source-specific records while creating a unified master-school view.

## 5. Database Architecture

**Database:** `school_scraping_db`

**Schema:** `school_scraping`

Main database objects:

| Table / View | Purpose |
|---|---|
| `json_file_archive` | Stores archived original JSON content and file metadata |
| `school_entities` | Maintains master school entities |
| `school_source_records` | Links UDISE and SARAS source records |
| `school_yearly_snapshots` | Stores yearly UDISE school data |
| `school_match_candidates` | Stores possible school matches and review decisions |
| `school_entity_merge_history` | Maintains an audit history of approved merges |
| `school_master_search` | Provides a unified search view for the API |

## 6. Data Processing Workflow

1. Collect school information from UDISE+ and CBSE SARAS.
2. Save the original scraped JSON files.
3. Import JSON files into the PostgreSQL archive.
4. Load canonical school records into normalized database tables.
5. Create yearly UDISE snapshots.
6. Identify possible matches using normalized school names and region.
7. Review candidate matches using available evidence.
8. Approve or reject matches through manual review.
9. Merge approved source records into common master entities.
10. Maintain merge history for audit purposes.
11. Expose the unified school data through FastAPI.

**Important:** The project does not automatically merge candidate schools. Human review is required before merging.

## 7. Current Database Statistics

| Metric | Count |
|---|---:|
| Archived JSON files | 17,226 |
| Canonical source records | 5,779 |
| Active master school entities | 5,770 |
| Merged entities | 9 |
| UDISE yearly snapshots | 3,260 |
| Match candidates | 12 |
| Approved matches | 9 |
| Candidates requiring more evidence | 3 |

These figures represent the current database state at the time of documentation.

## 8. FastAPI Endpoints

The project provides the following endpoints:

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/` | API welcome message |
| GET | `/health` | Checks database connectivity |
| GET | `/schools` | Searches and lists schools |
| GET | `/schools/{entity_id}` | Retrieves an individual school |

### Search API Examples

List schools:

`http://127.0.0.1:8000/schools?page=1&page_size=5`

Search by school name:

`http://127.0.0.1:8000/schools?q=Oxford`

Find schools available in both sources:

`http://127.0.0.1:8000/schools?has_udise=true&has_saras=true`

Retrieve an individual school:

`http://127.0.0.1:8000/schools/2555`

API documentation:

`http://127.0.0.1:8000/docs`

## 9. Local Setup

### Prerequisites

- Python 3
- PostgreSQL
- Git
- Required Python packages

### Clone the repository

```bash
git clone https://github.com/raju1207/Web-Scrapping.git
cd Web-Scrapping
```

### Create and activate a virtual environment

Windows PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### Install dependencies

```powershell
pip install -r requirements.txt
```

### Configure environment variables

Create a local `.env` file in the project root.

Configure these variables with your own PostgreSQL credentials:

```env
DB_HOST=localhost
DB_PORT=5432
DB_NAME=school_scraping_db
DB_USER=school_api_user
DB_PASSWORD=YOUR_LOCAL_PASSWORD
```

Never upload the `.env` file or database credentials to GitHub.

### Start the API

```powershell
python -m uvicorn database.api.main:app --reload
```

Open:

`http://127.0.0.1:8000/docs`

## 10. Data Integrity and Safety

- Original scraped JSON files are preserved.
- Source-specific UDISE and SARAS records are maintained.
- Missing values remain NULL rather than being replaced with zero.
- Candidate school matches require manual review.
- Approved merges are recorded in merge history.
- The API uses parameterized SQL queries.
- The API database connection is configured for read-only transactions.

## 11. Project Repository

GitHub:

https://github.com/raju1207/Web-Scrapping

## 12. Future Improvements

- Improve school matching using additional evidence.
- Add more validation and data-quality checks.
- Extend API filtering and search capabilities.
- Add API authentication and access control.
- Develop a frontend for school search and data visualization.
- Improve automated data refresh and monitoring.

---

**Project:** School Data Scraping and Unified Database  
**Data Sources:** UDISE+ and CBSE SARAS  
**Database:** PostgreSQL  
**API Framework:** FastAPI  
**Repository:** https://github.com/raju1207/Web-Scrapping
