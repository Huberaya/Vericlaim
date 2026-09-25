\set ON_ERROR_STOP on

-- Read-only verification. Run as vericlaim_app after migrations and grants.
-- Expected output: no bypass RLS; Alembic head d3c8a6e1b409; RLS enabled + forced.

BEGIN READ ONLY;

SELECT current_user AS runtime_role,
       r.rolsuper AS is_superuser,
       r.rolbypassrls AS bypasses_rls
FROM pg_roles AS r
WHERE r.rolname = current_user;

SELECT version_num AS alembic_revision FROM public.alembic_version;

SELECT c.relname AS table_name,
       c.relrowsecurity AS rls_enabled,
       c.relforcerowsecurity AS rls_forced,
       count(p.polname) AS policy_count
FROM pg_class AS c
JOIN pg_namespace AS n ON n.oid = c.relnamespace
LEFT JOIN pg_policy AS p ON p.polrelid = c.oid
WHERE n.nspname = 'public'
  AND c.relname IN (
    'suppliers', 'products', 'documents', 'document_versions',
    'document_segments', 'document_uploads', 'document_extraction_jobs',
    'analyses', 'analysis_versions', 'analysis_documents',
    'analysis_detection_jobs', 'claims', 'audit_events'
  )
GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity
ORDER BY c.relname;

SELECT has_table_privilege(current_user, 'public.audit_events', 'UPDATE') AS audit_events_update_allowed,
       has_table_privilege(current_user, 'public.audit_events', 'DELETE') AS audit_events_delete_allowed,
       has_table_privilege(current_user, 'public.audit_records', 'UPDATE') AS audit_records_update_allowed,
       has_table_privilege(current_user, 'public.audit_records', 'DELETE') AS audit_records_delete_allowed;

-- No organization context must expose zero tenant-owned rows.
SELECT set_config('app.current_organization_id', '', true);
SELECT 'suppliers' AS table_name, count(*) AS visible_rows_without_tenant FROM public.suppliers
UNION ALL
SELECT 'products', count(*) FROM public.products
UNION ALL
SELECT 'documents', count(*) FROM public.documents
UNION ALL
SELECT 'analyses', count(*) FROM public.analyses;

ROLLBACK;
