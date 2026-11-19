from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class PdfExportRequest(BaseModel):
    """Report request.

    ``evaluation_response`` used to be accepted here and rendered verbatim, which
    let a caller publish a VeriClaim-branded PDF asserting "COMPLIANT" for an
    analysis the server had judged non-compliant. The field is gone: verdicts are
    read from the persisted analysis and can no longer be supplied by a client.
    ``analysis_id`` is therefore mandatory.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    analysis_id: str = Field(
        description="Persistent analysis ID the report is rendered from.",
    )
    document_title: str = Field(
        default="Rapport d'Audit Pré-Réglementaire Allégations",
        description="User-facing title of the generated document.",
    )
    product_identifier: Optional[str] = Field(
        default=None,
        description="Target SKU or Product name.",
    )
    surface: Optional[str] = Field(
        default="packaging",
        description="Surface or media analyzed (packaging, online_store, advertisement, etc.).",
    )
    include_evidence_matrix: bool = Field(
        default=True,
        description="Whether to include the detailed evidence registry matrix table in the PDF.",
    )
    include_remediation_clauses: bool = Field(
        default=True,
        description="Whether to include suggested legal remediation clauses.",
    )


class ReportVerificationResponse(BaseModel):
    """Public verification result.

    Every field is a fact, and ``known`` is deliberately separated from
    ``signature_valid``: an unknown reference must never be read as a valid one.
    """

    model_config = ConfigDict(extra="forbid")

    known: bool
    reference: str
    signature_valid: bool
    analysis_unchanged: bool
    # None means "not checked", which is not the same as "matches".
    file_matches: Optional[bool] = None
    generated_at: Optional[datetime] = None
    report_format: Optional[str] = None
    rulebook_version: Optional[str] = None
    engine_version: Optional[str] = None
    result_sha256: Optional[str] = None
    signature_key_id: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(
        default_factory=lambda: [
            "La signature atteste la correspondance entre le document et une analyse persistée; "
            "elle ne certifie pas la véracité juridique des verdicts.",
            "La clé de signature appartient à l'éditeur: elle prouve l'origine côté serveur, pas "
            "une non-répudiation par un tiers indépendant.",
            "L'intégrité du fichier n'est vérifiée que si l'empreinte du fichier est fournie.",
        ]
    )


class ReportQueueRead(BaseModel):
    """État de la file de génération d'une organisation (C22).

    Publié à chaque mise en file : un client qui reçoit un 429 doit pouvoir lire
    la limite qu'il a atteinte, et un client qui attend doit pouvoir voir si la
    file avance.
    """

    model_config = ConfigDict(extra="forbid")

    pending: int
    running: int
    oldest_pending_seconds: Optional[int] = None
    pending_limit: int
    running_limit: int
    saturated: bool
    workers_note: str = (
        "La file est traitée par des workers dédiés : ajouter une réplique de "
        "`report-worker` augmente la capacité sans redéploiement du service d'API."
    )


class ReportQueuedResponse(BaseModel):
    """Réponse à une demande de rapport : un travail, pas un fichier.

    Le rapport n'est plus rendu dans la requête HTTP. Ce que le client reçoit ici
    est un identifiant de travail à interroger et une URL de téléchargement qui
    fonctionnera une fois le rapport prêt.
    """

    model_config = ConfigDict(extra="forbid")

    report_id: Optional[str] = None
    job_id: str
    report_format: str
    analysis_version_id: str
    report_version_number: int
    status: str = "queued"
    reused: bool
    queued_at: datetime
    job_url: str
    download_url: str
    queue: ReportQueueRead
    note: str = (
        "Le rendu a lieu dans un worker dédié, pas dans cette requête : interrogez job_url "
        "jusqu'à ce que le rapport soit prêt, puis appelez download_url. La référence de "
        "vérification est créée au moment du rendu et figure dans le document."
    )


class ReportJobRead(BaseModel):
    """État d'un travail de génération, lisible par le client qui l'a demandé."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    report_id: Optional[str] = None
    analysis_version_id: str
    report_format: str
    job_status: str
    report_status: Optional[str] = None
    attempt_count: int
    max_attempts: int
    available_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_ms: Optional[int] = None
    error_code: Optional[str] = None
    verification_reference: Optional[str] = None
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None
    content_type: Optional[str] = None
    download_available: bool
    download_url: Optional[str] = None


class DossierExportResponse(BaseModel):
    analysis_id: str
    archive_name: str
    sha256_checksum: str
    generated_at: str
    items_count: int
