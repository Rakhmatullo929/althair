from django.conf import settings
from django.db.models import Count
from django.http import Http404
from rest_framework.response import Response
from rest_framework.views import APIView

from booking.models import Appointment, AppointmentHold, AppointmentReminder, WaitlistEntry
from control_plane.authentication import PlatformSessionAuthentication
from control_plane.permissions import HasPlatformAccess, HasPlatformPermission
from organizations.models import Organization


class InternalBookingBaseView(APIView):
    authentication_classes = [PlatformSessionAuthentication]
    permission_classes = [HasPlatformAccess, HasPlatformPermission]
    platform_permission = "organization.read"

    def initial(self, request, *args, **kwargs):
        if not (settings.CONTROL_PLANE_ENABLE or settings.TESTING):
            raise Http404
        return super().initial(request, *args, **kwargs)


class InternalBookingOverviewView(InternalBookingBaseView):
    def get(self, request):
        organizations = {
            str(row["id"]): {"organization_id": str(row["id"]), "organization_name": row["name"]}
            for row in Organization.objects.values("id", "name")
        }
        for row in Appointment.objects.values("organization_id").annotate(count=Count("id")):
            organizations[str(row["organization_id"])]["appointments"] = row["count"]
        for row in AppointmentReminder.objects.filter(status=AppointmentReminder.Status.FAILED).values(
            "organization_id"
        ).annotate(count=Count("id")):
            organizations[str(row["organization_id"])]["failed_reminders"] = row["count"]
        return Response({"results": list(organizations.values())})


class InternalBookingOrganizationView(InternalBookingBaseView):
    def get(self, request, organization_id):
        organization = Organization.objects.filter(pk=organization_id).first()
        if not organization:
            raise Http404
        appointments = Appointment.objects.for_organization(organization)
        return Response({
            "organization_id": str(organization.id),
            "appointments_total": appointments.count(),
            "appointments_by_status": list(
                appointments.values("status").annotate(count=Count("id")).order_by("status")
            ),
            "active_holds": AppointmentHold.objects.for_organization(organization).filter(
                status=AppointmentHold.Status.ACTIVE
            ).count(),
            "active_waitlist": WaitlistEntry.objects.for_organization(organization).filter(
                status=WaitlistEntry.Status.ACTIVE
            ).count(),
            "failed_reminders": list(
                AppointmentReminder.objects.for_organization(organization).filter(
                    status=AppointmentReminder.Status.FAILED
                ).values("last_error_code").annotate(count=Count("id")).order_by("last_error_code")
            ),
        })
