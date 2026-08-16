from django.urls import path

from booking.public_views import (
    PublicAppointmentActionView,
    PublicAppointmentDetailView,
    PublicAppointmentView,
    PublicAvailabilityView,
    PublicHoldView,
    PublicProfileView,
    PublicSessionView,
    PublicWaitlistView,
)

urlpatterns = [
    path("<str:public_key>/", PublicProfileView.as_view(), name="profile"),
    path("<str:public_key>/sessions/", PublicSessionView.as_view(), name="session"),
    path("<str:public_key>/availability/", PublicAvailabilityView.as_view(), name="availability"),
    path("<str:public_key>/holds/", PublicHoldView.as_view(), name="hold"),
    path("<str:public_key>/appointments/", PublicAppointmentView.as_view(), name="appointment_create"),
    path("<str:public_key>/appointments/<str:reference>/", PublicAppointmentDetailView.as_view(), name="appointment_detail"),
    path("<str:public_key>/appointments/<str:reference>/<str:action>/", PublicAppointmentActionView.as_view(), name="appointment_action"),
    path("<str:public_key>/waitlist/", PublicWaitlistView.as_view(), name="waitlist"),
]
