from __future__ import annotations

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


def test_neon_runbook_documents_current_alembic_head() -> None:
    text = (REPOSITORY_ROOT / "infra" / "neon" / "README.md").read_text(encoding="utf-8")

    assert "--expected-revision e8f9a1b2c3d4" in text
    assert "d3c8a6e1b409" not in text
    assert "preflight_staging.py" in text
