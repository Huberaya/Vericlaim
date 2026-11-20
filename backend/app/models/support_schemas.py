"""Schémas du support (C15). ``extra="forbid"`` : un champ non déclaré casse le contrat."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.domain import SupportRequestCategory


class SupportRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: SupportRequestCategory
    subject: str = Field(min_length=3, max_length=255)
    message: str = Field(min_length=20, max_length=8000)
    #: écran d'où la demande est envoyée : le support reçoit le contexte réel
    screen: str | None = Field(default=None, max_length=64)
    #: dernier code d'erreur affiché, s'il y en avait un
    last_error_code: str | None = Field(default=None, max_length=128)


class SupportRequestRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    subject: str
    screen: str | None
    last_error_code: str | None
    requester_email: str
    plan_code: str
    first_response_hours: int
    first_response_due_at: datetime
    status: str
    resolution: str | None
    answered_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    overdue: bool
    hours_remaining: float


class SupportRequestListRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: list[SupportRequestRead]
    total: int
    open: int
    overdue: int


class SupportAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(min_length=10, max_length=8000)
    close: bool = True


class SupportSlaRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: str
    plan_name: str
    first_response_hours: int
    incident_response_hours: int


class SupportSlaRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[SupportSlaRow]
    statement: str
    exclusions: list[str]


class SupportErrorEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_code: str
    title: str
    explanation: str
    fix: str


class SupportStartEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    body: str


class SupportContactRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None
    note: str
    form_available: bool


class SupportCentreRead(BaseModel):
    """Le centre d'aide public : aucune donnée personnelle, aucun compte requis."""

    model_config = ConfigDict(extra="forbid")

    contact: SupportContactRead
    start_here: list[SupportStartEntry]
    frequent_errors: list[SupportErrorEntry]
    sla: SupportSlaRead
    honesty: str
    help_url: str
