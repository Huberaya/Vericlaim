from __future__ import annotations

import re
import unicodedata
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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


@router.post("/current/members/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
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
    append_audit_event(
        db,
        organization_id=principal.organization_id,
        actor_user_id=principal.user_id,
        entity_type="membership",
        entity_id=membership.id,
        action="membership.invited",
        payload={"target_user_id": str(user.id), "role_code": role.code},
        request_id=request_id_from_request(request),
    )
    return InvitationResponse(**present_organization_member(_member_view_from_mutation(principal, membership, user, role)).model_dump())


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
