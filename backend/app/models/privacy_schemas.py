"""Response models for the privacy surface (C20)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PurgeDecisionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: str
    entity_id: str
    action: str  # purge | keep
    reason: str
    expires_at: str | None = None
    #: objet(s) qu'une exécution réelle supprimerait (un essai à blanc ne supprime rien)
    planned_storage_objects: int = 0


class PurgeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    generated_at: str
    dry_run: bool
    policy_declared: bool
    purged_count: int
    kept_count: int
    #: ce qui a réellement été supprimé
    storage_objects_deleted: int
    rows_deleted: int
    #: ce qu'une exécution réelle supprimerait (essai à blanc : non nul, deleted = 0)
    storage_objects_planned: int
    decisions: list[PurgeDecisionOut]
    #: Durations declared by the organization that this code does not apply, with the
    #: reason. An empty list means everything declared here is enforced.
    not_enforced: list[dict[str, str]]
    #: C13 — la durée que l'offre couvre, et la durée réellement appliquée quand
    #: l'offre plafonne une déclaration plus longue.
    plan_retention: dict[str, Any] = {}


class ErasureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: str = Field(pattern="^(document|analysis)$")
    target_id: str
    reason: str = Field(min_length=3, max_length=500)


class ErasureReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: str
    scope: str
    target_id: str
    erased: bool
    reason: str
    manifest_sha256: str | None = None
    storage_objects_deleted: int
    rows_deleted: dict[str, int]


class RetentionEnforcementRead(BaseModel):
    """What the declared policy actually does, as opposed to what it says."""

    model_config = ConfigDict(extra="forbid")

    declared: dict[str, Any]
    enforced: list[dict[str, Any]]
    not_enforced: list[dict[str, str]]
    active_legal_holds: list[dict[str, Any]]
    #: C13 — ce que l'offre couvre et ce qu'elle plafonne, avec la raison.
    plan: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# C12 — demandes d'exercice des droits
# --------------------------------------------------------------------------- #


class RightsRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_type: str = Field(pattern="^(access|rectification|erasure|restriction|portability|objection)$")
    requester_email: str = Field(min_length=5, max_length=320)
    requester_name: str | None = Field(default=None, max_length=255)
    requester_is_member: bool = False
    details: str = Field(min_length=10, max_length=5000)
    #: date de réception si elle diffère de l'enregistrement (demande reçue par un autre
    #: canal). Le délai court à partir de cette date, jamais de la saisie.
    received_at: str | None = None


class RightsRequestExtend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=10, max_length=2000)


class RightsRequestClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: str = Field(pattern="^(granted|partially_granted|refused)$")
    #: ce qui a été remis : exigé pour tout accord (empreinte d'export, référence de manifeste)
    outcome_reference: str | None = Field(default=None, max_length=255)
    outcome_detail: str | None = Field(default=None, max_length=2000)
    refusal_reason: str | None = Field(default=None, max_length=2000)


class RightsRequestRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    request_type: str
    article: str
    requester_email: str
    requester_name: str | None
    requester_is_member: bool
    status: str
    received_at: str
    due_at: str
    extension_due_at: str | None
    extension_reason: str | None
    effective_due_at: str
    days_remaining: int
    overdue: bool
    #: Le délai d'origine a été dépassé, même si une prolongation régulière est en cours.
    initial_deadline_passed: bool
    outcome: str | None
    outcome_reference: str | None
    outcome_detail: str | None
    refusal_reason: str | None
    completed_at: str | None
    handled_by_user_id: str | None
    handling: dict[str, Any]


class RightsRequestListRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: list[RightsRequestRead]
    total: int
    open: int
    overdue: int
    #: Demandes ouvertes dont le **premier** délai a été dépassé (prolongation ou non).
    initial_deadline_missed: int
    response_window_days: int
    window_months: int
    extension_months: int


class RightHandlingRead(BaseModel):
    """Ce que le produit traite réellement, droit par droit (publié, pas promis)."""

    model_config = ConfigDict(extra="forbid")

    request_type: str
    article: str
    automated: bool
    route: str | None
    requires_permission: str
    note: str


class RightsProcedureRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: adresse à laquelle les demandes sont reçues ; vide = non configurée, et l'API le dit
    contact_email: str | None
    response_window_months: int
    extension_months: int
    handling: list[RightHandlingRead]
