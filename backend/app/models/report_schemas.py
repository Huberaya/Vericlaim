from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.models.schemas import EvaluationResponse


class PdfExportRequest(BaseModel):
    evaluation_response: Optional[EvaluationResponse] = Field(
        default=None,
        description="Structured evaluation response if generating from a live interactive audit.",
    )
    analysis_id: Optional[str] = Field(
        default=None,
        description="Persistent analysis ID if generating from a persisted analysis record.",
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


class DossierExportResponse(BaseModel):
    analysis_id: str
    archive_name: str
    sha256_checksum: str
    generated_at: str
    items_count: int
