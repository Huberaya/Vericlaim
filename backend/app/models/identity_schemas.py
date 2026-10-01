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
    # Filled from the real delivery result (C14). Never hardcode "sent": an
    # instance with EMAIL_BACKEND=outbox records the message and delivers nothing.
    delivery_status: str = "inconnu"
    delivery_note: str = (
        "Statut de remise non calculé. EMAIL_BACKEND=smtp remet réellement le message ; "
        "EMAIL_BACKEND=outbox l’enregistre dans email_messages sans l’envoyer."
    )


# --------------------------------------------------------------------------- #
# C14 — self-service identity
# --------------------------------------------------------------------------- #


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    organization_name: str = Field(min_length=2, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("organization_name")
    @classmethod
    def strip_organization_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("Le nom de l'organisation doit contenir au moins 2 caractères.")
        return normalized


class PasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=512)


class PasswordResetConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=512)
    new_password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=16, max_length=512)
    password: str = Field(min_length=1, max_length=256)
    display_name: str | None = Field(default=None, max_length=255)


class SelfServiceMessageResponse(BaseModel):
    """Deliberately generic: the answer must not reveal whether an account exists."""

    status: str
    message: str
    email_recorded: bool = True


class SessionIssuedResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None = None
    active_organization_id: str | None = None
