"""Pydantic schemas and domain models for Enterprise Industrialization (Chantier 9)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100)
    scopes: list[str] = Field(min_length=1)
    rate_limit_per_minute: int = Field(default=120, ge=10, le=10000)
    expires_in_days: int | None = Field(default=365, ge=1, le=730)


class ApiKeyCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    prefix: str
    raw_api_key: str  # Only returned once on creation
    scopes: list[str]
    rate_limit_per_minute: int
    expires_at: datetime | None
    created_at: datetime


class ApiKeySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    rate_limit_per_minute: int
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime


class EnterpriseMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uptime_seconds: float
    service_status: str
    #: Why the service reports this status, in plain words.
    status_reasons: list[str] = []
    database_status: str
    storage_status: str
    workers_status: str
    #: Not measurable by a tenant, therefore null and named in `not_measured`.
    active_tenants_count: int | None
    total_analyses_completed: int
    #: Average duration of *HTTP requests* measured by this process since start, not
    #: an "analysis latency". The previous field name promised something that was
    #: never measured; the value is now real and the field documents what it is.
    average_analysis_latency_ms: float | None
    total_api_requests: int
    error_rate_percent: float
    open_alerts_count: int
    memory_usage_mb: float | None
    cpu_utilization_percent: float | None
    #: Audit events of the requesting organization (tenant-scoped, unlike before).
    tenant_audit_events: int = 0
    #: Fields with no honest value on this instance. An empty list means everything
    #: below is measured.
    not_measured: list[str] = []
    #: Explicit scope of the process-level figures above.
    metrics_note: str = (
        "Latence, mémoire, CPU et débit sont mesurés dans ce processus et remis à zéro à "
        "chaque redémarrage ; ils ne sont pas agrégés entre réplicas. Les compteurs métier "
        "sont, eux, calculés depuis la base au moment de la lecture."
    )
    timestamp: datetime


class EnterpriseAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    severity: str  # "info" | "warning" | "critical"
    category: str  # "security" | "quota" | "sso" | "system"
    title: str
    message: str
    occurred_at: datetime
    is_acknowledged: bool


class ScimUserCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    userName: str
    externalId: str | None = None
    displayName: str | None = None
    emails: list[dict[str, Any]] = Field(default_factory=list)
    active: bool = True


class ScimUserResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schemas: list[str] = Field(default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:User"])
    id: str
    userName: str
    displayName: str | None = None
    active: bool
    emails: list[dict[str, Any]]
    meta: dict[str, Any]


class LegalHoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_reference: str
    reason: str
    expires_at: datetime | None = None


class LegalHoldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    case_reference: str
    reason: str
    is_active: bool
    created_by: str
    created_at: datetime
    expires_at: datetime | None
