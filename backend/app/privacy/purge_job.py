"""Scheduled retention job: apply every organization's declared policy, and say so.

Run it on a timer (cron, systemd timer, Compose service). It is deliberately *not* a
loop inside the API process: a purge that runs in the process that serves traffic will
one day run twice, or not at all, and neither state is visible.

    python -m app.privacy.purge_job                # tout: applique ce qui est déclaré
    python -m app.privacy.purge_job --dry-run      # ne supprime rien
    python -m app.privacy.purge_job --org <uuid>   # une seule organisation

Exit code is 0 when every organization was processed, 1 when at least one failed: a
cron job that always exits 0 hides a purge that has been failing for months.
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal, create_tables
from app.core.logging import configure_logging
from app.documents.storage import build_object_storage
from app.models.domain import Organization, OrganizationStatus
from app.privacy import service as privacy


def run(*, organization_id: str | None, dry_run: bool) -> int:
    configure_logging(service="vericlaim-purge-job")
    storage = build_object_storage(settings)
    failures = 0

    with SessionLocal() as db:
        statement = select(Organization.id).where(Organization.status == OrganizationStatus.ACTIVE)
        if organization_id:
            statement = statement.where(Organization.id == organization_id)
        organization_ids = list(db.scalars(statement.order_by(Organization.id.asc())).all())

    print(f"{len(organization_ids)} organisation(s) à examiner, dry_run={dry_run}")
    for identifier in organization_ids:
        try:
            with SessionLocal() as db:
                plan = privacy.run_purge(
                    db,
                    storage=storage,
                    settings=settings,
                    organization_id=identifier,
                    actor_user_id=None,
                    dry_run=dry_run,
                )
            summary = {
                "organization_id": str(identifier),
                "policy_declared": plan.policy_declared,
                "purged": len(plan.purged),
                "kept": len(plan.kept),
                "storage_objects_deleted": sum(decision.storage_objects for decision in plan.purged),
                "not_enforced": [item["item"] for item in plan.not_enforced],
            }
            print(json.dumps(summary, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001 — one organization must not stop the rest
            failures += 1
            print(
                json.dumps(
                    {"organization_id": str(identifier), "error": type(exc).__name__, "detail": str(exc)[:300]},
                    ensure_ascii=False,
                ),
                file=sys.stderr,
            )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Applique la politique de rétention déclarée.")
    parser.add_argument("--dry-run", action="store_true", help="n'efface rien, décrit le plan")
    parser.add_argument("--org", default=None, help="limiter à une organisation (UUID)")
    parser.add_argument(
        "--create-schema",
        action="store_true",
        help="créer le schéma local avant de tourner (développement et tests seulement)",
    )
    arguments = parser.parse_args()
    if arguments.create_schema:
        create_tables()
    return run(organization_id=arguments.org, dry_run=arguments.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
