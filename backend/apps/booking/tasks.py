from celery import shared_task
from django.conf import settings
from django.utils import timezone

from booking.models import AppointmentHold, AppointmentReminder
from booking.services import deliver_due_reminder


@shared_task
def expire_booking_holds():
    return AppointmentHold.objects.filter(
        status=AppointmentHold.Status.ACTIVE,
        expires_at__lte=timezone.now(),
    ).update(status=AppointmentHold.Status.EXPIRED)


@shared_task
def deliver_booking_reminders(limit=200):
    if not settings.BOOKING_REMINDERS_ENABLE:
        return {'processed': 0, 'sent': 0}
    ids = list(
        AppointmentReminder.objects.filter(
            status__in=[AppointmentReminder.Status.SCHEDULED, AppointmentReminder.Status.FAILED],
            scheduled_for__lte=timezone.now(),
            attempt_count__lt=3,
        ).order_by('scheduled_for').values_list('id', flat=True)[:limit]
    )
    sent = 0
    for reminder_id in ids:
        _, delivered = deliver_due_reminder(reminder_id)
        sent += int(delivered)
    return {'processed': len(ids), 'sent': sent}
