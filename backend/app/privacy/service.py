"""C20 — retention, erasure and export of a tenant's data.

Three obligations, one module, and one rule that governs all of it: **this code never
invents a duration.** The retention windows come from the organization's own
declaration (`organization_retention_policies`, written by `declare_retention_policy`);
when nothing is declared, nothing is purged and the report says so.

What the module does:

* `build_purge_plan` — decides, item by item, what is past its retention window and what
  is not, with the **reason** for every kept item. A purge that silently keeps data is
  indistinguishable from a purge that forgets to run.
* `erase` — deletes the content of a document or an analysis on demand: storage objects
  first, then rows, in one transaction. **Fail closed**: when an object cannot be
  deleted, the rows are kept, because a row-less object with no reference is worse than
  a row that still points at it.
* `export_organization` — a complete export: everything this code holds about the
  organization, including the parts the old `export-dossier` summary omitted (document
  versions, text segments, claims, verdicts, evidence links, reports, audit trail).

Two invariants this module refuses to break:

1. **No audit row is ever deleted.** Deleting one would make `verify_audit_chain` fail
   from that point on — the very proof the customer pays for. Each deletion therefore
   appends a *tombstone* event carrying a deterministic digest of what was destroyed:
   the content is gone, the proof that it existed and was destroyed remains.
2. **The longest applicable retention wins.** A document attached as evidence, or under
   an active legal hold, is not deleted because a shorter window on another table has
   expired. Only an explicit erasure request can go past a retention window, and never
   past a legal hold.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from fastapi.encoders import jsonable_encoder

from app.core.database import sha256_json
from app.identity.service import append_audit_event
from app.models.domain import (
    Analysis,
    AnalysisDocument,
    AnalysisVerdict,
    Claim,
    Document,
    DocumentSegment,
    DocumentVersion,
    Evidence,
    EvidenceLink,
    LegalHold,
    OrganizationRetentionPolicy,
    Report,
    Validation,
)

logger = logging.getLogger("vericlaim.privacy")

#: Everything this module can destroy, in the order it must be destroyed (children
#: before parents, so no foreign key is ever left dangling mid-transaction).
PURGEABLE_ENTITIES = ("reports", "document_segments", "document_versions", "documents")


@dataclass
class PurgeDecision:
    """One item, and the reason it is or is not destroyed."""

    entity: str
    entity_id: str
    action: str  # purge | keep
    reason: str
    expires_at: str | None = None
    #: What a real run *would* delete. Kept apart from what was actually deleted:
    #: a dry run that reports "12 objects deleted" reports something that did not
    #: happen, and a purge is exactly the place where that cannot be tolerated.
    planned_storage_objects: int = 0
    planned_rows: int = 0
    storage_objects: int = 0
    rows: int = 0


@dataclass
class PurgePlan:
    organization_id: UUID
    generated_at: datetime
    dry_run: bool
    policy_declared: bool
    decisions: list[PurgeDecision] = field(default_factory=list)
    #: Durations this code cannot enforce, with the reason. Never empty when the
    #: organization declared something this module does not apply.
    not_enforced: list[dict[str, str]] = field(default_factory=list)
    #: C13 — ce que l'offre de l'organisation couvre, et ce qu'elle plafonne. La
    #: durée appliquée est la plus courte des deux (déclarée / couverte par l'offre).
    plan_retention: dict[str, object] = field(default_factory=dict)

    @property
    def purged(self) -> list[PurgeDecision]:
        return [decision for decision in self.decisions if decision.action == "purge"]

    @property
    def kept(self) -> list[PurgeDecision]:
        return [decision for decision in self.decisions if decision.action == "keep"]

    def as_dict(self) -> dict[str, object]:
        return {
            "organization_id": str(self.organization_id),
            "generated_at": self.generated_at.isoformat(),
            "dry_run": self.dry_run,
            "policy_declared": self.policy_declared,
            "purged_count": len(self.purged),
            "kept_count": len(self.kept),
            "storage_objects_deleted": sum(d.storage_objects for d in self.purged),
            "rows_deleted": sum(d.rows for d in self.purged),
            "storage_objects_planned": sum(d.planned_storage_objects for d in self.purged),
            "decisions": [
                {
                    "entity": decision.entity,
                    "entity_id": decision.entity_id,
                    "action": decision.action,
                    "reason": decision.reason,
                    "expires_at": decision.expires_at,
                }
                for decision in self.decisions
            ],
            "not_enforced": self.not_enforced,
            "plan_retention": self.plan_retention,
        }


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def retention_policy(db: Session, *, organization_id: UUID) -> OrganizationRetentionPolicy | None:
    return db.scalar(
        select(OrganizationRetentionPolicy).where(
            OrganizationRetentionPolicy.organization_id == organization_id
        )
    )


def active_legal_holds(db: Session, *, organization_id: UUID, now: datetime) -> list[LegalHold]:
    """Holds that forbid deletion *right now*.

    An expired hold stops forbidding: a hold is a legal instrument with an end date,
    and treating an expired one as active would freeze the data forever.
    """
    holds = db.scalars(
        select(LegalHold).where(
            LegalHold.organization_id == organization_id,
            LegalHold.is_active.is_(True),
        )
    ).all()
    return [
        hold
        for hold in holds
        if hold.expires_at is None or (_as_utc(hold.expires_at) or now) > now
    ]


def _windows(policy: OrganizationRetentionPolicy | None) -> dict[str, int | None]:
    if policy is None:
        return {
            "documents": None,
            "evidence_archive": None,
            "audit_trail": None,
        }
    return {
        "documents": policy.documents_retention_years,
        "evidence_archive": policy.evidence_archive_retention_years,
        "audit_trail": policy.audit_trail_retention_years,
    }


def _expiry(created_at: datetime | None, years: int | None, now: datetime) -> datetime | None:
    """`created_at` + the declared window, or None when nothing is declared."""
    if years is None:
        return None
    start = _as_utc(created_at) or now
    return start + timedelta(days=365 * years)


def _cap_windows_by_plan(
    db: Session,
    *,
    organization_id: UUID,
    windows: dict[str, int | None],
    now: datetime,
) -> tuple[dict[str, int | None], dict[str, object]]:
    """Plafonne les durées appliquées par ce que l'offre couvre réellement.

    La durée **déclarée** n'est pas modifiée : elle reste ce que le client a
    déclaré. C'est la durée **appliquée** qui devient ``min(déclarée, couverte par
    l'offre)``, et le résultat est publié avec la raison. Une offre d'entrée de
    gamme ne conserve pas des documents pendant dix ans parce qu'un client l'a
    déclaré : le produit ne promet pas une durée qu'il ne facture pas.
    """

    from app.billing.enforcement import effective_retention_months

    info: dict[str, object] = {
        "plan_code": None,
        "ceiling_months": None,
        "limited_by_plan": False,
        "applied_months": {},
        "note": "",
    }
    capped = dict(windows)
    for item in ("documents", "evidence_archive"):
        declared_years = windows.get(item)
        window = effective_retention_months(
            db,
            organization_id=organization_id,
            declared_months=declared_years * 12 if declared_years is not None else None,
            now=now,
        )
        info["plan_code"] = window.plan_code
        info["ceiling_months"] = window.ceiling_months
        info["limited_by_plan"] = bool(info["limited_by_plan"]) or window.limited_by_plan
        info["note"] = window.note
        if window.effective_months is not None:
            applied = max(window.effective_months // 12, 1)
            capped[item] = applied
            cast_applied = info["applied_months"]
            assert isinstance(cast_applied, dict)
            cast_applied[item] = applied
    return capped, info


def build_purge_plan(
    db: Session,
    *,
    organization_id: UUID,
    now: datetime | None = None,
) -> PurgePlan:
    """Decide what is past its window — and record why everything else is kept."""
    now = now or datetime.now(timezone.utc)
    policy = retention_policy(db, organization_id=organization_id)
    windows, plan_retention = _cap_windows_by_plan(
        db, organization_id=organization_id, windows=_windows(policy), now=now
    )
    holds = active_legal_holds(db, organization_id=organization_id, now=now)
    plan = PurgePlan(
        organization_id=organization_id,
        generated_at=now,
        dry_run=True,
        policy_declared=policy is not None,
        plan_retention=plan_retention,
    )

    if policy is None:
        plan.not_enforced.append(
            {
                "item": "purge automatique",
                "reason": (
                    "Aucune politique de rétention n'est déclarée : ce code ne connaît aucune durée "
                    "de conservation et ne supprime donc rien de lui-même. Déclarer la politique "
                    "via PUT /api/v1/pilot/retention-policy."
                ),
            }
        )
        plan.decisions.append(
            PurgeDecision(
                entity="organization",
                entity_id=str(organization_id),
                action="keep",
                reason="aucune politique déclarée — aucune durée ne peut être appliquée",
            )
        )
        return plan

    if windows["audit_trail"] is not None:
        plan.not_enforced.append(
            {
                "item": "audit_trail_retention_years",
                "reason": (
                    "La durée déclarée pour la piste d'audit n'est PAS appliquée : supprimer des "
                    "événements d'audit romprait la chaîne d'empreintes et rendrait invérifiable "
                    "tout ce qui la suit. Une purge de piste d'audit suppose une décision juridique "
                    "(pseudonymisation, ou conservation justifiée) qui n'appartient pas à ce code."
                ),
            }
        )

    if holds:
        # A legal hold is not one item among others: it suspends the whole purge for
        # this organization. Reporting the documents as "to be purged" next to a hold
        # would produce a plan whose own execution contradicts it.
        for hold in holds:
            plan.decisions.append(
                PurgeDecision(
                    entity="legal_hold",
                    entity_id=str(hold.id),
                    action="keep",
                    reason=(
                        f"gel légal « {hold.case_reference} » actif "
                        f"({'sans échéance' if hold.expires_at is None else f'jusqu’au {hold.expires_at:%d/%m/%Y}'})"
                        " — la purge est suspendue pour cette organisation"
                    ),
                )
            )

    documents_window = windows["documents"]
    evidence_window = windows["evidence_archive"]

    documents = db.scalars(
        select(Document).where(Document.organization_id == organization_id)
    ).all()

    for document in documents:
        if holds:
            plan.decisions.append(
                PurgeDecision(
                    entity="document",
                    entity_id=str(document.id),
                    action="keep",
                    reason="gel légal actif — aucune suppression n'est possible",
                )
            )
            continue
        versions = db.scalars(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.document_id == document.id,
            )
        ).all()
        version_ids = [version.id for version in versions]

        # The longest applicable window wins. Evidence attached to a version pulls the
        # document into the evidence-archive window.
        used_as_evidence = False
        used_as_evidence_count = 0
        attached_analysis_count = 0
        if version_ids:
            used_as_evidence_count = int(
                db.scalar(
                    select(func.count(Evidence.id)).where(
                        Evidence.organization_id == organization_id,
                        Evidence.document_version_id.in_(version_ids),
                    )
                )
                or 0
            )
            used_as_evidence = used_as_evidence_count > 0

            # A document that is part of an analysis's input is NOT purged
            # automatically: `analysis_documents.document_version_id` is RESTRICT, and
            # deleting the link would silently change what an issued report was built
            # from. An explicit erasure request may destroy it; an automatic window may
            # not. This distinction is deliberate, and published below.
            attached_analysis_count = int(
                db.scalar(
                    select(func.count(AnalysisDocument.id)).where(
                        AnalysisDocument.organization_id == organization_id,
                        AnalysisDocument.document_version_id.in_(version_ids),
                    )
                )
                or 0
            )

        document_expiry = _expiry(document.created_at, documents_window, now)
        evidence_expiry = _expiry(document.created_at, evidence_window, now) if used_as_evidence else None
        expiries = [value for value in (document_expiry, evidence_expiry) if value is not None]

        if not expiries:
            plan.decisions.append(
                PurgeDecision(
                    entity="document",
                    entity_id=str(document.id),
                    action="keep",
                    reason="aucune durée déclarée pour les documents",
                )
            )
            continue

        if attached_analysis_count:
            plan.decisions.append(
                PurgeDecision(
                    entity="document",
                    entity_id=str(document.id),
                    action="keep",
                    reason=(
                        f"rattaché à {attached_analysis_count} analyse(s) : une purge automatique "
                        "changerait silencieusement ce sur quoi un rapport a été émis — seul un "
                        "effacement explicite (POST /api/v1/privacy/erasures) peut le détruire"
                    ),
                )
            )
            plan.not_enforced.append(
                {
                    "item": "purge des documents rattachés à une analyse",
                    "reason": (
                        "Non appliquée : détruire l'entrée d'une analyse invaliderait la provenance "
                        "d'un rapport déjà émis. Décision à trancher avec un juriste (C19/C12)."
                    ),
                }
            )
            continue

        expiry = max(expiries)
        if expiry > now:
            reason = "durée de conservation non écoulée"
            if evidence_expiry is not None and evidence_expiry == expiry:
                reason = (
                    "conservé comme preuve : la durée d'archivage des preuves "
                    "(la plus longue des durées applicables) court encore"
                )
            plan.decisions.append(
                PurgeDecision(
                    entity="document",
                    entity_id=str(document.id),
                    action="keep",
                    reason=reason,
                    expires_at=expiry.isoformat(),
                )
            )
            continue

        storage_keys = [
            (version.storage_key, version.extracted_text_storage_key) for version in versions
        ]
        objects = sum(1 for key, extracted in storage_keys for value in (key, extracted) if value)
        plan.decisions.append(
            PurgeDecision(
                entity="document",
                entity_id=str(document.id),
                action="purge",
                reason=(
                    "durée de conservation écoulée"
                    + (" ; conservé comme preuve jusqu'à échéance" if used_as_evidence else "")
                ),
                expires_at=expiry.isoformat(),
                planned_storage_objects=objects,
                planned_rows=1 + len(versions),
            )
        )

    return plan


@dataclass
class ErasureOutcome:
    organization_id: UUID
    scope: str
    target_id: str
    erased: bool
    reason: str
    manifest_sha256: str | None = None
    storage_objects_deleted: int = 0
    rows_deleted: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "organization_id": str(self.organization_id),
            "scope": self.scope,
            "target_id": self.target_id,
            "erased": self.erased,
            "reason": self.reason,
            "manifest_sha256": self.manifest_sha256,
            "storage_objects_deleted": self.storage_objects_deleted,
            "rows_deleted": self.rows_deleted,
        }


class PrivacyError(RuntimeError):
    """A retention or erasure request that cannot be satisfied honestly."""

    def __init__(self, code: str, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _manifest_digest(entries: list[tuple[str, str]]) -> str:
    """Deterministic digest of what is about to be destroyed.

    Sorted, so two runs on the same content produce the same digest; the digest is what
    the audit tombstone carries. It proves *that* something was destroyed and lets a
    later export be compared with it, without keeping the content.
    """
    material = "\n".join(f"{kind}:{identifier}" for kind, identifier in sorted(entries))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass
class StorageDeletion:
    """What actually happened in the object store, as opposed to what was requested."""

    deleted: int = 0
    absent: int = 0


def _delete_storage_objects(storage: object, *, keys: list[tuple[str, str]]) -> StorageDeletion:
    """Delete objects that exist, and refuse to continue on any other failure.

    Two honesties are encoded here:

    * a key that holds nothing is *counted as absent*, not as deleted — a report that
      says "12 objects deleted" when 10 of them never existed is a report nobody can
      audit;
    * an unreachable store raises, and the caller then keeps the rows. Deleting the
      rows after a failed object deletion would leave the content in the store with no
      record pointing at it: invisible, unreachable, and still there.
    """
    from app.documents.storage import ObjectNotFoundError

    outcome = StorageDeletion()
    for bucket, key in keys:
        try:
            storage.head(bucket=bucket, key=key)  # type: ignore[attr-defined]
        except ObjectNotFoundError:
            outcome.absent += 1
            continue
        storage.delete(bucket=bucket, key=key)  # type: ignore[attr-defined]
        outcome.deleted += 1
    return outcome


def _version_buckets(settings: Settings) -> tuple[str, str]:
    """The two buckets that can hold a document's content.

    Quarantine holds bytes that were never promoted (rejected, expired, or still
    scanning); the clean bucket holds the promoted object *and* the extracted text
    artefact, which the extraction worker writes there too.
    """
    return (
        settings.document_quarantine_bucket,
        settings.document_clean_bucket,
    )


def erase_document(
    db: Session,
    *,
    storage: object,
    settings: Settings,
    organization_id: UUID,
    document_id: UUID,
    actor_user_id: UUID | None,
    reason: str,
    request_id: str | None = None,
    now: datetime | None = None,
) -> ErasureOutcome:
    """Destroy one document's content and rows, and leave a trace that survives it.

    On-demand erasure (a data-subject request, a contract end) may go past a retention
    window, because the customer is the controller and decides: keeping data because
    "the window has not expired" would be an odd answer to "delete my data". It may
    **not** go past a legal hold — that is the whole point of a hold.
    """
    now = now or datetime.now(timezone.utc)
    holds = active_legal_holds(db, organization_id=organization_id, now=now)
    if holds:
        references = ", ".join(sorted(hold.case_reference for hold in holds))
        return ErasureOutcome(
            organization_id=organization_id,
            scope="document",
            target_id=str(document_id),
            erased=False,
            reason=f"refusé : gel légal actif ({references}) — l'effacement est suspendu",
        )

    document = db.scalar(
        select(Document).where(
            Document.organization_id == organization_id, Document.id == document_id
        )
    )
    if document is None:
        return ErasureOutcome(
            organization_id=organization_id,
            scope="document",
            target_id=str(document_id),
            erased=False,
            reason="document introuvable dans cette organisation (rien n'a été supprimé)",
        )

    report = _destroy_document(
        db,
        storage=storage,
        settings=settings,
        organization_id=organization_id,
        document_id=document_id,
        actor_user_id=actor_user_id,
        request_id=request_id,
        action="document.erased",
        reason=reason,
        allow_analysis_attachment=True,
    )
    return ErasureOutcome(
        organization_id=organization_id,
        scope="document",
        target_id=str(document_id),
        erased=True,
        reason=reason,
        manifest_sha256=report["manifest_sha256"],
        storage_objects_deleted=report["storage_objects_deleted"],
        rows_deleted=report["rows_deleted"],
    )


def _destroy_document(
    db: Session,
    *,
    storage: object,
    settings: Settings,
    organization_id: UUID,
    document_id: UUID,
    actor_user_id: UUID | None,
    request_id: str | None,
    action: str,
    reason: str,
    extra_payload: dict[str, object] | None = None,
    allow_analysis_attachment: bool = False,
) -> dict[str, object]:
    """Destroy one document: objects first, rows after, tombstone last.

    **Fail closed**: if the object store refuses a deletion, the exception propagates
    *before* any row is touched. Deleting the rows first would leave the content in the
    store with nothing pointing at it — invisible, unreachable, and still there.

    `allow_analysis_attachment` distinguishes the two callers:

    * an automatic purge (`False`) never destroys a document that is the input of an
      analysis — the plan already keeps those, and this is the belt to that braces;
    * an explicit erasure (`True`) does, because the customer asked for it, and the
      tombstone records how many analysis attachments went with it.
    """
    document = db.get(Document, document_id)
    if document is None:
        raise PrivacyError("document_not_found", "document introuvable", status_code=404)

    versions = db.scalars(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.document_id == document_id,
        )
    ).all()
    version_ids = [version.id for version in versions]
    segments = (
        db.scalars(
            select(DocumentSegment).where(
                DocumentSegment.organization_id == organization_id,
                DocumentSegment.document_version_id.in_(version_ids),
            )
        ).all()
        if version_ids
        else []
    )
    attachments = (
        db.scalars(
            select(AnalysisDocument).where(
                AnalysisDocument.organization_id == organization_id,
                AnalysisDocument.document_version_id.in_(version_ids),
            )
        ).all()
        if version_ids
        else []
    )
    if attachments and not allow_analysis_attachment:
        raise PrivacyError(
            "document_attached_to_analysis",
            "ce document est l'entrée d'une analyse : une purge automatique ne peut pas le détruire",
        )

    quarantine, clean = _version_buckets(settings)
    objects: list[tuple[str, str]] = []
    for version in versions:
        objects.append((clean, version.storage_key))
        # A version that never completed extraction still holds its quarantined bytes:
        # leaving them behind would erase the record and keep the document.
        objects.append((quarantine, version.storage_key))
        if version.extracted_text_storage_key:
            objects.append((clean, version.extracted_text_storage_key))

    manifest = _manifest_digest(
        [("document", str(document.id))]
        + [("document_version", str(version.id)) for version in versions]
        + [("document_segment", str(segment.id)) for segment in segments]
        + [("analysis_document", str(attachment.id)) for attachment in attachments]
    )

    deletion = _delete_storage_objects(storage, keys=objects)

    rows: dict[str, int] = {}
    if version_ids:
        # Verdicts and claims point at segments by foreign key: deleting a segment
        # without them would leave a verdict whose citation is gone.
        segment_ids = [segment.id for segment in segments]
        if segment_ids:
            rows["analysis_verdicts"] = _delete(
                db,
                AnalysisVerdict,
                AnalysisVerdict.organization_id == organization_id,
                AnalysisVerdict.document_segment_id.in_(segment_ids),
            )
            rows["claims"] = _delete(
                db,
                Claim,
                Claim.organization_id == organization_id,
                Claim.document_segment_id.in_(segment_ids),
            )
        rows["document_segments"] = _delete(
            db,
            DocumentSegment,
            DocumentSegment.organization_id == organization_id,
            DocumentSegment.document_version_id.in_(version_ids),
        )
        rows["analysis_documents"] = _delete(
            db,
            AnalysisDocument,
            AnalysisDocument.organization_id == organization_id,
            AnalysisDocument.document_version_id.in_(version_ids),
        )
        rows["evidence"] = _delete(
            db,
            Evidence,
            Evidence.organization_id == organization_id,
            Evidence.document_version_id.in_(version_ids),
        )
    rows["document_versions"] = _delete(
        db, DocumentVersion, DocumentVersion.organization_id == organization_id,
        DocumentVersion.document_id == document_id,
    )
    rows["documents"] = _delete(
        db, Document, Document.organization_id == organization_id, Document.id == document_id
    )

    payload = {
        "reason": reason,
        "manifest_sha256": manifest,
        "storage_objects_deleted": deletion.deleted,
        "storage_objects_absent": deletion.absent,
        "rows_deleted": rows,
        "document_key": document.document_key,
        "audit_trail_kept": True,
    }
    if extra_payload:
        payload.update(extra_payload)
    append_audit_event(
        db,
        organization_id=organization_id,
        actor_user_id=actor_user_id,
        entity_type="document",
        entity_id=document_id,
        action=action,
        payload=payload,
        request_id=request_id,
    )
    db.commit()
    logger.info(
        "Document destroyed",
        extra={
            "event": action,
            "organization_id": str(organization_id),
            "document_id": str(document_id),
            "manifest_sha256": manifest,
            "storage_objects_deleted": deletion.deleted,
            "storage_objects_absent": deletion.absent,
        },
    )
    return {
        "manifest_sha256": manifest,
        "storage_objects_deleted": deletion.deleted,
        "storage_objects_absent": deletion.absent,
        "rows_deleted": rows,
    }


def _delete(db: Session, model, *conditions) -> int:
    """Delete rows matching the conditions, and return how many were removed."""
    rows = db.query(model).filter(*conditions).all()
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


def run_purge(
    db: Session,
    *,
    storage: object,
    settings: Settings,
    organization_id: UUID,
    actor_user_id: UUID | None,
    dry_run: bool = True,
    request_id: str | None = None,
    now: datetime | None = None,
) -> PurgePlan:
    """Apply the retention plan, or (by default) only describe it.

    A dry run is the default everywhere: a destructive job whose default is to destroy
    is a job that eventually destroys the wrong thing during an incident.
    """
    plan = build_purge_plan(db, organization_id=organization_id, now=now)
    plan.dry_run = dry_run
    if dry_run or not plan.policy_declared:
        return plan

    for decision in plan.purged:
        document_id = UUID(decision.entity_id)
        # The same routine as an on-demand erasure: two destruction paths that can
        # diverge would produce, sooner or later, a document deleted in the database
        # and alive in the object store (or the reverse).
        report = _destroy_document(
            db,
            storage=storage,
            settings=settings,
            organization_id=organization_id,
            document_id=document_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
            action="document.purged",
            reason="durée de conservation écoulée (purge planifiée)",
            extra_payload={"policy": "retention.purge", "expires_at": decision.expires_at},
        )
        decision.storage_objects = report["storage_objects_deleted"]
        decision.rows = sum(report["rows_deleted"].values())
    db.commit()
    logger.info(
        "Retention purge applied",
        extra={
            "event": "retention_purge",
            "organization_id": str(organization_id),
            "documents_purged": len(plan.purged),
        },
    )
    return plan


def export_organization(db: Session, *, organization_id: UUID) -> dict[str, object]:
    """Everything this code holds about one organization, stated explicitly.

    The former `GET /pilot/export-dossier` answered with counts (`claims_count`,
    `evidence_count`) and document titles: a summary presented as a dossier. This one
    carries the rows themselves, and lists what it does *not* contain so the reader is
    not left to assume.

    The scope claim is only worth as much as it is true. An earlier revision exported
    the compliance dossier but not the members, not the legal holds and not the mail
    outbox — while announcing "toutes les données détenues par cette instance pour
    cette organisation". A DPO export that omits the people who may read the data, the
    holds that freeze it, and the messages sent about it is not an export; it is a
    subset with a completeness claim attached to it.
    """
    from app.models.domain import (
        AnalysisVersion,
        AuditEvent,
        Certificate,
        EmailMessage,
        EvidenceRequest,
        LegalHold,
        Membership,
        Organization,
        OrganizationRetentionPolicy,
        Role,
        Supplier,
        User,
        Product,
    )

    def rows(model, *conditions):
        return db.scalars(select(model).where(*conditions)).all()

    suppliers = rows(Supplier, Supplier.organization_id == organization_id)
    products = rows(Product, Product.organization_id == organization_id)
    documents = rows(Document, Document.organization_id == organization_id)
    versions = rows(DocumentVersion, DocumentVersion.organization_id == organization_id)
    segments = rows(DocumentSegment, DocumentSegment.organization_id == organization_id)
    analyses = rows(Analysis, Analysis.organization_id == organization_id)
    analysis_versions = rows(AnalysisVersion, AnalysisVersion.organization_id == organization_id)
    claims = rows(Claim, Claim.organization_id == organization_id)
    verdicts = rows(AnalysisVerdict, AnalysisVerdict.organization_id == organization_id)
    evidence = rows(Evidence, Evidence.organization_id == organization_id)
    links = rows(EvidenceLink, EvidenceLink.organization_id == organization_id)
    validations = rows(Validation, Validation.organization_id == organization_id)
    certificates = rows(Certificate, Certificate.organization_id == organization_id)
    reports = rows(Report, Report.organization_id == organization_id)
    requests = rows(EvidenceRequest, EvidenceRequest.organization_id == organization_id)
    audit_events = rows(AuditEvent, AuditEvent.organization_id == organization_id)

    # Members and holds: what a data protection officer asks for first, and what the
    # previous revision simply did not carry.
    member_rows = db.execute(
        select(Membership, User, Role)
        .join(User, User.id == Membership.user_id)
        .join(Role, Role.id == Membership.role_id)
        .where(Membership.organization_id == organization_id)
        .order_by(Membership.created_at.asc())
    ).all()
    legal_holds = rows(LegalHold, LegalHold.organization_id == organization_id)
    email_messages = rows(EmailMessage, EmailMessage.organization_id == organization_id)
    organization = db.get(Organization, organization_id)
    retention_policy = db.scalar(
        select(OrganizationRetentionPolicy).where(
            OrganizationRetentionPolicy.organization_id == organization_id
        )
    )

    def column_values(row, names: tuple[str, ...]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for name in names:
            value = getattr(row, name, None)
            payload[name] = value.value if hasattr(value, "value") else (str(value) if isinstance(value, UUID) else value)
        return payload

    document_versions = [
        {
            **column_values(
                version,
                (
                    "id",
                    "document_id",
                    "version_number",
                    "source_filename",
                    "content_type",
                    "storage_key",
                    "sha256",
                    "size_bytes",
                    "page_count",
                    "extraction_status",
                ),
            ),
            "extracted_text_storage_key": version.extracted_text_storage_key,
            "segment_count": sum(1 for segment in segments if segment.document_version_id == version.id),
        }
        for version in versions
    ]

    export: dict[str, object] = {
        "export_metadata": {
            "organization_id": str(organization_id),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "format_version": "vericlaim-export-1",
            #: C12 — l'export doit porter **ce qu'il est**, pas seulement ce qu'il contient.
            #: Sans empreinte, une demande d'accès ne peut pas être close avec la preuve de
            #: ce qui a été remis : l'organisation n'aurait qu'une capture d'écran à produire.
            "manifest_sha256": "",
            "scope": "toutes les données détenues par cette instance pour cette organisation",
            "laws": [
                "Le contenu binaire des documents n'est pas inclus : il est référencé par "
                "`storage_key` et téléchargeable via l'API de téléchargement, qui vérifie les droits.",
                "Les secrets (mots de passe hachés, jetons d'identité, jetons de session, clés "
                "d'API, secrets MFA) sont exclus par construction : un export ne doit pas devenir "
                "un jeu de clés.",
                "Le corps des e-mails est exclu : chaque message de la file contient un lien "
                "à jeton à usage unique (vérification, réinitialisation, invitation), donc une "
                "capacité d'authentification. L'enveloppe (destinataire, objet, objet du message, "
                "état, dates) est incluse.",
                "Aucune ligne appartenant à une autre organisation n'est lisible depuis ici.",
                "Cet export ne crée aucun droit : il ne remplace ni la procédure d'effacement "
                "(`POST /privacy/erasures`) ni le gel légal.",
            ],
        },
        "organization": (
            column_values(organization, ("id", "name", "slug", "status", "data_region"))
            if organization is not None
            else None
        ),
        "retention_policy": (
            {
                **column_values(
                    retention_policy,
                    (
                        "documents_retention_years",
                        "audit_trail_retention_years",
                        "evidence_archive_retention_years",
                        "gdpr_contact_email",
                        "encryption_standard",
                        "storage_region",
                        "last_policy_review",
                        "configured_at",
                    ),
                ),
                "configured_by_user_id": (
                    str(retention_policy.configured_by_user_id)
                    if retention_policy.configured_by_user_id
                    else None
                ),
                "export_formats_supported": retention_policy.export_formats_supported,
            }
            if retention_policy is not None
            else None
        ),
        "members": [
            {
                "membership_id": str(membership.id),
                "user_id": str(user.id),
                "email": user.email,
                "display_name": user.display_name,
                "user_status": user.status.value if hasattr(user.status, "value") else str(user.status),
                "role_code": role.code,
                "membership_status": (
                    membership.status.value if hasattr(membership.status, "value") else str(membership.status)
                ),
                "invited_by_user_id": (
                    str(membership.invited_by_user_id) if membership.invited_by_user_id else None
                ),
                "activated_at": membership.activated_at,
                "last_authenticated_at": user.last_authenticated_at,
                "created_at": membership.created_at,
            }
            for membership, user, role in member_rows
        ],
        "legal_holds": [
            {
                **column_values(
                    hold,
                    ("id", "case_reference", "reason", "is_active", "expires_at", "created_at"),
                ),
                "created_by_user_id": str(hold.created_by_user_id) if hold.created_by_user_id else None,
            }
            for hold in legal_holds
        ],
        "email_messages": [
            {
                **column_values(
                    message,
                    (
                        "id",
                        "recipient_email",
                        "subject",
                        "purpose",
                        "status",
                        "transport",
                        "attempt_count",
                        "sent_at",
                        "created_at",
                    ),
                ),
                "body_excluded": True,
            }
            for message in email_messages
        ],
        "suppliers": [column_values(row, ("id", "legal_name", "country_code", "archived_at")) for row in suppliers],
        "products": [column_values(row, ("id", "supplier_id", "reference", "name")) for row in products],
        "documents": [
            column_values(row, ("id", "document_key", "title", "document_type", "status", "created_at"))
            for row in documents
        ],
        "document_versions": document_versions,
        "document_segments": [
            column_values(
                row,
                (
                    "id",
                    "document_version_id",
                    "sequence_number",
                    "page_number",
                    "segment_type",
                    "text",
                    "source_sha256",
                ),
            )
            for row in segments
        ],
        "analyses": [column_values(row, ("id", "analysis_key", "status", "created_at")) for row in analyses],
        "analysis_versions": [
            column_values(
                row,
                (
                    "id",
                    "analysis_id",
                    "version_number",
                    "status",
                    "engine_version",
                    "rulebook_version",
                    "input_manifest_sha256",
                    "result_sha256",
                ),
            )
            for row in analysis_versions
        ],
        "claims": [
            column_values(
                row,
                (
                    "id",
                    "analysis_version_id",
                    "document_segment_id",
                    "claim_type",
                    "category",
                    "claim_text",
                    "normalized_text",
                    "confidence_score",
                    "status",
                ),
            )
            for row in claims
        ],
        "analysis_verdicts": [
            column_values(
                row,
                (
                    "id",
                    "analysis_version_id",
                    "claim_id",
                    "document_segment_id",
                    "sequence_number",
                    "claim_type",
                    "claim_text",
                    "rule_id",
                    "rule_title",
                    "law_reference",
                    "legal_force",
                    "severity",
                    "verdict",
                    "is_legal_violation",
                    "legal_caveat",
                    "engine_version",
                    "rulebook_version",
                ),
            )
            for row in verdicts
        ],
        "evidence": [
            column_values(
                row,
                (
                    "id",
                    "document_version_id",
                    "certificate_id",
                    "supplier_id",
                    "product_id",
                    "evidence_type",
                    "status",
                    "reference",
                    "issuer",
                    "issued_on",
                    "expires_on",
                    "product_scope",
                    "verified_at",
                ),
            )
            for row in evidence
        ],
        "evidence_links": [
            column_values(
                row,
                (
                    "id",
                    "claim_id",
                    "evidence_id",
                    "relation",
                    "coverage_status",
                    "validity_as_of",
                    "confidence_score",
                    "rationale",
                ),
            )
            for row in links
        ],
        "validations": [
            column_values(row, ("id", "analysis_version_id", "claim_id", "decision", "decided_at"))
            for row in validations
        ],
        "certificates": [
            column_values(
                row,
                (
                    "id",
                    "document_version_id",
                    "scheme",
                    "license_number",
                    "issuer",
                    "valid_from",
                    "valid_until",
                    "status",
                    "registry_name",
                ),
            )
            for row in certificates
        ],
        "reports": [
            {
                **column_values(row, ("id", "analysis_version_id", "version_number", "report_format", "status", "sha256", "generated_at")),
                "storage_key": row.storage_key,
                "verification_reference": row.verification_reference,
            }
            for row in reports
        ],
        "evidence_requests": [
            column_values(
                row,
                ("id", "supplier_id", "product_id", "claim_id", "status", "created_at"),
            )
            for row in requests
        ],
        "audit_events": [
            column_values(
                row,
                (
                    "id",
                    "occurred_at",
                    "action",
                    "entity_type",
                    "entity_id",
                    "actor_user_id",
                    "payload_sha256",
                    "previous_event_hash",
                    "event_hash",
                ),
            )
            for row in audit_events
        ],
        "counts": {
            "suppliers": len(suppliers),
            "products": len(products),
            "documents": len(documents),
            "document_versions": len(versions),
            "document_segments": len(segments),
            "analyses": len(analyses),
            "claims": len(claims),
            "analysis_verdicts": len(verdicts),
            "evidence": len(evidence),
            "evidence_links": len(links),
            "validations": len(validations),
            "certificates": len(certificates),
            "reports": len(reports),
            "audit_events": len(audit_events),
            "members": len(member_rows),
            "legal_holds_active": sum(1 for hold in legal_holds if hold.is_active),
            "legal_holds": len(legal_holds),
            "email_messages": len(email_messages),
        },
    }

    # L'empreinte est calculée **sur le contenu sérialisé**, avec la métadonnée
    # d'empreinte vide : elle identifie exactement ce qui a été produit et peut être
    # recalculée par le destinataire de l'export.
    #
    # Le calcul se fait sur la forme encodée par FastAPI et non sur le dictionnaire
    # Python : `str(datetime)` (« 2026-09-30 19:15:55+00:00 ») et la forme JSON
    # (« 2026-09-30T19:15:55+00:00 ») diffèrent, donc une empreinte calculée sur le
    # dictionnaire **ne serait pas vérifiable** par celui qui reçoit l'export — ce qui
    # vide de sens la référence opposable remise au demandeur. Défaut réel corrigé
    # pendant C12 (mesuré : 27d2084… publiée contre 36e94e3… recalculée).
    encoded = jsonable_encoder(export)
    encoded["export_metadata"]["manifest_sha256"] = ""
    digest = sha256_json(encoded)
    export = encoded
    export["export_metadata"]["manifest_sha256"] = digest
    return export
