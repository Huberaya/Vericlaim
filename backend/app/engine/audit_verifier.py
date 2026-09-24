from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import AuditRecord, sha256_json


@dataclass
class AuditVerificationResult:
    audit_id: str
    is_valid: bool
    status: str  # "CERTIFIED" | "TAMPERED" | "NOT_FOUND"
    message: str
    created_at_utc: datetime | None = None
    organization_id: str | None = None
    source_sha256: str | None = None
    report_sha256: str | None = None
    record_hash: str | None = None
    chain_verified: bool = False
    overall_compliance: str | None = None
    risk_score: int | None = None
    violations_count: int | None = None
    verification_timestamp_utc: datetime = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "is_valid": self.is_valid,
            "status": self.status,
            "message": self.message,
            "created_at_utc": self.created_at_utc.isoformat() if self.created_at_utc else None,
            "organization_id": self.organization_id,
            "source_sha256": self.source_sha256,
            "report_sha256": self.report_sha256,
            "record_hash": self.record_hash,
            "chain_verified": self.chain_verified,
            "overall_compliance": self.overall_compliance,
            "risk_score": self.risk_score,
            "violations_count": self.violations_count,
            "verification_timestamp_utc": self.verification_timestamp_utc.isoformat(),
        }


def verify_audit_record(
    db: Session,
    audit_id: str,
    supplied_report_json: dict[str, Any] | None = None,
) -> AuditVerificationResult:
    """
    Vérifie l'intégrité cryptographique et l'authenticité d'un audit dans le registre immuable.
    Contrôle :
    1. Présence dans le registre
    2. Concordance de l'empreinte SHA-256 du rapport
    3. Intégrité de la chaîne de hachage tamper-evident
    """
    record = db.scalar(select(AuditRecord).where(AuditRecord.audit_id == audit_id))
    if not record:
        return AuditVerificationResult(
            audit_id=audit_id,
            is_valid=False,
            status="NOT_FOUND",
            message="Identifiant d'audit introuvable dans le registre officiel VeriClaim AI.",
        )

    # 1. Vérification contre une altération du document fourni
    if supplied_report_json is not None:
        # On calcule le hash du rapport fourni
        # Si un audit_trail y est inclus, il peut avoir été ajouté après base_response
        calculated_hash = sha256_json(supplied_report_json)
        if calculated_hash != record.report_sha256:
            # Vérifier si sans audit_trail le hash correspond
            cleaned = {k: v for k, v in supplied_report_json.items() if k != "audit_trail"}
            if sha256_json(cleaned) != record.report_sha256:
                return AuditVerificationResult(
                    audit_id=audit_id,
                    is_valid=False,
                    status="TAMPERED",
                    message="Altération détectée : l'empreinte SHA-256 du rapport ne correspond pas au registre d'origine.",
                    created_at_utc=record.created_at_utc,
                    organization_id=record.organization_id,
                    source_sha256=record.source_sha256,
                    report_sha256=record.report_sha256,
                    record_hash=record.record_hash,
                )

    # 2. Vérification de l'intégrité cryptographique du chaînage
    created_dt = record.created_at_utc
    if created_dt and created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)

    material = {
        "organization_id": record.organization_id,
        "audit_id": record.audit_id,
        "created_at_utc": created_dt.isoformat(),
        "source_sha256": record.source_sha256,
        "evidence_manifest_sha256": record.evidence_manifest_sha256,
        "report_sha256": record.report_sha256,
        "previous_record_hash": record.previous_record_hash,
        "summary": record.summary_json,
        "supplier_name": record.supplier_name,
        "product_identifier": record.product_identifier,
    }
    recomputed_hash = sha256_json(material)
    chain_ok = (recomputed_hash == record.record_hash)

    if not chain_ok:
        legacy_material = {k: v for k, v in material.items() if k != "organization_id"}
        if sha256_json(legacy_material) == record.record_hash:
            chain_ok = True

    summary = record.summary_json or {}

    return AuditVerificationResult(
        audit_id=audit_id,
        is_valid=chain_ok,
        status="CERTIFIED" if chain_ok else "TAMPERED",
        message="Attestation d'audit authentique et certifiée conforme dans le registre immuable."
        if chain_ok
        else "Avertissement : rupture de la chaîne d'intégrité détectée dans le registre.",
        created_at_utc=record.created_at_utc,
        organization_id=record.organization_id,
        source_sha256=record.source_sha256,
        report_sha256=record.report_sha256,
        record_hash=record.record_hash,
        chain_verified=chain_ok,
        overall_compliance=summary.get("overall_compliance"),
        risk_score=summary.get("risk_score"),
        violations_count=summary.get("violations_count"),
    )
