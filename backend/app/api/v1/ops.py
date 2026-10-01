"""Operator-facing observability routes (chantier C16).

Two decisions shape this module.

**Where the alerts live.** The plan listed the alert sources (repeated job failure,
ClamAV/MinIO unreachable, saturated queue, invalid audit chain, `/readyz` failing)
without saying who may read them. `GET /api/v1/enterprise/alerts` already existed for
tenant administrators and returned two hardcoded literals; it now returns the real,
organisation-scoped alerts plus the instance's readiness, each labelled with its
scope. No new "platform operator" concept was invented: this codebase has no such
account type, and inventing one to hold five alert rules would be a role model added
for the convenience of a test.

**What is deliberately not here.** A Prometheus scrape endpoint is not an
authenticated API: an external scraper holds no VeriClaim session, and minting a
service account for it would be a real access-control decision, not a side effect of
this chantier. `/metrics` is therefore served **unauthenticated inside the platform
network**, and the only sensitive thing it exposes is volume and error counters —
no tenant identifier, no document title, no user address. That property is asserted
by a test: it is a security boundary, not a comment.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.alerting import evaluate_organization_alerts, evaluate_platform_alerts
from app.core.database import get_db
from app.core.metrics import REGISTRY
from app.identity.dependencies import (
    TenantPrincipal,
    request_id_from_request,
    require_permission,
)

logger = logging.getLogger("vericlaim.ops")

router = APIRouter(prefix="/api/v1/ops", tags=["operations"])

DATABASE_DEPENDENCY = Depends(get_db)
ORG_MANAGE_DEPENDENCY = Depends(require_permission("organization:manage"))


class OpsAlert(BaseModel):
    """An alert with the measurement that produced it, and its scope."""

    model_config = ConfigDict(extra="forbid")

    id: str
    severity: str
    category: str
    title: str
    message: str
    observed_value: float | str | None = None
    threshold: float | str | None = None
    is_acknowledged: bool = False
    scope: str = "organization"


class OpsAlertsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_alerts: list[OpsAlert]
    platform_alerts: list[OpsAlert]
    note: str = (
        "Les alertes d'organisation portent sur les données de votre organisation. Les alertes "
        "de plateforme concernent l'instance qui vous sert, pas votre organisation : elles sont "
        "identiques pour tous ses clients et ne remplacent pas une supervision externe — aucune "
        "notification (courriel, SMS, astreinte) n'est envoyée par ce code."
    )


class ClientErrorReport(BaseModel):
    """A browser-side error. Bounded and validated: this endpoint writes to the log."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=500)
    route: str = Field(min_length=1, max_length=200)
    error_name: str | None = Field(default=None, max_length=100)
    request_id: str | None = Field(default=None, max_length=128)


def _as_ops_alert(alert) -> OpsAlert:
    return OpsAlert(
        id=alert.id,
        severity=alert.severity,
        category=alert.category,
        title=alert.title,
        message=alert.message,
        observed_value=alert.observed_value,
        threshold=alert.threshold,
        is_acknowledged=alert.is_acknowledged,
        scope=alert.scope,
    )


@router.get("/alerts", response_model=OpsAlertsResponse)
def list_alerts(
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
    db: Session = DATABASE_DEPENDENCY,
) -> OpsAlertsResponse:
    """Organization alerts (real measurements) plus the instance's own readiness."""
    from app.core.config import settings
    from app.main import app as application

    return OpsAlertsResponse(
        organization_alerts=[
            _as_ops_alert(alert)
            for alert in evaluate_organization_alerts(db, organization_id=principal.organization_id)
        ],
        platform_alerts=[
            _as_ops_alert(alert)
            for alert in evaluate_platform_alerts(
                settings,
                storage=getattr(application.state, "document_storage", None),
                scanner=getattr(application.state, "document_scanner", None),
            )
        ],
    )


@router.get("/alert-rules")
def alert_rules() -> dict[str, object]:
    """Publish the thresholds, so an alert is never an opaque surprise."""
    from app.core.alerting import (
        QUEUE_DEPTH_CRITICAL,
        QUEUE_DEPTH_WARNING,
        QUEUE_STALL_SECONDS,
    )

    return {
        "rules": [
            {
                "id": "queue-depth",
                "observed": "travaux en attente ou en cours pour l'organisation",
                "warning_at": QUEUE_DEPTH_WARNING,
                "critical_at": QUEUE_DEPTH_CRITICAL,
            },
            {
                "id": "queue-stalled",
                "observed": "âge du plus ancien travail en attente (secondes)",
                "critical_at": QUEUE_STALL_SECONDS,
            },
            {
                "id": "job-failures",
                "observed": "travaux en échec ; critique dès qu'un travail a épuisé ses tentatives",
                "warning_at": 1,
            },
            {
                "id": "audit-chain",
                "observed": "résultat de GET /api/v1/audit/verify",
                "critical_at": "is_valid == false",
            },
            {
                "id": "platform-readiness",
                "observed": "résultat de GET /readyz",
                "critical_at": "toute dépendance requise en échec",
            },
        ],
        "not_implemented": [
            "aucune notification sortante (courriel, webhook, astreinte) : ces alertes sont "
            "consultables et scrapables, jamais poussées",
            "aucun acquittement persistant : le champ is_acknowledged est toujours faux",
        ],
    }


@router.post("/client-errors", status_code=status.HTTP_202_ACCEPTED)
def report_client_error(
    body: ClientErrorReport,
    request: Request,
    principal: TenantPrincipal = ORG_MANAGE_DEPENDENCY,
) -> dict[str, str]:
    """Collect a frontend error.

    Authenticated on purpose. An anonymous log-writing endpoint on the public internet
    is a log-injection and unbounded-growth vector, and this deployment has no rate
    limiting (see the C14 open decisions). Consequence, stated rather than hidden:
    **errors occurring before sign-in are not collected yet**.
    """
    logger.error(
        "Frontend error reported",
        extra={
            "event": "frontend_error",
            "frontend_message": body.message,
            "frontend_route": body.route,
            "frontend_error_name": body.error_name,
            "frontend_request_id": body.request_id,
            "organization_id": str(principal.organization_id),
            "request_id": request_id_from_request(request),
        },
    )
    REGISTRY.increment(
        "vericlaim_frontend_errors_total",
        {"route": body.route[:64] or "inconnue"},
    )
    return {"status": "recorded"}


@router.get("/metric-names")
def metric_names(response: Response) -> dict[str, object]:
    """List the metric families this process exposes, without scraping them."""
    rendered = REGISTRY.render()
    names = sorted(
        {
            line.split("{")[0].split(" ")[0]
            for line in rendered.splitlines()
            if line and not line.startswith("#")
        }
    )
    families = sorted({name.rsplit("_bucket", 1)[0].rsplit("_count", 1)[0].rsplit("_sum", 1)[0] for name in names})
    return {
        "families": families,
        "exposition": "/metrics",
        "note": (
            "Compteurs et histogrammes locaux au processus, remis à zéro au redémarrage ; "
            "les jauges métier (profondeur de file, âge du plus ancien travail) sont calculées "
            "depuis la base au moment du scrape. /metrics est servi sans authentification à "
            "l'intérieur du réseau : il ne contient aucun identifiant d'organisation, de "
            "document ni d'utilisateur."
        ),
    }
