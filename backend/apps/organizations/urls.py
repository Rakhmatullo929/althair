from django.urls import path

from organizations.views import (
    BranchDetailView,
    BranchListCreateView,
    MembershipDetailView,
    MembershipListView,
    OrganizationDetailView,
    OrganizationListCreateView,
    OrganizationProfileView,
)

urlpatterns = [
    path("", OrganizationListCreateView.as_view(), name="organization-list"),
    path("<uuid:organization_id>/", OrganizationDetailView.as_view(), name="organization-detail"),
    path("<uuid:organization_id>/profile/", OrganizationProfileView.as_view(), name="organization-profile"),
    path("<uuid:organization_id>/branches/", BranchListCreateView.as_view(), name="branch-list"),
    path("<uuid:organization_id>/branches/<uuid:branch_id>/", BranchDetailView.as_view(), name="branch-detail"),
    path("<uuid:organization_id>/memberships/", MembershipListView.as_view(), name="membership-list"),
    path("<uuid:organization_id>/memberships/<uuid:membership_id>/", MembershipDetailView.as_view(), name="membership-detail"),
]
