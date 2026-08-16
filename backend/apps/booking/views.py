from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet

from booking.models import (
    Appointment,
    AppointmentHold,
    AppointmentReminder,
    BookableResource,
    BookableStaffProfile,
    BookingPolicy,
    PublicBookingProfile,
    ScheduleBreak,
    ScheduleException,
    Service,
    ServiceCategory,
    ServiceResourceRequirement,
    StaffBranchAssignment,
    StaffService,
    WaitlistEntry,
    WeeklyScheduleRule,
)
from crm.models import Contact, Conversation
from booking.serializers import (
    AppointmentActionSerializer,
    AppointmentCreateSerializer,
    AppointmentHoldSerializer,
    AppointmentSerializer,
    AvailabilityQuerySerializer,
    BookingPolicySerializer,
    HoldCreateSerializer,
    PublicBookingProfileSerializer,
    ReminderSerializer,
    ResourceSerializer,
    ScheduleBreakSerializer,
    ScheduleExceptionSerializer,
    ServiceCategorySerializer,
    ServiceResourceRequirementSerializer,
    ServiceSerializer,
    StaffBranchAssignmentSerializer,
    StaffSerializer,
    StaffServiceSerializer,
    WaitlistSerializer,
    WeeklyScheduleRuleSerializer,
)
from booking.services import AppointmentHoldService, AppointmentService, AvailabilityService, BookingError
from organizations.permissions import HasOrganizationRole, IsOrganizationMember, OrganizationContextMixin


def error_response(exc: BookingError):
    return Response(
        {"code": exc.code, "detail": exc.message, "details": exc.details},
        status=exc.status_code,
    )


class BookingBaseView(OrganizationContextMixin, APIView):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]


class TenantModelViewSet(OrganizationContextMixin, ModelViewSet):
    permission_classes = [IsAuthenticated, IsOrganizationMember, HasOrganizationRole]
    write_action = "manage_settings"

    def get_queryset(self):
        return self.queryset.for_organization(self.request.organization)


class ServiceCategoryViewSet(TenantModelViewSet):
    queryset = ServiceCategory.objects.all()
    serializer_class = ServiceCategorySerializer

    def perform_destroy(self, instance):
        instance.active = False
        instance.save(update_fields=["active", "updated_at"])


class ServiceViewSet(TenantModelViewSet):
    queryset = Service.objects.select_related("category").all()
    serializer_class = ServiceSerializer

    def perform_create(self, serializer):
        from billing.services import BillingError, EntitlementService

        try:
            EntitlementService(self.request.organization).require_capacity(
                "max_services", self.get_queryset().filter(active=True).count()
            )
        except BillingError as exc:
            raise ValidationError({"code": exc.code, "detail": exc.message, "details": exc.details}) from exc
        serializer.save()

    def perform_destroy(self, instance):
        instance.active = False
        instance.save(update_fields=["active", "updated_at"])


class StaffViewSet(TenantModelViewSet):
    queryset = BookableStaffProfile.objects.select_related("membership__user").all()
    serializer_class = StaffSerializer

    def perform_destroy(self, instance):
        instance.active = False
        instance.accepts_online_booking = False
        instance.save(update_fields=["active", "accepts_online_booking", "updated_at"])

    def perform_create(self, serializer):
        from billing.services import BillingError, EntitlementService

        current = self.get_queryset().filter(active=True).count()
        try:
            EntitlementService(self.request.organization).require_capacity("max_bookable_staff", current)
        except BillingError as exc:
            raise ValidationError({"code": exc.code, "detail": exc.message, "details": exc.details}) from exc
        serializer.save()


class StaffBranchAssignmentViewSet(TenantModelViewSet):
    queryset = StaffBranchAssignment.objects.select_related("branch", "staff_profile").all()
    serializer_class = StaffBranchAssignmentSerializer


class StaffServiceViewSet(TenantModelViewSet):
    queryset = StaffService.objects.select_related("service", "staff_profile").all()
    serializer_class = StaffServiceSerializer


