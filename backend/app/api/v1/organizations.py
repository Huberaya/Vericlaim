from __future__ import annotations

import re
import unicodedata
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing.http import require_active_subscription
from app.api.v1.presenters import (
    present_membership,
    present_organization,
    present_organization_member,
)
from app.core.database import get_db
from app.identity.dependencies import (
    AuthenticatedPrincipal,
    TenantPrincipal,
    get_authenticated_principal,
    require_csrf_authenticated_principal,
    require_permission,
    request_id_from_request,
)
from app.identity.roles import SYSTEM_ROLES
from app.identity.service import (
    AuthorizationInvariantError,
    IdentityConflictError,
    IdentityNotFoundError,
    OrganizationMemberView,
    append_audit_event,
    create_organization,
    invite_or_provision_member,
    list_active_memberships,
    list_organization_members,
    revoke_member,
    update_member_role,
    update_organization_name,
)
from app.models.domain import Organization
from app.models.identity_schemas import (
    InvitationResponse,
    OrganizationCreateRequest,
    OrganizationMemberResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
    RoleResponse,
    UpdateMembershipRoleRequest,
    InviteMemberRequest,
    MembershipResponse,
)


router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])

# C13 — garde d'abonnement : cette route produit un artefact payant ou une
# dépense réelle (OCR, e-mail tiers, rapport signé). Voir app/billing/http.py.
SUBSCRIPTION_GATE = Depends(require_active_subscription())


