-- FUTURE OPERATOR TEMPLATE ONLY. No production target is selected or approved.
-- Shared read-only gate. The three write wrappers set their own constant stage.
\set ON_ERROR_STOP on
\set VERBOSITY terse
\if :{?provisioning_stage}
\else
\set provisioning_stage verify
\endif
\unset _runtime_sslmode
\unset _runtime_sslrootcert
\getenv _runtime_sslmode PGSSLMODE
\getenv _runtime_sslrootcert PGSSLROOTCERT

SELECT :{?provisioning_approved} AND :{?approval_reference}
   AND :{?approved_host} AND :{?approved_port} AND :{?approved_server_major}
   AND :{?approved_system_id} AND :{?approved_data_directory}
   AND :{?approved_admin} AND :{?approved_database}
   AND :{?migration_role} AND :{?scraper_role} AND :{?report_role} AND :{?backup_role}
   AND :{?tls_policy} AND :{?_runtime_sslmode} AND :{?_runtime_sslrootcert}
   AS required_settings_present \gset
\if :required_settings_present
\else
SELECT CAST('BLOCKED_REQUIRED_SETTINGS_MISSING' AS integer);
\endif

SELECT :'provisioning_stage' IN ('bootstrap','migrate','privileges','verify') AS stage_valid,
       :'provisioning_stage' = 'bootstrap' AS stage_bootstrap,
       :'provisioning_stage' = 'migrate' AS stage_migrate,
       :'provisioning_stage' = 'privileges' AS stage_privileges,
       :'provisioning_stage' = 'verify' AS stage_verify \gset
\if :stage_valid
\else
SELECT CAST('BLOCKED_UNKNOWN_STAGE' AS integer);
\endif
\if :stage_bootstrap
SELECT :{?approved_maintenance_database} AND :{?approved_maintenance_oid}
       AS stage_settings_present \gset
\else
SELECT :{?new_database_oid} AS stage_settings_present \gset
\endif
\if :stage_settings_present
\else
SELECT CAST('BLOCKED_STAGE_IDENTITY_MISSING' AS integer);
\endif

BEGIN READ ONLY;
SELECT :'provisioning_approved' = 'yes' AND length(btrim(:'approval_reference')) > 0
   AND :'approved_host' = '127.0.0.1' AND :'approved_server_major' = '17'
   AND CASE WHEN :'approved_port' ~ '^[0-9]{1,5}$'
            THEN :'approved_port'::integer BETWEEN 1 AND 65535
                 AND :'approved_port'::integer NOT IN (5432,55439,55449,59487)
            ELSE false END
   AND :'approved_system_id' ~ '^[1-9][0-9]{0,19}$'
   AND length(btrim(:'approved_data_directory')) > 0
   AND replace(:'approved_data_directory',chr(92),'/') !~* '(^|/)\.refactor_validation(/|$)'
   AND :'approved_database' ~ '^[a-z_][a-z0-9_]{0,62}$'
   AND :'approved_database' !~ '^(postgres$|template[01]$|pg_|section|memory_|database_only_test)'
   AND (SELECT count(DISTINCT name)=4 AND bool_and(name ~ '^[a-z_][a-z0-9_]{0,62}$'
           AND name !~ '^(pg_|postgres$|section|memory_|database_only_test)'
           AND name <> :'approved_admin')
        FROM unnest(ARRAY[:'migration_role',:'scraper_role',:'report_role',:'backup_role']) AS names(name))
   AND :'tls_policy' = 'verify-full' AND :'_runtime_sslmode' = 'verify-full'
   AND length(btrim(:'_runtime_sslrootcert')) > 0
   AS approval_guard \gset
\if :approval_guard
\else
SELECT CAST('BLOCKED_APPROVAL_OR_TARGET_POLICY' AS integer);
\endif