class ResourceViewSet(TenantModelViewSet):
    queryset = BookableResource.objects.select_related("branch").all()
    serializer_class = ResourceSerializer

    def perform_create(self, serializer):
        from billing.services import BillingError, EntitlementService

        try:
            EntitlementService(self.request.organization).require_capacity(
                "max_resources", self.get_queryset().filter(active=True).count()
            )
        except BillingError as exc:
            raise ValidationError({"code": exc.code, "detail": exc.message, "details": exc.details}) from exc
        serializer.save()

    def perform_destroy(self, instance):
        instance.active = False
        instance.save(update_fields=["active", "updated_at"])


class ServiceResourceRequirementViewSet(TenantModelViewSet):
    queryset = ServiceResourceRequirement.objects.select_related("service", "specific_resource").all()
    serializer_class = ServiceResourceRequirementSerializer


class WeeklyScheduleRuleViewSet(TenantModelViewSet):
    queryset = WeeklyScheduleRule.objects.all()
    serializer_class = WeeklyScheduleRuleSerializer


class ScheduleBreakViewSet(TenantModelViewSet):
    queryset = ScheduleBreak.objects.all()
    serializer_class = ScheduleBreakSerializer


class ScheduleExceptionViewSet(TenantModelViewSet):
    queryset = ScheduleException.objects.order_by("starts_at", "id")
    serializer_class = ScheduleExceptionSerializer


class BookingPolicyViewSet(TenantModelViewSet):
    queryset = BookingPolicy.objects.select_related("branch", "service").all()
    serializer_class = BookingPolicySerializer


