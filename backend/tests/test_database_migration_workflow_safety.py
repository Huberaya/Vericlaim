from __future__ import annotations

import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPOSITORY_ROOT / ".github" / "workflows"


def _workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def _assert_manual_main_only_workflow(text: str, environment: str) -> None:
    assert "workflow_dispatch:" in text
    assert "push:" not in text
    assert "pull_request:" not in text
    assert "github.ref == 'refs/heads/main' && inputs.confirmation == 'APPLY'" in text
    assert f"environment: {environment}" in text
    assert "DATABASE_URL_MIGRATOR: ${{ secrets.DATABASE_URL_MIGRATOR }}" in text
    assert "DATABASE_URL_APP: ${{ secrets.DATABASE_URL_APP }}" in text
    assert "python -m alembic upgrade head" in text
    assert "python infra/neon/run_post_migration_grants.py" in text
    assert "python infra/neon/verify_runtime.py --expected-revision" in text


def test_staging_database_migration_is_manual_main_only_and_isolated() -> None:
    text = _workflow("staging-database-migration.yml")

    _assert_manual_main_only_workflow(text, "staging")
    assert "group: vericlaim-staging-database-migration" in text
    assert "GitHub staging environment" in text


def test_production_database_migration_remains_manual_and_protected() -> None:
    text = _workflow("production-database-migration.yml")

    _assert_manual_main_only_workflow(text, "production")
    assert "group: vericlaim-production-database-migration" in text
    assert "GitHub production environment" in text


def test_runbook_requires_staging_validation_before_production() -> None:
    text = (REPOSITORY_ROOT / "docs" / "operations" / "production-migration-runbook.md").read_text(
        encoding="utf-8"
    )

    assert "Continuous integration" in text
    assert "## Validation staging obligatoire" in text
    assert "Staging database migration" in text
    assert "## Déclenchement de migration production" in text
    assert text.index("## Validation staging obligatoire") < text.index("## Déclenchement de migration production")


def test_ci_runs_without_secrets_and_never_applies_a_database_migration() -> None:
    text = _workflow("continuous-integration.yml")

    assert "pull_request:" in text
    assert "push:" in text
    assert "workflow_dispatch:" in text
    assert "contents: read" in text
    assert "fetch-depth: 0" in text
    assert "APP_ENV: test" in text
    assert "DATABASE_URL: sqlite:////tmp/vericlaim-ci.db" in text
    assert "${{ secrets." not in text
    assert "python -m pytest -q" in text
    assert "python -m alembic upgrade head --sql" in text
    assert "python -m alembic upgrade head\n" not in text
    assert "ENABLE ROW LEVEL SECURITY" in text
    assert "FORCE ROW LEVEL SECURITY" in text
    assert "git grep -nE" in text



def test_staging_preflight_runs_before_first_migration_write() -> None:
    workflow = _workflow("staging-database-migration.yml")
    preflight = "python infra/neon/preflight_staging.py"
    migration = "python -m alembic upgrade head"

    assert preflight in workflow
    assert workflow.index(preflight) < workflow.index(migration)
    assert "STAGING_DATABASE_HOST: ${{ vars.STAGING_DATABASE_HOST }}" in workflow
    assert "STAGING_DATABASE_NAME: ${{ vars.STAGING_DATABASE_NAME }}" in workflow
    assert "PRODUCTION_DATABASE_HOST: ${{ vars.PRODUCTION_DATABASE_HOST }}" in workflow


def test_staging_preflight_fails_closed_on_missing_or_mismatched_target() -> None:
    text = (REPOSITORY_ROOT / "infra" / "neon" / "preflight_staging.py").read_text(encoding="utf-8")

    assert "STAGING_DATABASE_HOST" in text
    assert "STAGING_DATABASE_NAME" in text
    assert "actual_host != expected_host" in text
    assert "actual_database != expected_database" in text
    assert "production host" in text
    assert "require_tls(connection)" in text
    assert 'role != EXPECTED_ROLE' in text
    assert "rolsuper" in text
    assert "rolbypassrls" in text


