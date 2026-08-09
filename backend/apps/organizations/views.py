from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from organizations.models import Branch, Organization, OrganizationMembership
from organizations.permissions import (
    HasOrganizationRole,
    IsOrganizationMember,
    OrganizationContextMixin,
)
from organizations.policies import role_allows
from organizations.serializers import (
    BranchSerializer,
    MeSerializer,
    MembershipSerializer,
    OrganizationProfileSerializer,
    OrganizationSerializer,
)
from organizations.services import update_membership
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
        self.get_object(request, branch_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MembershipListView(PathOrganizationView):
    required_action = "manage_team"

    def get(self, request, organization_id):
        rows = OrganizationMembership.objects.filter(organization=request.organization).select_related("user")
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
        if requested_role == "owner" and not role_allows(
            request.organization_membership.role,
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