class PublicBookingProfileView(BookingBaseView):
    write_action = "manage_settings"

    def get(self, request):
        profile, _ = PublicBookingProfile.objects.get_or_create(organization=request.organization)
        return Response(PublicBookingProfileSerializer(profile, context={"request": request}).data)

    def patch(self, request):
        profile, _ = PublicBookingProfile.objects.get_or_create(organization=request.organization)
        if request.data.get("enabled") is True:
            from billing.services import BillingError, EntitlementService

            try:
                EntitlementService(request.organization).require("public_booking_page")
            except BillingError as exc:
                raise ValidationError({"code": exc.code, "detail": exc.message, "details": exc.details}) from exc
        serializer = PublicBookingProfileSerializer(
            profile, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class AvailabilityView(BookingBaseView):
    def get(self, request):
        serializer = AvailabilityQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        try:
            slots = AvailabilityService(request.organization).slots(**serializer.validated_data)
        except BookingError as exc:
            return error_response(exc)
        return Response({"results": [slot.as_dict() for slot in slots]})


class HoldCreateView(BookingBaseView):
    required_action = "operate"

    def post(self, request):
        serializer = HoldCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            hold, created = AppointmentHoldService.create(
                organization=request.organization,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
                created_by_type=AppointmentHold.CreatedByType.EMPLOYEE,
                **serializer.validated_data,
            )
        except BookingError as exc:
            return error_response(exc)
        return Response(
            AppointmentHoldSerializer(hold).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AppointmentListCreateView(BookingBaseView):
    write_action = "operate"

    def get(self, request):
        rows = Appointment.objects.for_organization(request.organization).select_related(
            "branch", "service", "staff_profile", "contact"
        ).prefetch_related("events", "resource_links")
        starts = request.query_params.get("starts_at__gte")
        ends = request.query_params.get("starts_at__lt")
        if starts:
            rows = rows.filter(starts_at__gte=starts)
        if ends:
            rows = rows.filter(starts_at__lt=ends)
        if request.query_params.get("status"):
            rows = rows.filter(status=request.query_params["status"])
        return Response({"results": AppointmentSerializer(rows[:500], many=True).data})

    def post(self, request):
        if not request.headers.get("Idempotency-Key"):
            return Response({"code": "idempotency_key_required"}, status=400)
        serializer = AppointmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        source_conversation_id = data.pop("source_conversation_id", None)
        source_conversation = None
        if source_conversation_id:
            source_conversation = Conversation.objects.for_organization(request.organization).filter(
                pk=source_conversation_id
            ).first()
            if not source_conversation:
                return Response({"code": "conversation_not_found"}, status=404)
        try:
            appointment, created, confirmation_token = AppointmentService.create_from_hold(
                organization=request.organization,
                idempotency_key=request.headers["Idempotency-Key"],
                created_by_membership=request.organization_membership,
                source_conversation=source_conversation,
                **data,
            )
        except BookingError as exc:
            return error_response(exc)
        data = AppointmentSerializer(appointment).data
        if confirmation_token:
            data["confirmation_token"] = confirmation_token
        return Response(data, status=201 if created else 200)


class AppointmentDetailView(BookingBaseView):
    def get(self, request, appointment_id):
        try:
            appointment = Appointment.objects.for_organization(request.organization).prefetch_related("events").get(
                pk=appointment_id
            )
        except Appointment.DoesNotExist:
            return Response({"code": "appointment_not_found"}, status=404)
        return Response(AppointmentSerializer(appointment).data)


class AppointmentActionView(BookingBaseView):
    required_action = "operate"

    def post(self, request, appointment_id, action):
        serializer = AppointmentActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            if action == "confirm":
                appointment, _ = AppointmentService.confirm(
                    organization=request.organization,
                    appointment_id=appointment_id,
                    actor_type="employee",
                    actor_membership=request.organization_membership,
                )
            elif action == "cancel":
                appointment, _ = AppointmentService.cancel(
                    organization=request.organization,
                    appointment_id=appointment_id,
                    reason=serializer.validated_data.get("reason", ""),
                    actor_type="employee",
                    actor_membership=request.organization_membership,
                )
            elif action == "reschedule":
                if "starts_at" not in serializer.validated_data:
                    return Response({"code": "starts_at_required"}, status=400)
                appointment = AppointmentService.reschedule(
                    organization=request.organization,
                    appointment_id=appointment_id,
                    starts_at=serializer.validated_data["starts_at"],
                    idempotency_key=request.headers.get("Idempotency-Key", ""),
                    actor_type="employee",
                    actor_membership=request.organization_membership,
                )
            elif action == "status":
                if "status" not in serializer.validated_data:
                    return Response({"code": "status_required"}, status=400)
                appointment = AppointmentService.set_status(
                    organization=request.organization,
                    appointment_id=appointment_id,
                    status=serializer.validated_data["status"],
                    actor_membership=request.organization_membership,
                )
            else:
                return Response({"code": "unknown_action"}, status=404)
        except BookingError as exc:
            return error_response(exc)
        return Response(AppointmentSerializer(appointment).data)


class WaitlistViewSet(TenantModelViewSet):
    queryset = WaitlistEntry.objects.select_related("branch", "service", "contact").order_by(
        "created_at", "id"
    )
    serializer_class = WaitlistSerializer
    write_action = "operate"


class ReminderListView(BookingBaseView):
    def get(self, request):
        rows = AppointmentReminder.objects.for_organization(request.organization).select_related("appointment")
        return Response({"results": ReminderSerializer(rows.order_by("scheduled_for")[:500], many=True).data})


class BookingDashboardView(BookingBaseView):
    def get(self, request):
        today = timezone.localdate()
        week_end = timezone.now() + timedelta(days=7)
        appointments = Appointment.objects.for_organization(request.organization)
        return Response({
            "today": appointments.filter(starts_at__date=today).count(),
            "next_seven_days": appointments.filter(starts_at__gte=timezone.now(), starts_at__lt=week_end).count(),
            "pending_confirmation": appointments.filter(status=Appointment.Status.PENDING_CONFIRMATION).count(),
            "waitlist": WaitlistEntry.objects.for_organization(request.organization).filter(status=WaitlistEntry.Status.ACTIVE).count(),
            "by_status": list(appointments.values("status").annotate(count=Count("id")).order_by("status")),
        })


class MyAppointmentsView(BookingBaseView):
    def get(self, request):
        contact_ids = Contact.objects.for_organization(request.organization).filter(
            identities__type="email", identities__normalized_value=request.user.email.lower()
        ).values_list("id", flat=True)
        rows = Appointment.objects.for_organization(request.organization).filter(contact_id__in=contact_ids)
        return Response({"results": AppointmentSerializer(rows, many=True).data})
