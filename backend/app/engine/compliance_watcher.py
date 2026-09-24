from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import MonitoredTarget, MonitoringLog
from app.core.webhooks import dispatch_webhook_event
from app.engine.url_scraper import EcommerceUrlScraper
from app.models.legal_types import EvidenceDossier, OverallCompliance, Surface
from app.models.schemas import AuditContext

logger = logging.getLogger(__name__)


async def execute_watcher_check(
    db: Session,
    target: MonitoredTarget,
    evaluator: Any,
    execute_evaluation_func: Any,
    scraper: EcommerceUrlScraper | None = None,
) -> MonitoringLog:
    """
    Exécute un contrôle planifié de surveillance sur une cible e-commerce.
    Détecte les régressions réglementaires et consigne l'historique d'audit.
    """
    url_scraper = scraper or EcommerceUrlScraper()
    now = datetime.now(timezone.utc)

    extracted_text, doc_sha, meta = await url_scraper.scrape(target.url, render_js=True)

    ctx = AuditContext(
        surface=Surface.PACKAGING,
        product_identifier=meta.get("page_title") or target.name,
        supplier_name=target.name,
    )
    dossier = EvidenceDossier(items=[], legal_person=True)

    evaluation = execute_evaluation_func(
        evaluator=evaluator,
        source_text=extracted_text,
        context=ctx,
        evidence=dossier,
        db=db,
        extraction_method="WATCHER_CRON",
        document_sha256=doc_sha,
        organization_id=target.organization_id,
    )

    claims_list = list({ev.claim_text for ev in evaluation.evaluations})
    new_violations = evaluation.violations_count
    new_status = evaluation.overall_compliance.value

    # Détection de régression
    delta_status = "UNCHANGED"
    regression = False

    if target.last_status == "PENDING" or target.last_audit_id is None:
        delta_status = "INITIAL"
        if new_violations > 0:
            regression = True
    elif (target.last_violations_count or 0) < new_violations:
        delta_status = "REGRESSION"
        regression = True
    elif (target.last_violations_count or 0) > 0 and new_violations == 0:
        delta_status = "RESOLVED"
    elif target.last_status != "NON_COMPLIANT" and new_status == "NON_COMPLIANT":
        delta_status = "REGRESSION"
        regression = True

    # Mise à jour de la cible
    target.last_checked_at_utc = now
    target.next_check_due_utc = now + timedelta(hours=target.frequency_hours)
    target.last_status = new_status
    target.last_risk_score = evaluation.risk_score
    target.last_violations_count = new_violations
    target.last_audit_id = evaluation.audit_trail.audit_id
    target.regression_detected = regression

    # Création du log de surveillance
    log_id = str(uuid4())
    log_entry = MonitoringLog(
        id=log_id,
        target_id=target.id,
        executed_at_utc=now,
        overall_compliance=new_status,
        risk_score=evaluation.risk_score,
        violations_count=new_violations,
        detected_claims=claims_list,
        audit_id=evaluation.audit_trail.audit_id,
        delta_status=delta_status,
    )
    db.add(log_entry)
    db.commit()

    # Déclenchement des alertes Webhooks en cas de régression
    try:
        event_data = {
            "target_id": target.id,
            "target_name": target.name,
            "target_url": target.url,
            "delta_status": delta_status,
            "violations_count": new_violations,
            "risk_score": evaluation.risk_score,
            "audit_id": evaluation.audit_trail.audit_id,
            "detected_claims": claims_list,
        }
        dispatch_webhook_event(db, target.organization_id, "watcher.check_completed", event_data)
        if regression:
            dispatch_webhook_event(db, target.organization_id, "watcher.regression_detected", event_data)
    except Exception as exc:
        logger.warning("Erreur webhook watcher: %s", exc)

    return log_entry


async def run_due_watcher_checks(
    db: Session,
    evaluator: Any,
    execute_evaluation_func: Any,
    max_targets: int = 10,
) -> list[MonitoringLog]:
    """Exécute les contrôles pour toutes les cibles dont l'échéance est dépassée."""
    now = datetime.now(timezone.utc)
    due_targets = list(
        db.scalars(
            select(MonitoredTarget)
            .where(
                MonitoredTarget.is_active == True,
                MonitoredTarget.next_check_due_utc <= now,
            )
            .limit(max_targets)
        ).all()
    )

    logs: list[MonitoringLog] = []
    for tgt in due_targets:
        try:
            log_entry = await execute_watcher_check(
                db=db,
                target=tgt,
                evaluator=evaluator,
                execute_evaluation_func=execute_evaluation_func,
            )
            logs.append(log_entry)
        except Exception as exc:
            logger.error("Échec surveillance cible %s (%s): %s", tgt.id, tgt.url, exc)

    return logs
