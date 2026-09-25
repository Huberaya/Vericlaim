\set ON_ERROR_STOP on

-- Run once, only on a new VeriClaim database, as the Neon database owner.
-- Required psql variables: target_database, migrator_password, app_password.
\if :{?target_database}
\else
  \echo 'Missing required variable: target_database'
  \quit
\endif
\if :{?migrator_password}
\else
  \echo 'Missing required variable: migrator_password'
  \quit
\endif
\if :{?app_password}
\else
  \echo 'Missing required variable: app_password'
  \quit
\endif

BEGIN;

-- Refuse accidental execution against a populated application schema.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.tables
    WHERE table_schema = 'public'
      AND table_type = 'BASE TABLE'
      AND table_name <> 'alembic_version'
  ) THEN
    RAISE EXCEPTION 'Refusing bootstrap: public schema already contains application tables.';
  END IF;
END
$$;

-- Create credentials only once. Existing roles are normalized below but their
-- passwords are never overwritten by this bootstrap script.
SELECT format(
  'CREATE ROLE vericlaim_migrator LOGIN NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
  :'migrator_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vericlaim_migrator')
\gexec

SELECT format(
  'CREATE ROLE vericlaim_app LOGIN NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
  :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vericlaim_app')
\gexec

-- A non-superuser cannot safely normalize SUPERUSER/BYPASSRLS attributes on
-- a pre-existing role. Fail closed instead of weakening a privileged role.
DO $$
DECLARE
  candidate record;
BEGIN
  FOR candidate IN
    SELECT rolname, rolsuper, rolbypassrls
    FROM pg_roles
    WHERE rolname IN ('vericlaim_migrator', 'vericlaim_app')
  LOOP
    IF candidate.rolsuper OR candidate.rolbypassrls THEN
      RAISE EXCEPTION 'Role % is privileged; refuse to reuse it for VeriClaim.', candidate.rolname;
    END IF;
  END LOOP;
END
$$;

ALTER ROLE vericlaim_migrator LOGIN NOCREATEDB NOCREATEROLE NOINHERIT;
ALTER ROLE vericlaim_app LOGIN NOCREATEDB NOCREATEROLE NOINHERIT;

-- The runtime role cannot create arbitrary objects. The migration role owns
-- objects it creates through Alembic.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE :"target_database" TO vericlaim_migrator, vericlaim_app;
GRANT USAGE, CREATE ON SCHEMA public TO vericlaim_migrator;
GRANT USAGE ON SCHEMA public TO vericlaim_app;

COMMIT;

\echo 'Bootstrap complete. Run Alembic as vericlaim_migrator, then run 02_after_migration_grants.sql as vericlaim_migrator.'
