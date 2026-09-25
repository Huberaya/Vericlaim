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

    assert "## Validation staging obligatoire" in text
    assert "Staging database migration" in text
    assert "## Déclenchement de migration production" in text
    assert text.index("## Validation staging obligatoire") < text.index("## Déclenchement de migration production")
