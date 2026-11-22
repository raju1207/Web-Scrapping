-- FUTURE first schema application only; partial/rerun states require review.
\set ON_ERROR_STOP on
\set provisioning_stage migrate
\ir verify.sql

-- One pinned administrator connection; no extra metadata grants to the owner.
-- SET ROLE establishes effective DDL ownership, not proof of LOGIN/HBA access.
SET ROLE :"migration_role";
\ir ../../schema.sql
\ir ../../schema_school_tables.sql
\ir ../../migrations/001_scraping_memory.sql
RESET ROLE;
SELECT current_database() AS migrated_database,
       'MIGRATE_STAGE_COMPLETED_ORIGINAL_DDL_TRANSACTION_BOUNDARIES_RETAINED' AS result;
