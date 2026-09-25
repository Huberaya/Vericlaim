from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


ROLE_CODE_PATTERN = re.compile(r"^(owner|admin|analyst|viewer)$")
SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,98}[a-z0-9])$")


class RoleResponse(BaseModel):
    code: str
    name: str
    permissions: list[str]


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    status: str
    data_region: str


class MembershipResponse(BaseModel):
    id: UUID
    organization: OrganizationResponse
    role: RoleResponse
    status: str
    activated_at: datetime | None = None


class CurrentUserResponse(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str | None = None
    status: str


class AuthMeResponse(BaseModel):
    authenticated: bool = True
    user: CurrentUserResponse
    active_organization_id: UUID | None = None
    memberships: list[MembershipResponse] = Field(default_factory=list)


class AuthStatusResponse(BaseModel):
    oidc_configured: bool
    authentication_required: bool = True


class OrganizationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=255)
    slug: str | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Le nom de l’organisation doit contenir au moins deux caractères.")
        return normalized

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if not SLUG_PATTERN.fullmatch(normalized):
            raise ValueError("Le slug doit contenir 2 à 100 caractères minuscules, chiffres ou tirets.")
        return normalized


class OrganizationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=255)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Le nom de l’organisation doit contenir au moins deux caractères.")
        return normalized


class ActiveOrganizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    organization_id: UUID


class InviteMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role_code: str = Field(pattern=r"^(owner|admin|analyst|viewer)$")


class UpdateMembershipRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_code: str = Field(pattern=r"^(owner|admin|analyst|viewer)$")


class OrganizationMemberResponse(BaseModel):
    membership_id: UUID
    user: CurrentUserResponse
    role: RoleResponse
    status: str
    activated_at: datetime | None = None


class InvitationResponse(OrganizationMemberResponse):
    delivery_status: str = "not_sent"
    delivery_note: str = (
        "Invitation enregistrée. L’envoi d’e-mail est volontairement hors périmètre ; "
        "la personne sera activée lors de sa première connexion SSO avec cette adresse vérifiée."
    )
