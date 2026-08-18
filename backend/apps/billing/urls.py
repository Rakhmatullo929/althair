from django.urls import path

from billing.views import (
    BillingAccountView,
    BillingCancelView,
    BillingChangePreviewView,
    BillingChangeView,
    BillingCheckoutView,
    BillingEntitlementsView,
    BillingInvoiceDetailView,
    BillingInvoiceListView,
    BillingPlanListView,
    BillingResumeView,
    BillingSubscriptionView,
    BillingUsageView,
    BillingWalletTransactionListView,
    BillingWalletView,
)


urlpatterns = [
    path("account/", BillingAccountView.as_view(), name="account"),
    path("subscription/", BillingSubscriptionView.as_view(), name="subscription"),
    path("plans/", BillingPlanListView.as_view(), name="plans"),
    path("subscription/change-preview/", BillingChangePreviewView.as_view(), name="change-preview"),
    path("subscription/change/", BillingChangeView.as_view(), name="change"),
    path("subscription/cancel/", BillingCancelView.as_view(), name="cancel"),
    path("subscription/resume/", BillingResumeView.as_view(), name="resume"),
    path("usage/", BillingUsageView.as_view(), name="usage"),
    path("entitlements/", BillingEntitlementsView.as_view(), name="entitlements"),
    path("invoices/", BillingInvoiceListView.as_view(), name="invoices"),
    path("invoices/<uuid:invoice_id>/", BillingInvoiceDetailView.as_view(), name="invoice-detail"),
    path("checkout/", BillingCheckoutView.as_view(), name="checkout"),
    path("wallet/", BillingWalletView.as_view(), name="wallet"),
    path("wallet/transactions/", BillingWalletTransactionListView.as_view(), name="wallet-transactions"),
]
