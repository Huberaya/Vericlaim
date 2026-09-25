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

-- A database-name argument alone is not enough: protect against connecting to
-- one database while granting CONNECT on another one.
SELECT (current_database() = :'target_database') AS vericlaim_target_database_matches \gset
\if :vericlaim_target_database_matches
\else
  \echo 'Refusing bootstrap: current database does not match target_database.'
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

-- The landing zone may pre-provision these exact roles as NOLOGIN identities.
-- That is the only existing-role state accepted here. A LOGIN role would make
-- rerunning this file capable of silently rotating a live credential, so fail
-- closed before changing anything.
DO $$
DECLARE
  candidate record;
BEGIN
  FOR candidate IN
    SELECT rolname, rolcanlogin, rolsuper, rolbypassrls
    FROM pg_roles
    WHERE rolname IN ('vericlaim_migrator', 'vericlaim_app')
  LOOP
    IF candidate.rolsuper OR candidate.rolbypassrls THEN
      RAISE EXCEPTION 'Role % is privileged; refuse to reuse it for VeriClaim.', candidate.rolname;
    END IF;
    IF candidate.rolcanlogin THEN
      RAISE EXCEPTION 'Role % already has LOGIN; refusing to rotate an existing credential.', candidate.rolname;
    END IF;
  END LOOP;
END
$$;

-- For roles that do not exist, create the first credential immediately. For
-- the approved pre-provisioned NOLOGIN roles, the ALTER statements below set
-- their first credential exactly once and enable LOGIN.
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

SELECT format(
  'ALTER ROLE vericlaim_migrator LOGIN NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
  :'migrator_password'
)
WHERE EXISTS (
  SELECT 1
  FROM pg_roles
  WHERE rolname = 'vericlaim_migrator'
    AND NOT rolcanlogin
)
\gexec

SELECT format(
  'ALTER ROLE vericlaim_app LOGIN NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
  :'app_password'
)
WHERE EXISTS (
  SELECT 1
  FROM pg_roles
  WHERE rolname = 'vericlaim_app'
    AND NOT rolcanlogin
)
\gexec

-- Verify the credentials are now constrained. A non-superuser cannot safely
-- normalize SUPERUSER/BYPASSRLS, so reject privilege drift rather than weaken
-- a privileged identity.
DO $$
DECLARE
  candidate record;
BEGIN
  FOR candidate IN
    SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit
    FROM pg_roles
    WHERE rolname IN ('vericlaim_migrator', 'vericlaim_app')
  LOOP
    IF NOT candidate.rolcanlogin
      OR candidate.rolsuper
      OR candidate.rolbypassrls
      OR candidate.rolcreaterole
      OR candidate.rolcreatedb
      OR candidate.rolinherit THEN
      RAISE EXCEPTION 'Role % does not meet VeriClaim least-privilege bootstrap requirements.', candidate.rolname;
    END IF;
  END LOOP;
END
$$;

-- The runtime role cannot create arbitrary objects. The migration role owns
-- objects it creates through Alembic.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE :"target_database" TO vericlaim_migrator, vericlaim_app;
GRANT USAGE, CREATE ON SCHEMA public TO vericlaim_migrator;
GRANT USAGE ON SCHEMA public TO vericlaim_app;

COMMIT;

\echo 'Bootstrap complete. Run Alembic as vericlaim_migrator, then run 02_after_migration_grants.sql as vericlaim_migrator.'