def _slugify(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")[:100]
    if not slug:
        return "organisation"
    return slug if len(slug) >= 2 else f"{slug}-org"


def _raise_known_identity_error(exc: Exception) -> None:
    if isinstance(exc, IdentityNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, IdentityConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, AuthorizationInvariantError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    raise exc


def _member_view_from_mutation(
    principal: TenantPrincipal,
    membership,
    user,
    role,
) -> OrganizationMemberView:
    return OrganizationMemberView(
        membership=membership,
        organization=principal.membership_view.organization,
        role=role,
        user=user,
    )


@router.get("", response_model=list[MembershipResponse])
def list_my_organizations(
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    db: Session = Depends(get_db),
) -> list[MembershipResponse]:
    return [present_membership(view) for view in list_active_memberships(db, principal.user_id)]


@router.post("", response_model=MembershipResponse, status_code=status.HTTP_201_CREATED)
def create_my_organization(
    body: OrganizationCreateRequest,
    request: Request,
    principal: AuthenticatedPrincipal = Depends(require_csrf_authenticated_principal),
    db: Session = Depends(get_db),
) -> MembershipResponse:
    slug = body.slug or _slugify(body.name)
    try:
        membership = create_organization(db, current=principal.current, name=body.name, slug=slug)
        append_audit_event(
            db,
            organization_id=membership.organization.id,
            actor_user_id=principal.user_id,
            entity_type="organization",
            entity_id=membership.organization.id,
            action="organization.created",
            payload={"slug": membership.organization.slug},
            request_id=request_id_from_request(request),
        )
    except IntegrityError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ce slug d’organisation est déjà utilisé.") from exc
    return present_membership(membership)


@router.get("/current", response_model=OrganizationResponse)
def current_organization(
    principal: TenantPrincipal = Depends(require_permission("organization:read")),
) -> OrganizationResponse:
    return present_organization(principal.membership_view.organization)


@router.patch("/current", response_model=OrganizationResponse)
def update_current_organization(
    body: OrganizationUpdateRequest,
    request: Request,
    principal: TenantPrincipal = Depends(require_permission("organization:manage", csrf_protected=True)),
    db: Session = Depends(get_db),
) -> OrganizationResponse:
    organization = update_organization_name(db, principal.membership_view.organization, name=body.name)
    append_audit_event(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        entity_type="organization",
        entity_id=organization.id,
        action="organization.updated",
        payload={"changed_fields": ["name"]},
        request_id=request_id_from_request(request),
    )
    return present_organization(organization)


@router.get("/current/roles", response_model=list[RoleResponse])
def list_available_roles(
    principal: TenantPrincipal = Depends(require_permission("members:read")),
) -> list[RoleResponse]:
    return [
        RoleResponse(code=role.code, name=role.name, permissions=list(role.permissions))
        for role in SYSTEM_ROLES
    ]


@router.get("/current/members", response_model=list[OrganizationMemberResponse])
def members_of_current_organization(
    principal: TenantPrincipal = Depends(require_permission("members:read")),
    db: Session = Depends(get_db),
) -> list[OrganizationMemberResponse]:
    return [present_organization_member(view) for view in list_organization_members(db, principal.organization_id)]


@router.post("/current/members/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED, dependencies=[SUBSCRIPTION_GATE])
def invite_member(
    body: InviteMemberRequest,
    request: Request,
    principal: TenantPrincipal = Depends(require_permission("members:manage", csrf_protected=True)),
    db: Session = Depends(get_db),
) -> InvitationResponse:
    try:
        membership, user, role = invite_or_provision_member(
            db,
            organization_id=principal.organization_id,
            email=str(body.email),
            role_code=body.role_code,
            invited_by_user_id=principal.user_id,
            actor_role_code=principal.role_code,
        )
    except (IdentityNotFoundError, IdentityConflictError, AuthorizationInvariantError) as exc:
        _raise_known_identity_error(exc)
    # C14: the invitation e-mail the audit found missing. The delivery outcome is
    # reported to the inviter (status + note) and written to the audit trail: on an
    # instance without a mail transport the answer says "recorded, not delivered"
    # instead of letting the inviter believe the colleague was notified.
    from app.core.config import get_settings as _get_settings
    from app.identity.self_service import send_invitation_email

    organization = db.get(Organization, principal.organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organisation active introuvable : invitation non enregistrée.",
        )
    delivery = send_invitation_email(
        db,
        settings=_get_settings(),
        user=user,
        organization=organization,
        invited_by_user_id=principal.user_id,
        role_code=role.code,
    )
    invitation_delivery = {
        "status": delivery.status.value,
        "transport": delivery.transport,
        "message_id": str(delivery.message_id),
    }
    append_audit_event(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        entity_type="membership",
        entity_id=membership.id,
        action="membership.invited",
        payload={
            "target_user_id": str(user.id),
            "role_code": role.code,
            "invitation_delivery": invitation_delivery,
        },
        request_id=request_id_from_request(request),
    )
    return InvitationResponse(
        **present_organization_member(
            _member_view_from_mutation(principal, membership, user, role)
        ).model_dump(),
        delivery_status=delivery.status.value,
        delivery_note=delivery.detail,
    )


@router.patch("/current/members/{user_id}/role", response_model=OrganizationMemberResponse)
def change_member_role(
    user_id: UUID,
    body: UpdateMembershipRoleRequest,
    request: Request,
    principal: TenantPrincipal = Depends(require_permission("members:manage", csrf_protected=True)),
    db: Session = Depends(get_db),
) -> OrganizationMemberResponse:
    try:
        membership, user, role = update_member_role(
            db,
            organization_id=principal.organization_id,
            user_id=user_id,
            role_code=body.role_code,
            actor_role_code=principal.role_code,
        )
    except (IdentityNotFoundError, IdentityConflictError, AuthorizationInvariantError) as exc:
        _raise_known_identity_error(exc)
    append_audit_event(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        entity_type="membership",
        entity_id=membership.id,
        action="membership.role_changed",
        payload={"target_user_id": str(user.id), "role_code": role.code},
        request_id=request_id_from_request(request),
    )
    return present_organization_member(_member_view_from_mutation(principal, membership, user, role))


@router.delete("/current/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_organization_member(
    user_id: UUID,
    request: Request,
    principal: TenantPrincipal = Depends(require_permission("members:manage", csrf_protected=True)),
    db: Session = Depends(get_db),
) -> Response:
    try:
        membership, user, role = revoke_member(
            db,
            organization_id=principal.organization_id,
            user_id=user_id,
            actor_role_code=principal.role_code,
        )
    except (IdentityNotFoundError, IdentityConflictError, AuthorizationInvariantError) as exc:
        _raise_known_identity_error(exc)
    append_audit_event(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        entity_type="membership",
        entity_id=membership.id,
        action="membership.revoked",
        payload={"target_user_id": str(user.id), "former_role_code": role.code},
        request_id=request_id_from_request(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
