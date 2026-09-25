from __future__ import annotations

from app.identity.service import MembershipView, OrganizationMemberView
from app.models.domain import Organization, Role, User
from app.models.identity_schemas import (
    CurrentUserResponse,
    MembershipResponse,
    OrganizationMemberResponse,
    OrganizationResponse,
    RoleResponse,
)


def present_user(user: User) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        status=user.status.value,
    )


def present_role(role: Role) -> RoleResponse:
    return RoleResponse(code=role.code, name=role.name, permissions=list(role.permissions_json))


def present_organization(organization: Organization) -> OrganizationResponse:
    return OrganizationResponse(
        id=organization.id,
        name=organization.name,
        slug=organization.slug,
        status=organization.status.value,
        data_region=organization.data_region,
    )


def present_membership(view: MembershipView) -> MembershipResponse:
    return MembershipResponse(
        id=view.membership.id,
        organization=present_organization(view.organization),
        role=present_role(view.role),
        status=view.membership.status.value,
        activated_at=view.membership.activated_at,
    )


def present_organization_member(view: OrganizationMemberView) -> OrganizationMemberResponse:
    return OrganizationMemberResponse(
        membership_id=view.membership.id,
        user=present_user(view.user),
        role=present_role(view.role),
        status=view.membership.status.value,
        activated_at=view.membership.activated_at,
    )
