from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant_context.models import OrganizationAssistantProfile
from assistant_context.serializers import AssistantProfileSerializer
from channels.models import ChannelConnection
from organizations.models import (
    Branch,
    Organization,
    OrganizationInvitation,
    OrganizationMembership,
    OrganizationMembershipRole,
)
from organizations.permissions import (
    HasOrganizationRole,
    IsOrganizationMember,
    OrganizationContextMixin,
)
from organizations.policies import role_allows
from organizations.serializers import (
    BranchSerializer,
    InvitationCreateSerializer,
    InvitationSerializer,
    MeSerializer,
    MembershipSerializer,
    OrganizationProfileSerializer,
    OrganizationSerializer,
)
from organizations.services import create_invitation, update_membership
from core.api.pagination import StandardPagination


def paginated_response(request, queryset, serializer_class, view):
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request, view=view)
    return paginator.get_paginated_response(serializer_class(page, many=True).data)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(MeSerializer(request.user).data)


class OrganizationListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        organizations = Organization.objects.filter(
            memberships__user=request.user,
            memberships__status="active",
        ).distinct()
        return paginated_response(request, organizations, OrganizationSerializer, self)

    def post(self, request):
        serializer = OrganizationSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        organization = serializer.save()
        return Response(OrganizationSerializer(organization).data, status=status.HTTP_201_CREATED)


class PathOrganizationView(OrganizationContextMixin, APIView):
    expected_organization_kwarg = "organization_id"
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]


