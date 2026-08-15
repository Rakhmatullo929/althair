from django.contrib import admin

from billing.models import (
    BillingAccount,
    BillingNotification,
    BillingProviderEvent,
    FeatureDefinition,
    Invoice,
    InvoiceLine,
    PaymentAttempt,
    PlanPrice,
    ScheduledSubscriptionChange,
    Subscription,
    UsageAggregate,
    UsageEvent,
)


admin.site.register(
    [
        FeatureDefinition,
        PlanPrice,
        BillingAccount,
        Subscription,
        ScheduledSubscriptionChange,
        UsageEvent,
        UsageAggregate,
        Invoice,
        InvoiceLine,
        PaymentAttempt,
        BillingProviderEvent,
        BillingNotification,
    ]
)
