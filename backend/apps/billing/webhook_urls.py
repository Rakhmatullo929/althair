from django.urls import path

from billing.views import BillingWebhookView


urlpatterns = [
    path("billing/<str:provider>/", BillingWebhookView.as_view(), name="billing-webhook"),
]