def _alembic_head() -> str:
    """La tête réelle, lue dans la chaîne de migrations — jamais recopiée.

    Une constante écrite ici rougirait à chaque migration ajoutée, et la
    tentation serait alors de la mettre à jour sans regarder : c'est exactement
    le défaut que C24 a supprimé de la CI.
    """

    versions = REPOSITORY_ROOT / "backend" / "alembic" / "versions"
    revisions: set[str] = set()
    parents: set[str] = set()
    for path in versions.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"""^revision(?::\s*str)?\s*=\s*["']([0-9a-f]+)["']""", text, re.M):
            revisions.add(match.group(1))
        for match in re.finditer(r"""^down_revision(?::\s*[^=]+)?\s*=\s*["']([0-9a-f]+)["']""", text, re.M):
            parents.add(match.group(1))
    heads = revisions - parents
    assert len(heads) == 1, f"la chaîne doit avoir exactement une tête, obtenu : {sorted(heads)}"
    return heads.pop()


def test_neon_runbook_documents_current_alembic_head() -> None:
    text = (REPOSITORY_ROOT / "infra" / "neon" / "README.md").read_text(encoding="utf-8")

    assert f"--expected-revision {_alembic_head()}" in text
    assert "e8f9a1b2c3d4" not in text
    assert "preflight_staging.py" in text


def test_runtime_verification_covers_every_rls_protected_table() -> None:
    """Every table the migration chain protects with RLS must be asserted live.

    The staging verifier is the only control that proves RLS is active on the
    deployed database, so a table missing from its expectations is a silent
    blind spot (this test caught api_keys and legal_holds).
    """
    import importlib.util
    import re
    import sys

    versions = REPOSITORY_ROOT / "backend" / "alembic" / "versions"
    enabled: set[str] = set()
    for path in versions.glob("*.py"):
        enabled.update(
            re.findall(
                r'ALTER TABLE "([a-z_]+)" (?:ENABLE|FORCE) ROW LEVEL SECURITY',
                path.read_text(encoding="utf-8"),
            )
        )

    identity = versions / "7b3b4c985738_identity_and_tenant_security.py"
    spec = importlib.util.spec_from_file_location("vericlaim_identity_migration", identity)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    enabled.update(module.TENANT_TABLES)
    enabled.update(module.GLOBAL_OR_TENANT_RULE_TABLES)

    neon_directory = str(REPOSITORY_ROOT / "infra" / "neon")
    sys.path.insert(0, neon_directory)
    try:
        from verify_runtime import EXPECTED_RLS_TABLES
    finally:
        sys.path.remove(neon_directory)

    missing = enabled - set(EXPECTED_RLS_TABLES)
    assert not missing, f"RLS-protected tables missing from runtime verification: {sorted(missing)}"


def test_migration_workflows_never_allow_silent_schema_creation() -> None:
    """A migration job must never be able to create schema implicitly.

    The migration jobs run with APP_ENV=development so that Alembic can load
    application settings without requiring unrelated production secrets. That
    makes the permissive ``AUTO_CREATE_SCHEMA`` default apply, so every step
    must pin it to false explicitly. This test is the guard that stops the pin
    from being removed: without it, a shared database could be mutated outside
    the reviewed Alembic chain.
    """
    for name in ("production-database-migration.yml", "staging-database-migration.yml"):
        text = _workflow(name)
        app_env_steps = text.count("APP_ENV: development")
        pinned_steps = text.count('AUTO_CREATE_SCHEMA: "false"')

        assert app_env_steps > 0, f"{name} no longer sets APP_ENV; revisit this guard"
        assert pinned_steps >= app_env_steps, (
            f"{name}: every step that sets APP_ENV must also pin "
            f'AUTO_CREATE_SCHEMA: "false" (found {app_env_steps} APP_ENV steps '
            f"but only {pinned_steps} pins)"
        )
        assert 'AUTO_CREATE_SCHEMA: "true"' not in text
        assert "AUTO_CREATE_SCHEMA: true" not in text


def test_deployment_platform_must_not_rely_on_the_default_app_env() -> None:
    """The Vercel manifest must not silently serve the permissive default.

    A missing APP_ENV on the platform boots the application as `development`,
    which re-enables the credential-less pilot route and points at SQLite.
    The application now refuses that combination at startup; this test records
    the requirement on the deployment side too.
    """
    manifest = (REPOSITORY_ROOT / "vercel.json").read_text(encoding="utf-8")
    assert "APP_ENV" not in manifest, (
        "APP_ENV must be configured as a platform environment variable, not hardcoded in "
        "vercel.json where it could drift from the actual target environment"
    )
    checklist = REPOSITORY_ROOT / "docs" / "operations" / "release-checklist.md"
    assert checklist.exists(), "the release checklist is missing"
    checklist_text = checklist.read_text(encoding="utf-8")
    assert "APP_ENV" in checklist_text
