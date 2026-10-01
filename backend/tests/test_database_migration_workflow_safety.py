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
    assert "n'est pas configuré dans l'environnement GitHub staging" in text


def test_production_database_migration_remains_manual_and_protected() -> None:
    text = _workflow("production-database-migration.yml")

    _assert_manual_main_only_workflow(text, "production")
    assert "group: vericlaim-production-database-migration" in text
    assert "n'est pas configuré dans l'environnement GitHub production" in text


def test_runbook_requires_staging_validation_before_production() -> None:
    text = (REPOSITORY_ROOT / "docs" / "operations" / "production-migration-runbook.md").read_text(
        encoding="utf-8"
    )

    assert "Intégration continue" in text
    assert "## Validation staging obligatoire" in text
    assert "Migration de base — staging" in text
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
