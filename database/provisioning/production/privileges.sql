-- FUTURE initial grants only: the shared gate requires empty, owner-pinned tables.
\set ON_ERROR_STOP on
\set provisioning_stage privileges
\ir verify.sql

BEGIN;
SET LOCAL ROLE :"migration_role";
REVOKE ALL ON DATABASE :"approved_database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"approved_database" TO :"migration_role", :"scraper_role", :"report_role", :"backup_role";
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA school_scraping FROM PUBLIC, :"scraper_role", :"report_role", :"backup_role";
GRANT USAGE ON SCHEMA school_scraping TO :"scraper_role", :"report_role", :"backup_role";
REVOKE ALL ON ALL TABLES IN SCHEMA school_scraping FROM PUBLIC, :"scraper_role", :"report_role", :"backup_role";
REVOKE ALL ON ALL SEQUENCES IN SCHEMA school_scraping FROM PUBLIC, :"scraper_role", :"report_role", :"backup_role";

GRANT SELECT, INSERT, UPDATE ON TABLE
    school_scraping.json_file_archive, school_scraping.school_entities,
    school_scraping.school_source_records, school_scraping.school_yearly_snapshots,
    school_scraping.school_match_candidates, school_scraping.scrape_runs,
    school_scraping.scrape_tasks, school_scraping.scrape_errors,
    school_scraping.scrape_checkpoints TO :"scraper_role";
GRANT SELECT, INSERT ON TABLE school_scraping.scrape_events TO :"scraper_role";
GRANT SELECT ON ALL TABLES IN SCHEMA school_scraping TO :"report_role", :"backup_role";
-- Backup metadata only. Identity INSERT needs no scraper sequence grant.
GRANT SELECT ON ALL SEQUENCES IN SCHEMA school_scraping TO :"backup_role";
-- No ALTER DEFAULT PRIVILEGES or role memberships: review every future object.
COMMIT;
SELECT current_database() AS configured_database,
       'PRIVILEGES_STAGE_COMPLETED_PASSWORDS_AND_FULL_VERIFY_STILL_REQUIRED' AS result;