class OrganizationDetailView(PathOrganizationView):
    write_action = "manage_settings"

    def get(self, request, organization_id):
        return Response(OrganizationSerializer(request.organization).data)

    def patch(self, request, organization_id):
        serializer = OrganizationSerializer(request.organization, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class OrganizationProfileView(PathOrganizationView):
    write_action = "manage_settings"

    def get(self, request, organization_id):
        return Response(OrganizationProfileSerializer(request.organization.profile).data)

    def patch(self, request, organization_id):
        serializer = OrganizationProfileSerializer(
            request.organization.profile,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class BranchListCreateView(PathOrganizationView):
    write_action = "manage_settings"

    def get(self, request, organization_id):
        rows = Branch.objects.filter(organization=request.organization)
        return paginated_response(request, rows, BranchSerializer, self)

    def post(self, request, organization_id):
        serializer = BranchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        branch = serializer.save(organization=request.organization)
        return Response(BranchSerializer(branch).data, status=status.HTTP_201_CREATED)


class BranchDetailView(PathOrganizationView):
    write_action = "manage_settings"

    def get_object(self, request, branch_id):
        return get_object_or_404(Branch, pk=branch_id, organization=request.organization)

    def get(self, request, organization_id, branch_id):
        return Response(BranchSerializer(self.get_object(request, branch_id)).data)

    def patch(self, request, organization_id, branch_id):
        branch = self.get_object(request, branch_id)
        serializer = BranchSerializer(branch, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, organization_id, branch_id):
        branch = self.get_object(request, branch_id)
        branch.is_active = False
        branch.save(update_fields=["is_active", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembershipListView(PathOrganizationView):
    write_action = "manage_team"

    def get(self, request, organization_id):
        rows = OrganizationMembership.objects.filter(
            organization=request.organization,
        ).select_related("user").order_by("-created_at")
        return paginated_response(request, rows, MembershipSerializer, self)


class MembershipDetailView(PathOrganizationView):
    required_action = "manage_team"

    def patch(self, request, organization_id, membership_id):
        membership = get_object_or_404(
            OrganizationMembership,
            pk=membership_id,
            organization=request.organization,
        )
        requested_role = request.data.get("role")
        requested_status = request.data.get("status")
        actor_role = request.organization_membership.role
        if membership.role == OrganizationMembershipRole.OWNER and actor_role != OrganizationMembershipRole.OWNER:
            return Response(
                {"detail": "Only an owner can change another owner's membership.", "code": "owner_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if requested_role == "owner" and not role_allows(
            actor_role,
            "manage_ownership",
        ):
            return Response(
                {"detail": "Only an owner can grant ownership.", "code": "owner_required"},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = MembershipSerializer(membership, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            membership = update_membership(
                membership=membership,
                role=serializer.validated_data.get("role"),
                status=serializer.validated_data.get("status"),
            )
        except ValueError as exc:
            return Response(
                {"detail": str(exc), "code": "last_owner_required"},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(MembershipSerializer(membership).data)


class InvitationListCreateView(PathOrganizationView):
    write_action = "manage_team"

    def get(self, request, organization_id):
        rows = OrganizationInvitation.objects.filter(
            organization=request.organization,
        ).select_related("invited_by")
        return paginated_response(request, rows.order_by("-created_at"), InvitationSerializer, self)

    def post(self, request, organization_id):
        serializer = InvitationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data["role"]
        authority = {"viewer": 1, "agent": 2, "manager": 3, "admin": 4, "owner": 5}
        if authority[role] > authority[request.organization_membership.role]:
            return Response(
                {"detail": "You cannot grant a role above your own.", "code": "role_escalation_denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        email = serializer.validated_data["email"]
        if OrganizationInvitation.objects.filter(
            organization=request.organization,
            email=email,
            status="pending",
            expires_at__gt=timezone.now(),
        ).exists():
            return Response(
                {"detail": "A pending invitation already exists.", "code": "invitation_conflict"},
                status=status.HTTP_409_CONFLICT,
            )
        invitation, raw_token = create_invitation(
            organization=request.organization,
            email=email,
            role=role,
            invited_by=request.user,
        )
        payload = InvitationSerializer(invitation).data
        payload["delivery"] = "development_console" if settings.DEBUG else "not_configured"
        if settings.DEBUG:
            client_app_url = getattr(settings, "CLIENT_APP_URL", "http://localhost:3001").rstrip("/")
            locale = request.organization.default_language
            payload["invitation_url"] = f"{client_app_url}/{locale}/accept-invitation/{raw_token}"
        return Response(payload, status=status.HTTP_201_CREATED)


class InvitationDetailView(PathOrganizationView):
    write_action = "manage_team"

    def patch(self, request, organization_id, invitation_id):
        invitation = get_object_or_404(
            OrganizationInvitation,
            pk=invitation_id,
            organization=request.organization,
        )
        if request.data.get("status") != "revoked" or invitation.status != "pending":
            return Response(
                {"detail": "Only pending invitations can be revoked.", "code": "invalid_invitation_state"},
                status=status.HTTP_409_CONFLICT,
            )
        invitation.status = "revoked"
        invitation.save(update_fields=["status", "updated_at"])
        return Response(InvitationSerializer(invitation).data)


class OrganizationOverviewView(PathOrganizationView):
    def get(self, request, organization_id):
        assistant = OrganizationAssistantProfile.objects.filter(
            organization=request.organization,
        ).first()
        profile = request.organization.profile
        recent_activity = []
        if assistant:
            recent_activity = [
                {
                    "type": "assistant_context_published",
                    "version": revision.version,
                    "actor": revision.published_by.get_full_name()
                    or revision.published_by.email
                    or revision.published_by.username,
                    "at": revision.published_at,
                }
                for revision in assistant.revisions.select_related("published_by")[:5]
            ]
        return Response(
            {
                "onboarding_completion_percentage": profile.onboarding_completion_percentage,
                "onboarding_completed_at": profile.onboarding_completed_at,
                "branch_count": Branch.objects.filter(
                    organization=request.organization, is_active=True,
                ).count(),
                "active_member_count": OrganizationMembership.objects.filter(
                    organization=request.organization, status="active",
                ).count(),
                "configured_channel_count": ChannelConnection.objects.filter(
                    organization=request.organization,
                ).exclude(status__in=["draft", "disconnected"]).count(),
                "ai_context_status": assistant.status if assistant else "draft",
                "ai_context_version": assistant.version if assistant else 0,
                "recent_activity": recent_activity,
            }
        )


class OrganizationOnboardingView(PathOrganizationView):
    write_action = "manage_settings"

    def get(self, request, organization_id):
        assistant = OrganizationAssistantProfile.objects.filter(
            organization=request.organization,
        ).first()
        if assistant is None:
            assistant = OrganizationAssistantProfile(
                organization=request.organization,
                supported_languages=[request.organization.default_language],
                default_language=request.organization.default_language,
                updated_by=request.user,
            )
        return Response(
            {
                "organization": OrganizationSerializer(request.organization).data,
                "profile": OrganizationProfileSerializer(request.organization.profile).data,
                "assistant_context": AssistantProfileSerializer(assistant).data,
                "branches": BranchSerializer(
                    Branch.objects.filter(organization=request.organization), many=True,
                ).data,
            }
        )

    @transaction.atomic
    def patch(self, request, organization_id):
        organization_data = request.data.get("organization", {})
        profile_data = request.data.get("profile", {})
        assistant_data = request.data.get("assistant_context", {})
        branch_data = request.data.get("branch")
        step = request.data.get("step")

        if step is not None:
            try:
                step = int(step)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Step must be between 1 and 6.", "code": "invalid_step"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if step not in range(1, 7):
                return Response(
                    {"detail": "Step must be between 1 and 6.", "code": "invalid_step"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        organization_serializer = OrganizationSerializer(
            request.organization, data=organization_data, partial=True,
        )
        profile_serializer = OrganizationProfileSerializer(
            request.organization.profile, data=profile_data, partial=True,
        )
        assistant, _ = OrganizationAssistantProfile.objects.get_or_create(
            organization=request.organization,
            defaults={
                "supported_languages": [request.organization.default_language],
                "default_language": request.organization.default_language,
                "updated_by": request.user,
            },
        )
        assistant_serializer = AssistantProfileSerializer(
            assistant,
            data=assistant_data,
            partial=True,
            context={"request": request},
        )
        branch_serializer = BranchSerializer(data=branch_data) if branch_data else None
        organization_serializer.is_valid(raise_exception=True)
        profile_serializer.is_valid(raise_exception=True)
        assistant_serializer.is_valid(raise_exception=True)
        if branch_serializer:
            branch_serializer.is_valid(raise_exception=True)

        organization_serializer.save()
        profile = profile_serializer.save()
        assistant_serializer.save()
        if branch_serializer:
            branch_serializer.save(organization=request.organization)

        if step is not None:
            completed = sorted(set([*profile.onboarding_completed_steps, step]))
            profile.onboarding_completed_steps = completed
            profile.onboarding_current_step = min(6, step + 1)
            profile.onboarding_completion_percentage = min(100, round(len(completed) / 6 * 100))

        if request.data.get("complete") is True:
            assistant.refresh_from_db()
            missing = []
            required = {
                "organization.name": request.organization.name,
                "profile.public_business_name": profile.public_business_name,
                "assistant_context.business_summary": assistant.business_summary,
                "assistant_context.business_description": assistant.business_description,
                "assistant_context.products_services": assistant.products_services,
                "assistant_context.introduction": assistant.introduction,
                "assistant_context.fallback_response": assistant.fallback_response,
            }
            missing.extend(key for key, value in required.items() if not str(value).strip())
            if not Branch.objects.filter(organization=request.organization, is_active=True).exists():
                missing.append("branch")
            if missing:
                transaction.set_rollback(True)
                return Response(
                    {
                        "detail": "Onboarding is incomplete.",
                        "code": "onboarding_incomplete",
                        "fields": missing,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            profile.onboarding_completed_steps = [1, 2, 3, 4, 5, 6]
            profile.onboarding_current_step = 6
            profile.onboarding_completion_percentage = 100
            profile.onboarding_completed_at = timezone.now()

        profile.save(
            update_fields=[
                "onboarding_completed_steps", "onboarding_current_step",
                "onboarding_completion_percentage", "onboarding_completed_at", "updated_at",
            ]
        )
        return self.get(request, organization_id)
