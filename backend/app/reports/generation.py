"""C22 — le rendu d'un rapport, exécuté par un worker et non par une requête HTTP.

Le code de rendu existait : il vivait dans le corps des routes ``POST
/reports/pdf`` et ``POST /reports/dossier``. Il est déplacé ici, sans changer ce
qu'il produit — mêmes générateurs, même signature, même référence de vérification
—, parce que trois choses ne pouvaient pas être faites depuis une route :

* **stocker l'artefact** : il était renvoyé dans la réponse et jeté. Un client qui
  perdait le fichier devait demander un nouveau rapport, donc une nouvelle
  signature, donc un nouvel artefact probatoire à distribuer ;
* **reprendre** : un redémarrage au milieu d'un rendu perdait le travail ;
* **échouer proprement** : une erreur de génération devenait un 500, sans ligne
  ``reports.failure_code`` à lire.

L'ordre est significatif et inchangé : la référence et la signature sont créées
**avant** le rendu, parce qu'elles sont imprimées dans le document. Puis les
octets sont hachés, stockés, et seulement alors la ligne ``reports`` passe à
``ready``. Un objet stocké sans ligne persistée serait un fichier que personne ne
sait réclamer ; une ligne ``ready`` sans objet serait un rapport promis et absent.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.documents.storage import ObjectStorage, ObjectStorageError
from app.models.domain import Analysis, AnalysisVersion, Document, DocumentVersion, Report
from app.reports.dossier_exporter import create_regulatory_dossier_zip
from app.reports.pdf_generator import RegulatoryPdfReportGenerator
from app.reports.persisted_report import (
    PersistedReportUnavailable,
    build_persisted_evaluation,
)
from app.reports.queue import (
    REPORT_CONTENT_TYPES,
    REPORT_FILE_EXTENSIONS,
    ClaimedReportJob,
    GenerateReportResult,
    ReportQueueError,
    complete_report_job,
)
from app.reports.signing import (
    SIGNATURE_SCHEMA_VERSION,
    build_signature_material,
    canonical_signature_timestamp,
    file_sha256,
    key_id,
    new_verification_reference,
    sign_material,
)


class ReportGenerationUnavailable(RuntimeError):
    """Échec définitif : le rapport ne peut pas être produit honnêtement."""


class ReportGenerationRetryable(RuntimeError):
    """Échec temporaire : un nouveau essai a une chance d'aboutir."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _PreparedArtifact:
    content: bytes
    reference: str
    signature: str
    signing_key_id: str
    generated_at: datetime


def report_storage_key(*, organization_id: UUID, report_id: UUID, report_format: str) -> str:
    """Clé déterministe : un objet de rapport est adressable par son identifiant."""

    extension = REPORT_FILE_EXTENSIONS[report_format]
    return f"reports/{organization_id}/{report_id}.{extension}"


def _deprecation_reasons(version: AnalysisVersion) -> list[str]:
    """Pourquoi ce rapport ne doit pas être lu comme l'état courant.

    Une analyse reste valable ; son verdict repose sur une empreinte de Rule Book.
    Si le référentiel a bougé, un lecteur qui compare deux rapports verrait deux
    réponses différentes sans explication.
    """

    from app.analyses.service import ANALYSIS_ENGINE_VERSION
    from app.engine.rule_book import RULEBOOK_VERSION

    reasons: list[str] = []
    if version.rulebook_version != RULEBOOK_VERSION:
        reasons.append(
            f"Analysé sous l'empreinte de Rule Book {version.rulebook_version}; "
            f"la version courante est {RULEBOOK_VERSION}."
        )
    if version.engine_version != ANALYSIS_ENGINE_VERSION:
        reasons.append(
            f"Produit par le moteur {version.engine_version}; la version courante est "
            f"{ANALYSIS_ENGINE_VERSION}."
        )
    return reasons


def _product_label(db: Session, *, organization_id: UUID, analysis: Analysis, version: AnalysisVersion) -> str:
    if analysis.product_id:
        return str(analysis.product_id)
    context = (version.result_json or {}).get("evaluation_context") or {}
    return str(context.get("product_identifier") or "produit-non-specifie")


def _surface_label(version: AnalysisVersion) -> str:
    context = (version.result_json or {}).get("evaluation_context") or {}
    return str(context.get("surface") or "packaging")


