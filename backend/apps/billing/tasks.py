from celery import shared_task
from django.utils import timezone

from billing.models import ScheduledSubscriptionChange, Subscription
from billing.services import apply_scheduled_change, expire_grace, payment_failed


@shared_task
def process_billing_lifecycle():
    now = timezone.now()
    counts = {"trial_expired": 0, "grace_expired": 0, "changes_applied": 0}
    for subscription in Subscription.objects.filter(status=Subscription.Status.TRIALING, trial_ends_at__lte=now)[:500]:
        payment_failed(subscription, failure_code="trial_ended_without_payment")
        counts["trial_expired"] += 1
    for subscription in Subscription.objects.filter(status=Subscription.Status.GRACE, grace_ends_at__lte=now)[:500]:
        expire_grace(subscription, at=now)
        counts["grace_expired"] += 1
    for change in ScheduledSubscriptionChange.objects.filter(
        status=ScheduledSubscriptionChange.Status.SCHEDULED, effective_at__lte=now
    ).select_related("subscription", "target_plan", "target_price")[:500]:
        apply_scheduled_change(change, at=now)
        counts["changes_applied"] += 1
    return counts
