from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from organizations.models import (
    Organization,
    OrganizationInvitation,
    OrganizationInvitationStatus,
    OrganizationMembership,
    OrganizationMembershipRole,
    OrganizationMembershipStatus,
    OrganizationProfile,
)


@transaction.atomic
def create_organization(*, creator, name: str, slug: str, **fields) -> Organization:
    organization = Organization.objects.create(name=name.strip(), slug=slug.strip().lower(), **fields)
    OrganizationMembership.objects.create(
        organization=organization,
        user=creator,
        role=OrganizationMembershipRole.OWNER,
        status=OrganizationMembershipStatus.ACTIVE,
        joined_at=timezone.now(),
    )
    OrganizationProfile.objects.create(
        organization=organization,
        public_business_name=organization.name,
        supported_languages=[organization.default_language],
    )
    return organization


def hash_invitation_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


@transaction.atomic
def create_invitation(*, organization, email: str, role: str, invited_by, expires_in=timedelta(days=7)):
    raw_token = secrets.token_urlsafe(32)
    invitation = OrganizationInvitation.objects.create(
        organization=organization,
        email=email.strip().lower(),
        role=role,
        token_hash=hash_invitation_token(raw_token),
        expires_at=timezone.now() + expires_in,
        invited_by=invited_by,
    )
    return invitation, raw_token


@transaction.atomic
def accept_invitation(*, raw_token: str, user=None):
    token_hash = hash_invitation_token(raw_token)
    invitation = OrganizationInvitation.objects.select_for_update().get(
        token_hash=token_hash,
        status=OrganizationInvitationStatus.PENDING,
    )
    if invitation.expires_at <= timezone.now():
        invitation.status = OrganizationInvitationStatus.EXPIRED
        invitation.save(update_fields=["status", "updated_at"])
        raise ValueError("Invitation has expired.")
    if user is None:
        User = get_user_model()
        user = User.objects.get(email__iexact=invitation.email)
    if user.email.strip().lower() != invitation.email:
        raise ValueError("Invitation email does not match the accepting user.")
    membership, _ = OrganizationMembership.objects.update_or_create(
        organization=invitation.organization,
        user=user,
        defaults={
            "role": invitation.role,
            "status": OrganizationMembershipStatus.ACTIVE,
            "joined_at": timezone.now(),
        },
    )
    invitation.status = OrganizationInvitationStatus.ACCEPTED
    invitation.accepted_by = user
    invitation.accepted_at = timezone.now()
    invitation.save(update_fields=["status", "accepted_by", "accepted_at", "updated_at"])
    return membership


@transaction.atomic
def update_membership(*, membership: OrganizationMembership, role=None, status=None):
    membership = OrganizationMembership.objects.select_for_update().get(pk=membership.pk)
    removing_owner = membership.role == OrganizationMembershipRole.OWNER and (
        (role is not None and role != OrganizationMembershipRole.OWNER)
        or (status is not None and status != OrganizationMembershipStatus.ACTIVE)
    )
    if removing_owner:
        active_owner_count = OrganizationMembership.objects.filter(
            organization=membership.organization,
            role=OrganizationMembershipRole.OWNER,
            status=OrganizationMembershipStatus.ACTIVE,
        ).count()
        if active_owner_count <= 1:
            raise ValueError("The last active owner cannot be removed or demoted.")
    if role is not None:
        membership.role = role
    if status is not None:
        membership.status = status
        if status == OrganizationMembershipStatus.ACTIVE and not membership.joined_at:
            membership.joined_at = timezone.now()
    membership.save(update_fields=["role", "status", "joined_at", "updated_at"])
    return membership