-- SQL can observe encryption, not prove client CA/hostname verification.
-- The operator must independently review the actual client/libpq TLS settings.
SELECT current_user = :'approved_admin' AND session_user = :'approved_admin'
   AND (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)
   AND host(inet_server_addr()) = :'approved_host'
   AND inet_server_port()::text = :'approved_port'
   AND current_setting('server_version_num')::integer/10000 = 17
   AND (pg_control_system()).system_identifier::text = :'approved_system_id'
   AND lower(replace(current_setting('data_directory'),chr(92),'/'))
       = lower(replace(:'approved_data_directory',chr(92),'/'))
   AND current_setting('password_encryption') = 'scram-sha-256'
   AND current_setting('listen_addresses') = '127.0.0.1'
   AND current_setting('ssl') = 'on'
   AND EXISTS (SELECT 1 FROM pg_stat_ssl WHERE pid=pg_backend_pid() AND ssl)
   AND current_setting('fsync') = 'on' AND current_setting('full_page_writes') = 'on'
   AND current_setting('synchronous_commit') = 'on'
   AND NOT EXISTS (SELECT 1 FROM pg_hba_file_rules WHERE error IS NOT NULL)
   AND NOT EXISTS (SELECT 1 FROM pg_hba_file_rules
       WHERE type LIKE 'host%' AND auth_method <> 'reject'
         AND (type <> 'hostssl' OR auth_method <> 'scram-sha-256'
              OR address IS DISTINCT FROM '127.0.0.1' OR netmask IS DISTINCT FROM '255.255.255.255'))
   AND NOT EXISTS (SELECT 1 FROM pg_hba_file_rules
       WHERE type='local' AND auth_method NOT IN ('reject','scram-sha-256'))
   AS identity_guard \gset
\if :identity_guard
\else
SELECT CAST('BLOCKED_CLUSTER_ADMIN_TLS_AUTH_OR_DURABILITY_IDENTITY' AS integer);
\endif

\if :stage_bootstrap
SELECT current_database() = :'approved_maintenance_database'
   AND (SELECT oid::text FROM pg_database WHERE datname=current_database()) = :'approved_maintenance_oid'
   AND NOT EXISTS (SELECT 1 FROM pg_database WHERE datname=:'approved_database')
   AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname=ANY(
       ARRAY[:'migration_role',:'scraper_role',:'report_role',:'backup_role']))
   -- Bootstrap is fresh-cluster-only, never a script for a populated installation.
   AND NOT EXISTS (SELECT 1 FROM pg_database WHERE NOT datistemplate AND datname<>current_database())
   AND NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname !~ '^pg_' AND rolname<>current_user)
   AND NOT EXISTS (SELECT 1 FROM pg_namespace
       WHERE nspname !~ '^pg_' AND nspname NOT IN ('public','information_schema'))
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S','f'))
   AS bootstrap_guard \gset
\if :bootstrap_guard
\else
SELECT CAST('BLOCKED_NEW_CLUSTER_DATABASE_OR_ROLE_NAMES_NOT_FRESH' AS integer);
\endif
\else
SELECT current_database() = :'approved_database'
   AND (SELECT oid::text FROM pg_database WHERE datname=current_database()) = :'new_database_oid'
   AND (SELECT r.rolname FROM pg_database d JOIN pg_roles r ON r.oid=d.datdba
        WHERE d.datname=current_database()) = :'migration_role'
   AND (SELECT count(*)=4 AND bool_and(NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole
        AND NOT rolreplication AND NOT rolbypassrls AND NOT rolinherit
        AND rolcanlogin=(rolname IN (:'migration_role',:'scraper_role')))
        FROM pg_roles WHERE rolname=ANY(ARRAY[:'migration_role',:'scraper_role',:'report_role',:'backup_role']))
   AND NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
        WHERE r.rolname=ANY(ARRAY[:'migration_role',:'scraper_role',:'report_role',:'backup_role']))
   AND NOT EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.roleid
        WHERE r.rolname=ANY(ARRAY[:'migration_role',:'scraper_role',:'report_role',:'backup_role']))
   AS database_roles_guard \gset
