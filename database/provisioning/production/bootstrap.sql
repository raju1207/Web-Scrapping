-- FUTURE OPERATOR TEMPLATE. Creates only fresh, explicitly approved names.
-- Do not invoke with psql --single-transaction/-1 or an outer transaction.
\set ON_ERROR_STOP on
\set AUTOCOMMIT on
\set provisioning_stage bootstrap
\ir verify.sql

BEGIN;
CREATE ROLE :"migration_role" LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE :"scraper_role" LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE :"report_role" NOLOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE :"backup_role" NOLOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOINHERIT NOREPLICATION NOBYPASSRLS;
COMMIT;

-- PostgreSQL forbids CREATE DATABASE inside a transaction. If this fails, STOP;
-- retain the password-disabled roles for review. Never auto-drop or reuse names.
CREATE DATABASE :"approved_database" WITH OWNER :"migration_role"
    TEMPLATE template0 ENCODING 'UTF8' ALLOW_CONNECTIONS false;
BEGIN;
REVOKE ALL ON DATABASE :"approved_database" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"approved_database" TO :"migration_role", :"scraper_role", :"report_role", :"backup_role";
ALTER DATABASE :"approved_database" ALLOW_CONNECTIONS true;
COMMIT;
SELECT datname AS created_database,oid AS new_database_oid,
       pg_get_userbyid(datdba) AS database_owner,
       'BOOTSTRAP_CREATED_PASSWORD_DISABLED_ROLES_REVIEW_AND_PIN_NEW_OID' AS result
FROM pg_database WHERE datname=:'approved_database';
