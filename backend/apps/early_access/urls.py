from django.urls import path

from early_access.views import EarlyAccessLeadView

urlpatterns = [path("early-access/", EarlyAccessLeadView.as_view(), name="early-access")]
