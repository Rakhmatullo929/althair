from django.urls import path

from booking.internal_views import InternalBookingOrganizationView, InternalBookingOverviewView


urlpatterns = [
    path("overview/", InternalBookingOverviewView.as_view(), name="overview"),
    path("organizations/<uuid:organization_id>/", InternalBookingOrganizationView.as_view(), name="organization"),
]