\if :database_roles_guard
\else
SELECT CAST('BLOCKED_DATABASE_OID_OWNER_OR_ROLE_BOUNDARY' AS integer);
\endif
\if :stage_migrate
SELECT to_regnamespace('school_scraping') IS NULL AS empty_namespace_guard \gset
\if :empty_namespace_guard
\else
SELECT CAST('BLOCKED_APPLICATION_NAMESPACE_ALREADY_EXISTS' AS integer);
\endif
\else
SELECT (SELECT r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner
        WHERE n.nspname='school_scraping') = :'migration_role'
   AND (SELECT count(*)=10 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='school_scraping' AND c.relkind IN ('r','p'))
   AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY['json_file_archive','school_entities','school_source_records',
       'school_yearly_snapshots','school_match_candidates','scrape_runs','scrape_tasks','scrape_events',
       'scrape_errors','scrape_checkpoints']) AS expected(name)
       WHERE NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           WHERE n.nspname='school_scraping' AND c.relname=name AND c.relkind IN ('r','p')))
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='school_scraping'
       AND c.relkind IN ('r','p','S') AND r.rolname<>:'migration_role')
   AND (SELECT count(*)=32 FROM pg_indexes WHERE schemaname='school_scraping')
   AND (SELECT count(*)=38 FROM pg_constraint k JOIN pg_namespace n ON n.oid=k.connamespace
        WHERE n.nspname='school_scraping' AND k.convalidated)
   AND (SELECT count(*)=9 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_type t ON t.oid=a.atttypid
        WHERE n.nspname='school_scraping' AND a.attnum>0 AND NOT a.attisdropped AND t.typname='jsonb')
   AND NOT EXISTS (SELECT 1 FROM (VALUES
       ('json_file_archive','payload'),('school_source_records','entity_resolution'),
       ('school_yearly_snapshots','snapshot_payload'),('school_match_candidates','evidence'),
       ('scrape_runs','payload'),('scrape_tasks','payload'),('scrape_events','payload'),
       ('scrape_errors','payload'),('scrape_checkpoints','payload')) AS expected(table_name,column_name)
       WHERE NOT EXISTS (SELECT 1 FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
           JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_type t ON t.oid=a.atttypid
           WHERE n.nspname='school_scraping' AND c.relname=expected.table_name
             AND a.attname=expected.column_name AND a.attnum>0 AND NOT a.attisdropped AND t.typname='jsonb'))
   AND NOT EXISTS (SELECT 1 FROM (VALUES
       ('json_file_archive','p',ARRAY['file_id'],NULL::text,NULL::text[]),
       ('json_file_archive','u',ARRAY['relative_path'],NULL,NULL),
       ('school_entities','p',ARRAY['entity_id'],NULL,NULL),
       ('school_source_records','p',ARRAY['source_record_pk'],NULL,NULL),
       ('school_source_records','u',ARRAY['archive_file_id'],NULL,NULL),
       ('school_source_records','u',ARRAY['source_system','region','source_record_id'],NULL,NULL),
       ('school_yearly_snapshots','p',ARRAY['snapshot_id'],NULL,NULL),
       ('school_yearly_snapshots','u',ARRAY['source_record_pk','academic_year'],NULL,NULL),
       ('school_match_candidates','p',ARRAY['candidate_id'],NULL,NULL),
       ('school_match_candidates','u',ARRAY['udise_source_record_pk','saras_source_record_pk'],NULL,NULL),
       ('scrape_runs','p',ARRAY['run_id'],NULL,NULL),('scrape_tasks','p',ARRAY['task_id'],NULL,NULL),
       ('scrape_events','p',ARRAY['event_id'],NULL,NULL),('scrape_errors','p',ARRAY['error_id'],NULL,NULL),
       ('scrape_checkpoints','p',ARRAY['source','academic_year','school_identifier','scope_key'],NULL,NULL),
       ('school_entities','f',ARRAY['merged_into_entity_id'],'school_entities',ARRAY['entity_id']),
       ('school_source_records','f',ARRAY['entity_id'],'school_entities',ARRAY['entity_id']),
       ('school_source_records','f',ARRAY['archive_file_id'],'json_file_archive',ARRAY['file_id']),
       ('school_yearly_snapshots','f',ARRAY['source_record_pk'],'school_source_records',ARRAY['source_record_pk']),
       ('school_yearly_snapshots','f',ARRAY['source_archive_file_id'],'json_file_archive',ARRAY['file_id']),
       ('school_match_candidates','f',ARRAY['udise_source_record_pk'],'school_source_records',ARRAY['source_record_pk']),
       ('school_match_candidates','f',ARRAY['saras_source_record_pk'],'school_source_records',ARRAY['source_record_pk']),
       ('scrape_events','f',ARRAY['run_id'],'scrape_runs',ARRAY['run_id']),
       ('scrape_tasks','f',ARRAY['run_id'],'scrape_runs',ARRAY['run_id']),
       ('scrape_errors','f',ARRAY['error_id'],'scrape_events',ARRAY['event_id']),
       ('scrape_errors','f',ARRAY['run_id'],'scrape_runs',ARRAY['run_id']),
       ('scrape_errors','f',ARRAY['task_id'],'scrape_tasks',ARRAY['task_id'])
       ) AS expected(table_name,kind,column_names,referenced_table,referenced_columns)
       WHERE NOT EXISTS (SELECT 1 FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
           JOIN pg_namespace n ON n.oid=c.relnamespace
           LEFT JOIN pg_class rc ON rc.oid=k.confrelid LEFT JOIN pg_namespace rn ON rn.oid=rc.relnamespace
           WHERE n.nspname='school_scraping' AND c.relname=expected.table_name
             AND k.contype::text=expected.kind AND k.convalidated
             AND ARRAY(SELECT a.attname::text FROM unnest(k.conkey) WITH ORDINALITY x(attnum,pos)
                 JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.attnum ORDER BY x.pos)=expected.column_names
             AND (expected.kind<>'f' OR (rn.nspname='school_scraping' AND rc.relname=expected.referenced_table
                 AND ARRAY(SELECT a.attname::text FROM unnest(k.confkey) WITH ORDINALITY x(attnum,pos)
                     JOIN pg_attribute a ON a.attrelid=rc.oid AND a.attnum=x.attnum ORDER BY x.pos)=expected.referenced_columns))))
   AS schema_guard \gset