def generate_report_artifact(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    analysis: Analysis,
    version: AnalysisVersion,
    report_format: str,
    report_id: UUID,
    organization_name: str,
    options: dict[str, object],
) -> _PreparedArtifact:
    """Rend l'artefact, sans rien écrire en base ni dans le stockage."""

    try:
        evaluation = build_persisted_evaluation(
            db,
            organization_id=analysis.organization_id,
            version=version,
            product_identifier=str(options.get("product_identifier") or "") or None,
        )
    except PersistedReportUnavailable as exc:
        # Un verdict disparu n'est pas un incident passager : réessayer ne le fera
        # pas revenir. L'erreur est donc définitive et le dit.
        raise ReportGenerationUnavailable(str(exc)) from exc

    reference = new_verification_reference()
    generated_at = datetime.now(timezone.utc)
    material = build_signature_material(
        analysis_version_id=str(version.id),
        result_sha256=version.result_sha256 or "",
        rulebook_version=version.rulebook_version,
        engine_version=version.engine_version,
        generated_at_utc=canonical_signature_timestamp(generated_at),
        verification_reference=reference,
    )
    signature = sign_material(material, signing_key=settings.report_signing_key)
    signing_key_id = key_id(settings.report_signing_key)
    title = str(options.get("document_title") or "Rapport d'Audit Pré-Réglementaire Allégations")
    product_identifier = str(
        options.get("product_identifier") or _product_label(db, organization_id=analysis.organization_id, analysis=analysis, version=version)
    )
    surface = str(options.get("surface") or _surface_label(version))
    deprecated_reasons = _deprecation_reasons(version)

    if report_format == "pdf":
        content = RegulatoryPdfReportGenerator(
            report=evaluation,
            organization_name=organization_name,
            document_title=title,
            product_identifier=product_identifier,
            surface=surface,
            include_evidence_matrix=bool(options.get("include_evidence_matrix", True)),
            include_remediation_clauses=bool(options.get("include_remediation_clauses", True)),
            signature_reference=reference,
            signature_value=signature,
            signature_key_id=signing_key_id,
            deprecated_reasons=deprecated_reasons,
        ).generate()
    elif report_format == "dossier_zip":
        content = create_regulatory_dossier_zip(
            report=evaluation,
            organization_name=organization_name,
            product_identifier=product_identifier,
            surface=surface,
            signature_reference=reference,
            signature_value=signature,
            signature_key_id=signing_key_id,
            deprecated_reasons=deprecated_reasons,
        )
    else:  # pragma: no cover - la file refuse déjà les formats inconnus
        raise ReportQueueError(f"Format de rapport non pris en charge : {report_format!r}.")

    if not content:
        raise ReportGenerationRetryable(
            "empty_artifact", "Le générateur a produit un document vide : rien n'a été stocké."
        )
    return _PreparedArtifact(
        content=content,
        reference=reference,
        signature=signature,
        signing_key_id=signing_key_id,
        generated_at=generated_at,
    )


def process_claimed_report_job(
    db: Session,
    *,
    settings: Settings,
    storage: ObjectStorage,
    claim: ClaimedReportJob,
) -> GenerateReportResult:
    """Génère, stocke, puis publie le rapport. Le travail est achevé à la fin."""

    started = time.perf_counter()
    if claim.report_id is None:
        raise ReportGenerationUnavailable(
            "Ce travail de génération n'a pas de rapport associé : rien ne peut être publié."
        )
    report = db.scalar(
        select(Report).where(
            Report.organization_id == claim.organization_id,
            Report.id == claim.report_id,
        )
    )
    if report is None:
        raise ReportGenerationUnavailable("Rapport introuvable pour ce travail de génération.")
    version = db.scalar(
        select(AnalysisVersion).where(
            AnalysisVersion.organization_id == claim.organization_id,
            AnalysisVersion.id == claim.analysis_version_id,
        )
    )
    if version is None:
        raise ReportGenerationUnavailable("Version d'analyse introuvable pour ce rapport.")
    analysis = db.scalar(
        select(Analysis).where(
            Analysis.organization_id == claim.organization_id,
            Analysis.id == version.analysis_id,
        )
    )
    if analysis is None:
        raise ReportGenerationUnavailable("Analyse introuvable pour ce rapport.")
    organization_name = _organization_name(db, organization_id=claim.organization_id)

    prepared = generate_report_artifact(
        db,
        settings=settings,
        storage=storage,
        analysis=analysis,
        version=version,
        report_format=claim.report_format,
        report_id=report.id,
        organization_name=organization_name,
        options=claim.options,
    )

    storage_key = report_storage_key(
        organization_id=claim.organization_id, report_id=report.id, report_format=claim.report_format
    )
    try:
        storage.put_bytes(
            bucket=settings.document_clean_bucket,
            key=storage_key,
            payload=prepared.content,
            content_type=REPORT_CONTENT_TYPES[claim.report_format],
        )
    except ObjectStorageError as exc:
        # Le stockage est le seul composant dont la panne est passagère et vaut un
        # nouvel essai : un rapport rendu ne doit pas être perdu pour un MinIO qui
        # redémarre.
        raise ReportGenerationRetryable("report_storage_unavailable", str(exc)) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    complete_report_job(
        db,
        claim=claim,
        report_fields={
            "storage_key": storage_key,
            "sha256": file_sha256(prepared.content),
            "size_bytes": len(prepared.content),
            "content_type": REPORT_CONTENT_TYPES[claim.report_format],
            "verification_reference": prepared.reference,
            "signature": prepared.signature,
            "signature_key_id": prepared.signing_key_id,
            "signature_schema_version": SIGNATURE_SCHEMA_VERSION,
            "signed_result_sha256": version.result_sha256,
            "signed_rulebook_version": version.rulebook_version,
            "signed_engine_version": version.engine_version,
            # La date **signée** est celle du rendu, pas celle de l'écriture en base :
            # la vérification recompose la signature à partir de cette colonne, et
            # deux horodatages différents feraient échouer tout rapport honnête.
            "generated_at": prepared.generated_at,
        },
        duration_ms=duration_ms,
    )
    return GenerateReportResult(
        report_id=report.id,
        job_id=claim.id,
        storage_key=storage_key,
        sha256=file_sha256(prepared.content),
        size_bytes=len(prepared.content),
        verification_reference=prepared.reference,
        signature=prepared.signature,
        signature_key_id=prepared.signing_key_id,
        duration_ms=duration_ms,
    )


def _organization_name(db: Session, *, organization_id: UUID) -> str:
    from app.models.domain import Organization

    organization = db.get(Organization, organization_id)
    if organization is None or not organization.name:
        return "Organisation sans nom"
    return organization.name


def report_artifact_filename(
    *, version_number: int, report_format: str, generated_at: datetime | None = None
) -> str:
    moment = (generated_at or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M")
    if report_format == "pdf":
        return f"rapport-pre-audit-{version_number}-{moment}.pdf"
    return f"dossier-probatoire-{version_number}-{moment}.zip"


__all__ = [
    "ReportGenerationRetryable",
    "ReportGenerationUnavailable",
    "generate_report_artifact",
    "process_claimed_report_job",
    "report_artifact_filename",
    "report_storage_key",
]
