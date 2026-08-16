from django.urls import include, path
from rest_framework.routers import SimpleRouter

from booking.views import (
    AppointmentActionView,
    AppointmentDetailView,
    AppointmentListCreateView,
    AvailabilityView,
    BookingDashboardView,
    BookingPolicyViewSet,
    HoldCreateView,
    MyAppointmentsView,
    PublicBookingProfileView,
    ReminderListView,
    ResourceViewSet,
    ScheduleBreakViewSet,
    ScheduleExceptionViewSet,
    ServiceCategoryViewSet,
    ServiceResourceRequirementViewSet,
    ServiceViewSet,
    StaffBranchAssignmentViewSet,
    StaffServiceViewSet,
    StaffViewSet,
    WaitlistViewSet,
    WeeklyScheduleRuleViewSet,
)

router = SimpleRouter()
router.register("categories", ServiceCategoryViewSet, basename="category")
router.register("services", ServiceViewSet, basename="service")
router.register("staff", StaffViewSet, basename="staff")
router.register("staff-branches", StaffBranchAssignmentViewSet, basename="staff-branch")
router.register("staff-services", StaffServiceViewSet, basename="staff-service")
router.register("resources", ResourceViewSet, basename="resource")
router.register("resource-requirements", ServiceResourceRequirementViewSet, basename="resource-requirement")
router.register("schedule-rules", WeeklyScheduleRuleViewSet, basename="schedule-rule")
router.register("schedule-breaks", ScheduleBreakViewSet, basename="schedule-break")
router.register("schedule-exceptions", ScheduleExceptionViewSet, basename="schedule-exception")
router.register("policies", BookingPolicyViewSet, basename="policy")
router.register("waitlist", WaitlistViewSet, basename="waitlist")

urlpatterns = [
    path("", include(router.urls)),
    path("dashboard/", BookingDashboardView.as_view(), name="dashboard"),
    path("public-profile/", PublicBookingProfileView.as_view(), name="public_profile"),
    path("availability/", AvailabilityView.as_view(), name="availability"),
    path("holds/", HoldCreateView.as_view(), name="hold_create"),
    path("appointments/", AppointmentListCreateView.as_view(), name="appointment_list"),
    path("appointments/<uuid:appointment_id>/", AppointmentDetailView.as_view(), name="appointment_detail"),
    path("appointments/<uuid:appointment_id>/<str:action>/", AppointmentActionView.as_view(), name="appointment_action"),
    path("reminders/", ReminderListView.as_view(), name="reminder_list"),
    path("my-appointments/", MyAppointmentsView.as_view(), name="my_appointments"),
]
