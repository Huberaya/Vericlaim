-- Run after every reviewed Alembic upgrade, connected as vericlaim_migrator.
-- This script grants only runtime DML and deliberately makes audit envelopes
-- append-only for vericlaim_app.

BEGIN;

GRANT USAGE ON SCHEMA public TO vericlaim_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vericlaim_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vericlaim_app;

-- Alembic bookkeeping belongs to the migrator. The application may read the
-- current revision to fail closed at startup, but cannot alter migration state.
REVOKE ALL ON TABLE public.alembic_version FROM vericlaim_app;
GRANT SELECT ON TABLE public.alembic_version TO vericlaim_app;

-- Audit chains must remain append-only from the runtime role. The application
-- writes events through INSERT only; historical rows cannot be rewritten or
-- deleted by this database identity.
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_events FROM vericlaim_app;
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE public.audit_records FROM vericlaim_app;
GRANT SELECT, INSERT ON TABLE public.audit_events, public.audit_records TO vericlaim_app;

-- New migration-owned tables/sequences inherit least-privilege runtime access.
-- Re-run this file after each migration so sensitive future tables can receive
-- narrower grants than the generic DML baseline.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vericlaim_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO vericlaim_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT EXECUTE ON FUNCTIONS TO vericlaim_app;

-- The tenant-context helper is evaluated by RLS policies. It must be callable
-- by the runtime role but not exposed broadly by default.
DO $$
BEGIN
  IF to_regprocedure('public.vericlaim_current_organization_id()') IS NOT NULL THEN
    REVOKE ALL ON FUNCTION public.vericlaim_current_organization_id() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.vericlaim_current_organization_id() TO vericlaim_app;
  END IF;
END
$$;

COMMIT;
