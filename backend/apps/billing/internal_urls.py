from django.urls import path

from billing.internal_views import (
    InternalInvoiceActionView,
    InternalInvoiceListView,
    InternalPlanDetailView,
    InternalPlanListView,
    InternalPlanPublishView,
    InternalProviderEventListView,
    InternalSubscriptionDetailView,
    InternalSubscriptionGraceView,
    InternalSubscriptionGrantView,
    InternalSubscriptionListView,
    InternalUsageReconcileView,
    InternalUsageView,
    InternalWalletActionView,
    InternalWalletDetailView,
    InternalWalletExportView,
    InternalWalletListView,
)


urlpatterns = [
    path("plans/", InternalPlanListView.as_view(), name="plans"),
    path("plans/<uuid:plan_id>/", InternalPlanDetailView.as_view(), name="plan-detail"),
    path("plans/<uuid:plan_id>/publish/", InternalPlanPublishView.as_view(), name="plan-publish"),
    path("subscriptions/", InternalSubscriptionListView.as_view(), name="subscriptions"),
    path("subscriptions/<uuid:subscription_id>/", InternalSubscriptionDetailView.as_view(), name="subscription-detail"),
    path("subscriptions/<uuid:subscription_id>/grant/", InternalSubscriptionGrantView.as_view(), name="subscription-grant"),
    path("subscriptions/<uuid:subscription_id>/extend-grace/", InternalSubscriptionGraceView.as_view(), name="subscription-grace"),
    path("invoices/", InternalInvoiceListView.as_view(), name="invoices"),
    path("invoices/<uuid:invoice_id>/<str:action>/", InternalInvoiceActionView.as_view(), name="invoice-action"),
    path("usage/", InternalUsageView.as_view(), name="usage"),
    path("usage/reconcile/", InternalUsageReconcileView.as_view(), name="usage-reconcile"),
    path("provider-events/", InternalProviderEventListView.as_view(), name="provider-events"),
    path("wallets/", InternalWalletListView.as_view(), name="wallets"),
    path("wallets/<uuid:wallet_id>/", InternalWalletDetailView.as_view(), name="wallet-detail"),
    path("wallets/<uuid:wallet_id>/export/", InternalWalletExportView.as_view(), name="wallet-export"),
    path("wallets/<uuid:wallet_id>/<str:action>/", InternalWalletActionView.as_view(), name="wallet-action"),
]