\if :schema_guard
\else
SELECT CAST('BLOCKED_REVIEWED_SCHEMA_OR_OWNER_CONTRACT' AS integer);
\endif
\if :stage_privileges
SELECT NOT EXISTS (SELECT 1 FROM school_scraping.json_file_archive)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.school_entities)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.school_source_records)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.school_yearly_snapshots)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.school_match_candidates)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.scrape_runs)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.scrape_tasks)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.scrape_events)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.scrape_errors)
   AND NOT EXISTS (SELECT 1 FROM school_scraping.scrape_checkpoints)
   AS empty_data_guard \gset
\if :empty_data_guard
\else
SELECT CAST('BLOCKED_INITIAL_GRANTS_REQUIRE_EMPTY_APPLICATION_TABLES' AS integer);
\endif
\endif
\endif
\endif

\if :stage_verify
SELECT (SELECT count(*)=2 AND bool_and((rolpassword LIKE 'SCRAM-SHA-256$%') IS TRUE)
        FROM pg_authid WHERE rolname IN (:'migration_role',:'scraper_role'))
   AND (SELECT bool_and(has_database_privilege(name,current_database(),'CONNECT'))
        FROM unnest(ARRAY[:'migration_role',:'scraper_role',:'report_role',:'backup_role']) AS roles(name))
   AND NOT has_database_privilege(:'scraper_role',current_database(),'CREATE')
   AND NOT has_schema_privilege(:'scraper_role','school_scraping','CREATE')
   AND NOT has_schema_privilege(:'scraper_role','public','CREATE')
   AND has_schema_privilege(:'scraper_role','school_scraping','USAGE')
   AND (SELECT bool_and(has_schema_privilege(name,'school_scraping','USAGE')
          AND NOT has_database_privilege(name,current_database(),'CREATE')
          AND NOT has_schema_privilege(name,'school_scraping','CREATE')
          AND NOT has_schema_privilege(name,'public','CREATE'))
        FROM unnest(ARRAY[:'report_role',:'backup_role']) AS readers(name))
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='school_scraping' AND c.relkind IN ('r','p') AND (
           NOT has_table_privilege(:'scraper_role',c.oid,'SELECT')
           OR NOT has_table_privilege(:'scraper_role',c.oid,'INSERT')
           OR has_table_privilege(:'scraper_role',c.oid,'DELETE')
           OR has_table_privilege(:'scraper_role',c.oid,'TRUNCATE')
           OR has_table_privilege(:'scraper_role',c.oid,'TRIGGER')
           OR has_table_privilege(:'scraper_role',c.oid,'MAINTAIN')
           OR has_any_column_privilege(:'scraper_role',c.oid,'REFERENCES')
           OR (c.relname='scrape_events' AND has_any_column_privilege(:'scraper_role',c.oid,'UPDATE'))
           OR (c.relname<>'scrape_events' AND NOT has_table_privilege(:'scraper_role',c.oid,'UPDATE'))))
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       CROSS JOIN unnest(ARRAY[:'report_role',:'backup_role']) AS readers(name)
       WHERE n.nspname='school_scraping' AND c.relkind IN ('r','p') AND (
           NOT has_table_privilege(readers.name,c.oid,'SELECT')
           OR has_any_column_privilege(readers.name,c.oid,'INSERT')
           OR has_any_column_privilege(readers.name,c.oid,'UPDATE')
           OR has_table_privilege(readers.name,c.oid,'DELETE')
           OR has_table_privilege(readers.name,c.oid,'TRUNCATE')
           OR has_table_privilege(readers.name,c.oid,'TRIGGER')))
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       CROSS JOIN unnest(ARRAY[:'report_role',:'backup_role']) AS readers(name)
       WHERE n.nspname='school_scraping' AND c.relkind IN ('r','p') AND (
           has_table_privilege(readers.name,c.oid,'MAINTAIN')
           OR has_any_column_privilege(readers.name,c.oid,'REFERENCES')))
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname='school_scraping' AND c.relkind='S' AND (
          has_sequence_privilege(:'scraper_role',c.oid,'USAGE')
          OR has_sequence_privilege(:'scraper_role',c.oid,'SELECT')
          OR has_sequence_privilege(:'scraper_role',c.oid,'UPDATE')
          OR has_sequence_privilege(:'report_role',c.oid,'SELECT')
          OR has_sequence_privilege(:'report_role',c.oid,'USAGE')
          OR has_sequence_privilege(:'report_role',c.oid,'UPDATE')
          OR has_sequence_privilege(:'backup_role',c.oid,'USAGE')
          OR has_sequence_privilege(:'backup_role',c.oid,'UPDATE')
          OR NOT has_sequence_privilege(:'backup_role',c.oid,'SELECT')))
   AS final_privilege_guard \gset
\if :final_privilege_guard
\else
SELECT CAST('BLOCKED_SCRAM_SECRET_SETUP_OR_MINIMUM_GRANTS' AS integer);
\endif
SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls,
       (rolpassword LIKE 'SCRAM-SHA-256$%') IS TRUE AS has_scram_password
FROM pg_authid WHERE rolname=ANY(ARRAY[:'migration_role',:'scraper_role',:'report_role',:'backup_role'])
ORDER BY rolname;
\endif
SELECT :'provisioning_stage' AS checked_stage,current_database() AS checked_database,
       (SELECT oid FROM pg_database WHERE datname=current_database()) AS checked_database_oid,
       current_user AS checked_administrator,inet_server_port() AS checked_port,
       'READ_ONLY_STAGE_GATE_PASSED_NOT_PRODUCTION_ACTIVATION' AS result;
COMMIT;
